"""run_fund_sql 的 SQL 守卫（PLAN S5）：sqlglot 解析后只放行单条 SELECT。

设计要点：
- **执行的是从语法树重新生成的 SQL，不是用户原文**。注释（含 MySQL 的 `/*! ... */` 可执行注释）
  在重新生成时被丢弃，所以「注释绕过」没有可乘之机。
- 只允许：单条语句；根节点是 SELECT / UNION 等集合运算；不含 DML/DDL/SHOW/SET 等节点；
  不含 INTO / FOR UPDATE / 会话变量；表只能属于 `fund_data` 库；黑名单函数（读文件、睡眠、加锁、
  泄露账号信息）一律拒绝。
- 行数：用户没写 LIMIT 或写得比上限大时，改写成 `LIMIT 上限+1`（多取 1 行用来判断是否被截断）；
  写得比上限小的保持不变。
- 这一层是应用侧检查；数据库侧还有只读账号（只有 fund_data 的 SELECT 权限）、只读事务和服务端超时。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_SQL_CHARS = 5000
ALLOWED_DB = "fund_data"

# 这些节点类型出现在语法树的任何位置都拒绝（getattr：不同 sqlglot 版本里类名可能缺席）
_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Drop",
    "Create",
    "Alter",
    "AlterTable",
    "Command",
    "Merge",
    "TruncateTable",
    "Set",
    "SetItem",
    "Use",
    "Show",
    "Describe",
    "Grant",
    "Revoke",
    "Commit",
    "Rollback",
    "Transaction",
    "Into",
    "LoadData",
    "Copy",
    "Analyze",
    "Kill",
    "Parameter",  # @user_var（可以赋值，属于写会话状态）
    "SessionParameter",  # @@system_var
    "Placeholder",
    "Hint",  # /*+ MAX_EXECUTION_TIME(...) */ 可以覆盖服务端超时，SET_VAR(...) 可以改会话变量
)
_FORBIDDEN_NODES = tuple(
    t for n in _FORBIDDEN_NODE_NAMES if isinstance(t := getattr(exp, n, None), type)
)

# 读文件、睡眠/压测、加锁，以及泄露连接账号 / 库版本的函数
_FORBIDDEN_FUNCS = frozenset(
    {
        "load_file",
        "sleep",
        "benchmark",
        "get_lock",
        "release_lock",
        "release_all_locks",
        "is_free_lock",
        "is_used_lock",
        "master_pos_wait",
        "sys_eval",
        "sys_exec",
        "user",
        "current_user",
        "session_user",
        "system_user",
        "database",
        "schema",
        "version",
        "current_version",  # sqlglot 把 VERSION() 解析成 CurrentVersion
        "current_schema",  # DATABASE() / SCHEMA() 同理
        "current_database",
        "connection_id",
        "last_insert_id",
        "row_count",
    }
)

_INTO_FILE = re.compile(r"\binto\s+(?:outfile|dumpfile)\b", re.IGNORECASE)
_SET_OPS = tuple(
    t
    for n in ("SetOperation", "Union", "Intersect", "Except")
    if isinstance(t := getattr(exp, n, None), type)
)


class SqlGuardError(ValueError):
    """守卫拒绝了这条 SQL；message 可以直接回传给 Agent（说明原因和改法）。"""


@dataclass(frozen=True)
class GuardedSql:
    sql: str  # 实际执行的 SQL（由语法树重新生成，已带行数限制）
    tables: tuple[str, ...]  # 引用到的表（不含 CTE 别名），去重排序
    user_limit: int | None  # 用户自己写的 LIMIT（字面量整数），没写为 None
    max_rows: int  # 返回行数上限

    @property
    def fetch_rows(self) -> int:
        """最多从数据库取多少行：比上限多 1 行，用来判断结果是否被截断。"""
        return self.max_rows + 1

    def is_truncated(self, fetched: int) -> bool:
        """取回 fetched 行时，结果是否被截断（用户自己写的小 LIMIT 不算截断）。"""
        return fetched > self.max_rows


def _reject(msg: str) -> SqlGuardError:
    return SqlGuardError(msg)


def _limit_value(limit: exp.Limit) -> int:
    e = limit.args.get("expression")
    if isinstance(e, exp.Literal) and not e.is_string:
        try:
            return int(e.name)
        except ValueError:
            pass
    raise _reject("LIMIT 必须是整数字面量，例如 LIMIT 50")


def guard_sql(sql: str, *, max_rows: int = 200) -> GuardedSql:
    """校验并改写 SQL；不合规时抛 SqlGuardError。"""
    text = (sql or "").strip()
    if not text:
        raise _reject("SQL 为空")
    if len(text) > MAX_SQL_CHARS:
        raise _reject(f"SQL 太长（{len(text)} 字符，上限 {MAX_SQL_CHARS}）")
    if _INTO_FILE.search(text):
        raise _reject("禁止 SELECT ... INTO OUTFILE/DUMPFILE（只读查询，不能写文件）")

    try:
        parsed = sqlglot.parse(text, read="mysql")
    except SqlglotError as e:
        raise _reject(
            f"SQL 无法解析（语法错误或不支持的写法）: {str(e).splitlines()[0][:200]}"
        ) from e
    statements = [s for s in parsed if s is not None]
    if not statements:
        raise _reject("没有可执行的语句（只有注释或空语句）")
    if len(statements) > 1:
        raise _reject(f"只允许单条语句，检测到 {len(statements)} 条（不能用分号拼接多条 SQL）")

    root = statements[0]
    while isinstance(root, exp.Subquery):  # (SELECT ...) 外面套括号
        root = root.this
    if not isinstance(root, (exp.Select, *_SET_OPS)):
        raise _reject(f"只允许 SELECT 查询，收到 {type(root).__name__.upper()} 语句")

    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    tables: set[str] = set()
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise _reject(f"SQL 含被禁止的语法: {type(node).__name__}（只允许纯 SELECT 查询）")
        if isinstance(node, exp.Select) and (node.args.get("into") or node.args.get("locks")):
            raise _reject("禁止 SELECT ... INTO / FOR UPDATE / LOCK IN SHARE MODE")
        if isinstance(node, exp.Table):
            if node.catalog or (node.db and node.db.lower() != ALLOWED_DB):
                raise _reject(
                    f"只能访问 {ALLOWED_DB} 库中的表，不能访问 {node.db or node.catalog} "
                    "（含 information_schema、mysql、performance_schema、sys）"
                )
            if node.name and not (not node.db and node.name.lower() in cte_names):
                tables.add(node.name)
        if isinstance(node, exp.Func):
            names = {node.sql_name().lower()}
            if isinstance(node, exp.Anonymous):
                names.add(node.name.lower())  # 反引号包起来的函数名也按原名比较
            bad = names & _FORBIDDEN_FUNCS
            if bad:
                raise _reject(f"禁止使用函数 {sorted(bad)[0].upper()}()")

    user_limit: int | None = None
    limit = root.args.get("limit")
    if limit is not None:
        if not isinstance(limit, exp.Limit):  # 例如 FETCH 子句
            raise _reject("只支持 LIMIT 子句限制行数")
        user_limit = _limit_value(limit)
    effective = user_limit if user_limit is not None and user_limit <= max_rows else max_rows + 1
    root.set("limit", exp.Limit(expression=exp.Literal.number(effective)))

    # comments=False：注释一律丢弃（包括 /*! 可执行注释）
    safe_sql = root.sql(dialect="mysql", comments=False)
    return GuardedSql(safe_sql, tuple(sorted(tables)), user_limit, max_rows)

"""为压测批量准备账号：注册（已存在则登录）N 个用户，把令牌写到 ``loadtest/.secrets/users.json``（gitignored）。

账号名 ``perf_u001…``（用户名只能含字母数字下划线）；密码在首次创建时随机生成并保存在同一个文件里，
所以重复运行会登录已有账号并刷新令牌（JWT 默认 12 小时过期）。BCrypt 哈希有意放在压测之外做完，
避免注册请求污染压测时的 CPU。令牌不进入 reports/、日志或对话。
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

import httpx

OUT = Path(__file__).with_name(".secrets") / "users.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", default="http://127.0.0.1:8081")
    ap.add_argument("-n", type=int, default=100)
    a = ap.parse_args(argv)
    OUT.parent.mkdir(exist_ok=True)
    known = {u["username"]: u for u in json.loads(OUT.read_text("utf-8"))} if OUT.exists() else {}
    out = []
    with httpx.Client(base_url=a.backend, trust_env=False, timeout=30) as c:
        for i in range(1, a.n + 1):
            name = f"perf_u{i:03d}"
            pw = known.get(name, {}).get("password") or secrets.token_urlsafe(12)
            body = {"username": name, "password": pw}
            r = c.post("/api/auth/register", json=body)
            if r.status_code != 200:  # 已存在 → 登录
                r = c.post("/api/auth/login", json=body)
            r.raise_for_status()
            out.append({"username": name, "password": pw, "token": r.json()["data"]["token"]})
    OUT.write_text(json.dumps(out), encoding="utf-8", newline="\n")
    print(f"{len(out)} 个压测账号就绪 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

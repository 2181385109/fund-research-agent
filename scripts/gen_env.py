"""从 .env.example 生成 .env。

- 注释里标了 ``[secret]`` 且值为空的变量：随机生成 24 位字母数字串（LLM_API_KEY 除外）。
- LLM_API_KEY：从 ``--llm-key-file`` 读取并去掉首尾空白。
- 任何密钥值都不打印、不写日志；只输出被填充的变量名。
- 默认拒绝覆盖已存在的 .env（加 ``--force`` 才覆盖，旧值会丢失）。

用法：python scripts/gen_env.py --llm-key-file <path>
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALPHABET = string.ascii_letters + string.digits
SECRET_MARK = "[secret]"
LLM_KEY_VAR = "LLM_API_KEY"


def random_secret(length: int = 24) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def render(example_text: str, llm_key: str | None) -> tuple[str, list[str]]:
    """返回 (.env 文本, 被填充的变量名列表)。"""
    out: list[str] = []
    filled: list[str] = []
    secret_next = False
    for line in example_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            secret_next = secret_next or SECRET_MARK in stripped
            out.append(line)
            continue
        if "=" in stripped:
            name, _, value = stripped.partition("=")
            if name == LLM_KEY_VAR and not value:
                if llm_key:
                    line = f"{name}={llm_key}"
                    filled.append(name)
            elif secret_next and not value:
                line = f"{name}={random_secret()}"
                filled.append(name)
        secret_next = False
        out.append(line)
    return "\n".join(out) + "\n", filled


def _var_names(text: str) -> set[str]:
    return {
        line.strip().partition("=")[0]
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }


def add_missing(example_text: str, env_text: str) -> tuple[str, list[str]]:
    """把 .env.example 中有、现有 .env 中没有的变量（连同其注释）追加到末尾；已有的值不动。

    返回 (新 .env 文本, 追加的变量名列表)。LLM_API_KEY 缺失时只追加空值。
    """
    rendered, _ = render(example_text, None)
    existing = _var_names(env_text)
    block: list[str] = []
    added: list[str] = []
    comments: list[str] = []
    for line in rendered.splitlines():
        stripped = line.strip()
        if not stripped:
            comments = []
        elif stripped.startswith("#"):
            comments.append(line)
        else:
            name = stripped.partition("=")[0]
            if name not in existing:
                block.extend([*comments, line])
                added.append(name)
            comments = []
    if not added:
        return env_text, []
    base = env_text if env_text.endswith("\n") or not env_text else env_text + "\n"
    header = "\n# " + "-" * 40 + " 由 gen_env.py --add-missing 追加\n"
    return base + header + "\n".join(block) + "\n", added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--llm-key-file", type=Path, help="存放 DeepSeek API key 的文本文件")
    parser.add_argument("--example", type=Path, default=ROOT / ".env.example")
    parser.add_argument("--out", type=Path, default=ROOT / ".env")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 .env")
    parser.add_argument(
        "--add-missing",
        action="store_true",
        help="只把 .env.example 新增的变量追加到已有 .env（新 [secret] 变量随机生成），已有值不动",
    )
    args = parser.parse_args(argv)

    if args.add_missing:
        if not args.out.exists():
            print(f"{args.out.name} 不存在，先不带 --add-missing 生成", file=sys.stderr)
            return 1
        text, added = add_missing(
            args.example.read_text(encoding="utf-8"), args.out.read_text(encoding="utf-8")
        )
        if added:
            with args.out.open("w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        print(f"{args.out.name} 追加的变量：{', '.join(added) or '（无）'}")
        return 0

    if args.out.exists() and not args.force:
        print(f"{args.out.name} 已存在，未覆盖（需要时加 --force）", file=sys.stderr)
        return 1

    llm_key = None
    if args.llm_key_file:
        llm_key = args.llm_key_file.read_text(encoding="utf-8").strip()
        if not llm_key:
            print("LLM key 文件为空", file=sys.stderr)
            return 1

    text, filled = render(args.example.read_text(encoding="utf-8"), llm_key)
    with args.out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"已写入 {args.out.name}；填充的变量：{', '.join(filled)}")
    if llm_key is None:
        print(f"注意：未提供 --llm-key-file，{LLM_KEY_VAR} 为空", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""由 proto/ 生成 ai-service 的 Python 桩代码（S9）。

    python scripts/gen_proto.py            # 生成到 ai-service/src/fundagent/v1/
    python scripts/gen_proto.py --check    # 重新生成到临时目录并与已提交的文件比较；不一致返回 1（CI 用）

生成物（*_pb2.py、*_pb2_grpc.py）已提交到仓库，Docker 镜像和本机开发都不需要装 protoc。
需要 grpcio-tools（版本固定在 ai-service 的 dev 依赖里，保证生成结果可复现）。
Java 侧不用这个脚本：backend 的 pom 在构建时直接从同一份 proto 生成。
"""

from __future__ import annotations

import argparse
import filecmp
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "proto"
PROTO_FILES = ["fundagent/v1/ai_service.proto"]
OUT_DIR = ROOT / "ai-service" / "src"
PACKAGE_DIR = Path("fundagent") / "v1"


def generate(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"--python_out={out}",
        f"--grpc_python_out={out}",
        *PROTO_FILES,
    ]
    subprocess.run(cmd, check=True)
    for pkg in (out / "fundagent", out / PACKAGE_DIR):
        (pkg / "__init__.py").touch()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比较，不覆盖")
    args = ap.parse_args()
    if not args.check:
        generate(OUT_DIR)
        print(f"generated -> {OUT_DIR / PACKAGE_DIR}")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        generate(Path(tmp))
        bad = []
        for f in sorted((Path(tmp) / PACKAGE_DIR).glob("*.py")):
            committed = OUT_DIR / PACKAGE_DIR / f.name
            if not committed.exists() or not filecmp.cmp(f, committed, shallow=False):
                bad.append(f.name)
        if bad:
            print("生成物与已提交的不一致（改了 proto 后要重新运行 scripts/gen_proto.py）：", bad)
            return 1
    print("proto 生成物与已提交的一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""评测集校验入口（PLAN §5 S3）。实现见 reference/validate.py。

本机完整校验（需要 data/raw PDF 与 fund_data 库）：
    cd eval && .venv/Scripts/python validate_dataset.py --out
CI（没有 PDF 与数据库）：
    python validate_dataset.py --no-pdf --no-sql
"""

from reference.validate import main

if __name__ == "__main__":
    raise SystemExit(main())

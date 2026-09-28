import sys
from pathlib import Path

# scripts/ 不是包，把它加入 sys.path 以便 import security_scan / gen_env / llm_smoke
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""开发与回归数据目录。

大体积样本和运行产物不属于 Skill 源码。默认放在源码仓库同级的
``<repo-name>-data``，也可通过环境变量覆盖。
"""

import os
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SKILL_ROOT.parent
DEFAULT_DATA_ROOT = REPO_ROOT.parent / f"{REPO_ROOT.name}-data"
DATA_ROOT = Path(
    os.environ.get("YMB_STANDARDIZATION_DATA_ROOT", DEFAULT_DATA_ROOT)
).expanduser()
TESTDATA_ROOT = DATA_ROOT / "testdata"
TESTOUTPUT_ROOT = DATA_ROOT / "testoutput"
RAW_STATEMENT_ROOT = DATA_ROOT / "原始流水数据"

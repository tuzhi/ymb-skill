"""银行流水 Skill 的确定性运行时模块。"""

import sys
from pathlib import Path


_SKILL_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SKILL_ROOT.parent
for _candidate in (
    _SKILL_ROOT / "packages" / "ymb_standardization_core",
    _REPO_ROOT / "ymb-standardization-core" / "src",
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

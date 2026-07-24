"""Stage 1 标准化运行时适配层。

标准化内核位于仓库平级子项目 ymb-standardization-core；Skill 分发包内使用
packages/ymb_standardization_core。
"""

import sys
from pathlib import Path


_SKILL_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _candidate in (
    _SKILL_ROOT / "packages" / "ymb_standardization_core",
    _REPO_ROOT / "ymb-standardization-core" / "src",
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

from ymb_standardization_core.core import *  # noqa: F401,F403,E402

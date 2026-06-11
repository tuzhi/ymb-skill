"""兼容旧命令的标准化 CLI 入口。

标准化内核已迁到 packages/ymb_standardization_core，便于脱敏等其他项目直接复用。
"""

import sys
from pathlib import Path


_SKILL_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _candidate in (
    _SKILL_ROOT / "packages" / "ymb_standardization_core",
    _REPO_ROOT / "packages" / "ymb_standardization_core",
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

from ymb_standardization_core.core import *  # noqa: F401,F403,E402


if __name__ == "__main__":
    main()

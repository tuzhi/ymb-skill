"""兼容旧命令的标准化 CLI 入口。

标准化内核已迁到 standardization.core，便于脱敏等其他项目直接复用。
"""

from standardization.core import *  # noqa: F401,F403


if __name__ == "__main__":
    main()

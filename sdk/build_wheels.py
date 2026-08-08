#!/usr/bin/env python3
"""从现有标准化与 BI 源码构建两个可安装的 SDK Wheel。"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = REPO_ROOT / "dist" / "sdk"
SDK_VERSION = "1.0.0"

IGNORED_NAMES = {
    ".DS_Store",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_NAMES or name.endswith((".pyc", ".pyo", ".egg-info"))
    }


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=_ignore)


def _rewrite_python(root: Path, replacements: tuple[tuple[str, str], ...]) -> None:
    for path in root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        rewritten = content
        for old, new in replacements:
            rewritten = rewritten.replace(old, new)
        if rewritten != content:
            path.write_text(rewritten, encoding="utf-8")


def _write_setup(
    project_root: Path,
    *,
    distribution_name: str,
    description: str,
    dependencies: tuple[str, ...],
    package_data: dict[str, list[str]] | None = None,
) -> None:
    setup = f'''from setuptools import find_namespace_packages, setup

setup(
    name={distribution_name!r},
    version={SDK_VERSION!r},
    description={description!r},
    python_requires=">=3.11,<3.12",
    packages=find_namespace_packages(),
    include_package_data=True,
    package_data={package_data or {}!r},
    install_requires={list(dependencies)!r},
    zip_safe=False,
)
'''
    (project_root / "setup.py").write_text(setup, encoding="utf-8")
    (project_root / "pyproject.toml").write_text(
        '''[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
''',
        encoding="utf-8",
    )


def _build_wheel(project_root: Path, output_dir: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="wheel-output-") as temporary:
        temporary_output = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "--no-build-isolation",
                "--disable-pip-version-check",
                "--wheel-dir",
                str(temporary_output),
            ],
            cwd=project_root,
            check=True,
        )
        created = sorted(temporary_output.glob("*.whl"))
        if len(created) != 1:
            raise RuntimeError(f"未能唯一确定新 Wheel：{created}")
        target = output_dir / created[0].name
        shutil.copy2(created[0], target)
        return target


def _stage_standardization(stage: Path) -> None:
    source = REPO_ROOT / "bank-statement-standardization"
    package = stage / "ymb_statement_standardization"
    package.mkdir(parents=True)

    for name in ("runtime", "services", "harness"):
        _copy_tree(source / name, package / name)

    scripts = package / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").write_text(
        '"""标准化 SDK 的命令行兼容入口。"""\n',
        encoding="utf-8",
    )
    for name in ("orchestrator.py", "repair_coordinator.py"):
        shutil.copy2(source / "scripts" / name, scripts / name)

    _copy_tree(source / "assets", package / "assets")
    _copy_tree(source / "roles", package / "roles")
    references = package / "references"
    references.mkdir()
    for name in ("prompt-1-字段映射.md", "附件A-标准化字段说明.md"):
        shutil.copy2(source / "references" / name, references / name)

    _copy_tree(
        REPO_ROOT / "ymb-standardization-core" / "src" / "ymb_standardization_core",
        stage / "ymb_standardization_core",
    )

    _rewrite_python(
        package,
        (
            ("from runtime", "from ymb_statement_standardization.runtime"),
            ("from services", "from ymb_statement_standardization.services"),
            ("from harness", "from ymb_statement_standardization.harness"),
        ),
    )
    (package / "__init__.py").write_text(
        '''"""YMB 银行流水标准化 Python SDK。"""

from .services import (
    InputFile,
    RoutingRulesSnapshot,
    ServiceError,
    StandardizationRequest,
    StandardizationResult,
    StatementService,
    YamlRuleService,
)

__version__ = "1.0.0"

__all__ = [
    "InputFile",
    "RoutingRulesSnapshot",
    "ServiceError",
    "StandardizationRequest",
    "StandardizationResult",
    "StatementService",
    "YamlRuleService",
]
''',
        encoding="utf-8",
    )
    _write_setup(
        stage,
        distribution_name="ymb-statement-standardization-sdk",
        description="YMB 银行流水标准化同步 Python SDK。",
        dependencies=(
            "pandas>=2.2.3,<3",
            "openpyxl>=3.1.5,<4",
            "xlrd>=2.0.2,<3",
            "pdfplumber>=0.11.5,<0.12",
            "msoffcrypto-tool>=5.4,<6",
            "PyYAML>=6,<7",
        ),
        package_data={
            "ymb_statement_standardization": [
                "assets/*",
                "roles/*.md",
                "references/*.md",
                "harness/protocols/v1/*.json",
            ],
            "ymb_standardization_core": ["config/routing/*"],
        },
    )


def _stage_bi(stage: Path) -> None:
    source = REPO_ROOT / "bank-statement-bi-analysis"
    package = stage / "bank_statement_bi_analysis"
    _copy_tree(source / "bank_statement_bi_analysis", package)

    engine = package / "engine"
    engine.mkdir()
    (engine / "__init__.py").write_text(
        '"""BI V4 确定性计算引擎。"""\n',
        encoding="utf-8",
    )
    for name in ("build_bi_report_v3.py", "build_bi_report_v4.py", "generate_vars.py"):
        shutil.copy2(source / "scripts" / name, engine / name)

    _rewrite_python(
        engine,
        (
            ("from build_bi_report_v3 import", "from .build_bi_report_v3 import"),
            ("from generate_vars import", "from .generate_vars import"),
            (
                "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n",
                "",
            ),
        ),
    )

    service_path = package / "service.py"
    service = service_path.read_text(encoding="utf-8")
    service = service.replace(
        '''        self.script_dir = Path(
            script_dir or Path(__file__).resolve().parents[1] / "scripts"
        ).resolve()
''',
        '''        self.script_dir = Path(script_dir).resolve() if script_dir else None
''',
    )
    service = re.sub(
        r'''    def _engine\(self\) -> Any:\n        if str\(self\.script_dir\) not in sys\.path:\n            sys\.path\.insert\(0, str\(self\.script_dir\)\)\n        import build_bi_report_v4\n\n        return build_bi_report_v4\n''',
        '''    def _engine(self) -> Any:
        if self.script_dir is None:
            from .engine import build_bi_report_v4

            return build_bi_report_v4
        if str(self.script_dir) not in sys.path:
            sys.path.insert(0, str(self.script_dir))
        import build_bi_report_v4

        return build_bi_report_v4
''',
        service,
    )
    if "from .engine import build_bi_report_v4" not in service:
        raise RuntimeError("未能为 BI Service 注入包内 Engine 导入")
    service_path.write_text(service, encoding="utf-8")

    _write_setup(
        stage,
        distribution_name="ymb-bank-statement-bi-sdk",
        description="YMB 银行流水 BI V4 同步 Python SDK。",
        dependencies=(
            "openpyxl>=3.1.5,<4",
            "pandas>=2.2.3,<3",
            "numpy>=1.26,<3",
        ),
    )


def build_wheels(output_dir: Path = DEFAULT_DIST) -> tuple[Path, Path]:
    """构建标准化和 BI 两个 Wheel，并返回产物路径。"""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ymb-sdk-build-") as temporary:
        root = Path(temporary)
        standardization = root / "standardization"
        bi = root / "bi"
        standardization.mkdir()
        bi.mkdir()
        _stage_standardization(standardization)
        _stage_bi(bi)
        standardization_wheel = _build_wheel(standardization, output_dir)
        bi_wheel = _build_wheel(bi, output_dir)
    return standardization_wheel, bi_wheel


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 YMB 流水分析的两个 SDK Wheel")
    parser.add_argument("--output-dir", default=str(DEFAULT_DIST))
    args = parser.parse_args()
    for wheel in build_wheels(Path(args.output_dir)):
        print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

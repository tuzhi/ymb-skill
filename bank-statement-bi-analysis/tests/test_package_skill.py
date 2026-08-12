import importlib.util
import tempfile
from pathlib import Path
import zipfile


BI_ROOT = Path(__file__).resolve().parents[1]
PACKAGER = BI_ROOT / "scripts" / "package_skill.py"


def load_packager():
    spec = importlib.util.spec_from_file_location("bi_package_skill", PACKAGER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_platform_packages_render_one_thin_entry():
    packager = load_packager()
    with tempfile.TemporaryDirectory() as tmp:
        for platform in packager.SUPPORTED_PLATFORMS:
            archive = Path(tmp) / f"{platform}.zip"
            packager.package_skill(BI_ROOT, output=archive, platform=platform)
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                skill = bundle.read(
                    "bank-statement-bi-analysis/SKILL.md"
                ).decode("utf-8")

            assert skill.count("!`") == 1
            assert "$ARGUMENTS" in skill
            assert "{{PLATFORM_COMMAND}}" not in skill
            assert "scripts/build_bi_report_v4.py" in skill.replace("\\", "/")
            assert "bank-statement-bi-analysis/scripts/build_bi_report_v4.py" in names
            if platform == "macos":
                assert "bank-statement-bi-analysis/scripts/run-posix.sh" in names
                assert "bank-statement-bi-analysis/scripts/run-windows.cmd" not in names
            else:
                assert "bank-statement-bi-analysis/scripts/run-windows.cmd" in names
                assert "bank-statement-bi-analysis/scripts/run-posix.sh" not in names

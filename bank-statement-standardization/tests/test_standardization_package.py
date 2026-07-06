import importlib
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SCRIPTS = ROOT / "scripts"
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StandardizationPackageTest(unittest.TestCase):
    def test_core_package_and_legacy_cli_expose_same_standardize_function(self):
        core = importlib.import_module("ymb_standardization_core.core")
        legacy = load_module("standardize_legacy", SCRIPTS / "standardize.py")

        self.assertTrue(callable(core.standardize))
        self.assertIs(legacy.standardize, core.standardize)


if __name__ == "__main__":
    unittest.main()

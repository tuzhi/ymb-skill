import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
CORE_PACKAGE = REPO_ROOT / "ymb-standardization-core" / "src"
if str(CORE_PACKAGE) not in sys.path:
    sys.path.insert(0, str(CORE_PACKAGE))

from ymb_standardization_core.readers.routing.rule_loader import (  # noqa: E402
    clear_route_rule_cache,
    load_excel_route_rules,
    load_pdf_route_rules,
)


class RouteRuleCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_route_rule_cache()

    def test_route_rules_are_reused_until_cache_is_cleared(self):
        pdf_first = load_pdf_route_rules()
        excel_first = load_excel_route_rules()

        self.assertIs(pdf_first, load_pdf_route_rules())
        self.assertIs(excel_first, load_excel_route_rules())

        clear_route_rule_cache()
        pdf_reloaded = load_pdf_route_rules()
        excel_reloaded = load_excel_route_rules()

        self.assertIsNot(pdf_first, pdf_reloaded)
        self.assertIsNot(excel_first, excel_reloaded)
        self.assertEqual([rule.id for rule in pdf_first], [rule.id for rule in pdf_reloaded])
        self.assertEqual([rule.id for rule in excel_first], [rule.id for rule in excel_reloaded])


if __name__ == "__main__":
    unittest.main()

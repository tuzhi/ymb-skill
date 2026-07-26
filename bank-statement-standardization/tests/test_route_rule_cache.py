from concurrent.futures import ThreadPoolExecutor
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
    load_routing_rules_snapshot,
)


class RouteRuleCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_route_rule_cache()

    def test_route_rules_are_reused_until_cache_is_cleared(self):
        pdf_first = load_pdf_route_rules()
        excel_first = load_excel_route_rules()
        snapshot = load_routing_rules_snapshot()

        self.assertIs(pdf_first, load_pdf_route_rules())
        self.assertIs(excel_first, load_excel_route_rules())
        self.assertIs(pdf_first, snapshot.pdf_rules)
        self.assertIs(excel_first, snapshot.excel_rules)

        clear_route_rule_cache()
        pdf_reloaded = load_pdf_route_rules()
        excel_reloaded = load_excel_route_rules()

        self.assertIsNot(pdf_first, pdf_reloaded)
        self.assertIsNot(excel_first, excel_reloaded)
        self.assertEqual([rule.id for rule in pdf_first], [rule.id for rule in pdf_reloaded])
        self.assertEqual([rule.id for rule in excel_first], [rule.id for rule in excel_reloaded])

    def test_concurrent_first_load_installs_one_complete_snapshot(self):
        clear_route_rule_cache()
        with ThreadPoolExecutor(max_workers=8) as executor:
            snapshots = list(
                executor.map(lambda _: load_routing_rules_snapshot(), range(16))
            )

        installed = snapshots[0]
        self.assertTrue(all(snapshot is installed for snapshot in snapshots))
        self.assertTrue(installed.pdf_rules)
        self.assertTrue(installed.excel_rules)
        self.assertIs(load_pdf_route_rules(), installed.pdf_rules)
        self.assertIs(load_excel_route_rules(), installed.excel_rules)


if __name__ == "__main__":
    unittest.main()

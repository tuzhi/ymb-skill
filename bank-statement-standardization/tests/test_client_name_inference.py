import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


orchestrator = load_module("orchestrator", SKILL_ROOT / "scripts" / "orchestrator.py")
package_deliverable = load_module("package_deliverable", SKILL_ROOT / "scripts" / "package_deliverable.py")


class ClientNameInferenceTest(unittest.TestCase):
    def write_standardized(self, work, filename, rows):
        path = Path(work) / filename
        fieldnames = ["本方名称", "本方账户", "账户余额", "来源文件名"]
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_orchestrator_joins_multiple_short_ambiguous_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.write_standardized(
                work,
                "sample__standardized.csv",
                [
                    {"本方名称": "张运贞", "本方账户": "62220001", "账户余额": "100.00", "来源文件名": "a.csv"},
                    {"本方名称": "江西省鹏达石业有限公司", "本方账户": "360501", "账户余额": "200.00", "来源文件名": "b.csv"},
                ],
            )

            client, ranked = orchestrator.infer_unique_client_name([str(work)])

            self.assertEqual(client, "张运贞_江西省鹏达石业有限公司")
            self.assertEqual([row["name"] for row in ranked], ["张运贞", "江西省鹏达石业有限公司"])

    def test_package_deliverable_joins_multiple_short_ambiguous_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.write_standardized(
                work,
                "sample__standardized.csv",
                [
                    {"本方名称": "张运贞", "本方账户": "62220001", "账户余额": "100.00", "来源文件名": "a.csv"},
                    {"本方名称": "江西省鹏达石业有限公司", "本方账户": "360501", "账户余额": "200.00", "来源文件名": "b.csv"},
                ],
            )

            client = package_deliverable._infer_unique_client_name(str(work))

            self.assertEqual(client, "张运贞_江西省鹏达石业有限公司")


if __name__ == "__main__":
    unittest.main()

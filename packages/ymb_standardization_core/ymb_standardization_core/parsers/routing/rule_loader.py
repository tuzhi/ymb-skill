from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PdfRouteRule:
    parser: str
    file_type: str
    bank: str
    version: str
    identity_any: list
    layout_all: list

    def match(self, text):
        identity_hits = [marker for marker in self.identity_any if marker in text]
        if not identity_hits:
            return None

        layout_hits = [marker for marker in self.layout_all if marker in text]
        if len(layout_hits) != len(self.layout_all):
            return None

        return {
            "identity_evidence": identity_hits,
            "layout_evidence": layout_hits,
        }


def _load_yaml(name):
    with (Path(__file__).resolve().parent / name).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def load_pdf_route_rules():
    rules = []
    for item in _load_yaml("pdf_rules.yaml"):
        rules.append(PdfRouteRule(
            parser=item["parser"],
            file_type=item.get("file_type", "pdf"),
            bank=item["bank"],
            version=str(item["version"]),
            identity_any=item.get("identity", {}).get("any", []),
            layout_all=item.get("layout", {}).get("all", []),
        ))
    return rules

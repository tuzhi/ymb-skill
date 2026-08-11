"""流水线阶段间的常量与契约转换函数。"""


YAML_ROUTE_REQUIRED_FIELDS = (
    "fingerprint_id",
    "series_family",
    "router_bank",
    "yaml_match_status",
)

YAML_ROUTE_FIELDS = (
    *YAML_ROUTE_REQUIRED_FIELDS[:-1],
    "reader_id",
    "account_type",
    "yaml_match_status",
)


def yaml_route_summary(report):
    """从阶段一内存报告提取供 manifest/阶段二使用的最小路由事实。"""
    image = (report or {}).get("文件画像") or {}
    fingerprint_id = str(image.get("fingerprint_id") or "").strip()
    decision = str(image.get("decision") or "").strip()
    if decision not in {"matched", "unmatched", "ambiguous", "failed"}:
        decision = "matched" if fingerprint_id else "unmatched"
    return {
        "fingerprint_id": fingerprint_id,
        "series_family": str(image.get("series_family") or "").strip(),
        "router_bank": str(image.get("router_bank") or image.get("bank") or "未识别").strip(),
        "reader_id": str(image.get("reader_id") or "").strip(),
        "account_type": str(image.get("account_type") or image.get("账户类型") or "未知").strip(),
        "yaml_match_status": decision,
    }

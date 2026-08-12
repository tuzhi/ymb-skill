"""原始输入的来源真实性检查。

这里只识别可确定的文件来源事实，不判断交易内容是否欺诈。规则会同时被
输入预检和 Reader 纵深门禁复用，后续可整体迁移到独立反欺诈模块。
"""

from __future__ import annotations

import base64
import os
import zipfile
import xml.etree.ElementTree as ET


def pdf_to_wps_rejection_reason(path: str) -> str:
    """识别由 WPS 将 PDF 转成的 Excel，并返回稳定的拒绝原因。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".xlsx", ".xlsm"):
        return ""
    try:
        with zipfile.ZipFile(path) as archive:
            custom_properties = archive.read("docProps/custom.xml")
        root = ET.fromstring(custom_properties)
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return ""

    for prop in root:
        if prop.attrib.get("name") != "CRO":
            continue
        encoded = next((str(child.text or "").strip() for child in prop if child.text), "")
        if not encoded:
            continue
        try:
            padded = encoded + "=" * (-len(encoded) % 4)
            marker = base64.b64decode(padded, validate=True).decode("utf-8", errors="replace")
        except (ValueError, UnicodeError):
            marker = encoded
        if "Kingsoft PDF to WPS" in marker:
            return (
                f"WPS PDF 转 Excel 文件（检测到 {marker} 元数据），不作为原始流水接收；"
                "请提供银行原始 Excel 或可抽取文本的原始 PDF"
            )
    return ""

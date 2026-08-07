"""Router YAML 与不可变内存规则快照之间的纯转换器。"""

from __future__ import annotations

from ymb_standardization_core.readers.routing.rule_loader import (
    RoutingRulesSnapshot,
    build_routing_rules_snapshot,
)


class YamlRuleService:
    """无状态配置转换器；草稿、版本、发布和测试记录由上层管理。"""

    @staticmethod
    def deserialize(yaml_content: str) -> RoutingRulesSnapshot:
        """把 YAML 字符串解析、校验为不可变规则快照。

        参数：
            yaml_content: 完整的 Router YAML 文本。正式规则和草稿规则
                使用同一种格式，由调用场景决定其用途。

        返回：
            包含版本号、PDF 规则、Excel 规则和原始 YAML 的只读快照。

        异常：
            ValueError: 文本为空、YAML 语法错误、规则结构错误，或者引用了
                不存在的 Reader、Handler 或 Transform。

        本方法无状态，不读取或修改生产规则文件。
        """
        if not isinstance(yaml_content, str) or not yaml_content.strip():
            raise ValueError("yaml_content 不能为空")
        return build_routing_rules_snapshot(yaml_content)

    @staticmethod
    def serialize(snapshot: RoutingRulesSnapshot) -> str:
        """把规则快照还原为构造它的 YAML 字符串。

        参数：
            snapshot: ``deserialize`` 返回的不可变规则快照。

        返回：
            快照保存的原始 YAML 文本，保留注释与书写顺序。

        异常：
            TypeError: 参数不是 ``RoutingRulesSnapshot``。
            ValueError: 快照不是从 YAML 构建，缺少 ``source_yaml``。

        本方法不负责保存、发布或激活规则。
        """
        if not isinstance(snapshot, RoutingRulesSnapshot):
            raise TypeError("snapshot 必须是 RoutingRulesSnapshot")
        if not snapshot.source_yaml:
            raise ValueError("规则快照缺少 source_yaml，无法无损序列化")
        return snapshot.source_yaml

"""与具体渲染技术无关的 BI 图表数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class SeriesSpec:
    """一条可同时供 Excel 和 ECharts 使用的数据序列。"""

    name: str
    chart_type: str
    values: tuple[float | None, ...]
    role: str = "neutral"


@dataclass(frozen=True)
class ChartSpec:
    """一张图的业务标题、分类轴和真实数据。"""

    chart_id: str
    title: str
    kind: str
    categories: tuple[str, ...]
    series: tuple[SeriesSpec, ...]
    unit: str = "元"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OmittedChart:
    """因输入数据不足而没有生成的图表及原因。"""

    chart_id: str
    reason: str


@dataclass(frozen=True)
class ChartBundle:
    """一次 BI 运行的统一图表事实来源。"""

    schema_version: str
    strategy_version: str
    currency: str
    charts: tuple[ChartSpec, ...]
    omitted_charts: tuple[OmittedChart, ...] = ()
    warnings: tuple[str, ...] = ()

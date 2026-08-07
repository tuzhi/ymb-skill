"""对现有 BI V4 引擎的同步 Service 包装。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sys

from .models import BiAnalysisRequest, BiAnalysisResult, ServiceError


class BiAnalysisService:
    def __init__(self, script_dir: str | Path | None = None) -> None:
        self.script_dir = Path(
            script_dir or Path(__file__).resolve().parents[1] / "scripts"
        ).resolve()

    def execute_analysis(self, request: BiAnalysisRequest) -> BiAnalysisResult:
        """同步执行 BI V4 分析并返回可入库结果。

        参数：
            request: BI 任务标识、来源标准化 Run、标准化交付物路径、
                客户名称及可选白名单、借据和拟授信参数。

        返回：
            ``BiAnalysisResult``。成功时包含 V4 Excel 报告路径、核心分析
            摘要和图表数据契约；失败时状态为 ``ERROR`` 并携带
            ``ServiceError``。

        本方法同步执行且只读取标准化交付物。它不会修改标准化 Run，
        也不会持久化上层任务状态。当前 V4 引擎使用 Excel 原生图表，
        尚未生成 ECharts JSON，因此 ``chart_data.charts`` 暂为空数组。
        """
        if not isinstance(request, BiAnalysisRequest):
            raise TypeError("request 必须是 BiAnalysisRequest")
        try:
            engine = self._engine()
            source = Path(request.standardized_file_path).resolve()
            if not source.exists():
                raise FileNotFoundError(f"标准化产物不存在：{source}")
            selected = Path(engine.pick_input(str(source))).resolve()
            whitelist = self._load_whitelist(request.whitelist_path)
            new_loan = request.new_loan or engine.NEW_LOAN
            frame, daily_balance, validation = engine.load_v4(str(selected))
            frame = engine.prep(frame)
            analysis = engine.analyze(
                frame,
                engine.daily_balance(frame, daily_balance),
                validation,
                whitelist,
                new_loan,
            )
            detail = engine.analyze_v4(frame, analysis, validation)
            loans = engine.load_loans(request.loans_path) if request.loans_path else None
            whitelist_names = set(whitelist.keys()) if whitelist else None
            metrics = engine.compute_spec_metrics(
                engine.spec_augment(frame),
                loans=loans,
                whitelist=whitelist_names,
            )
            output_dir = Path(request.output_dir).resolve() if request.output_dir else selected.parent
            output_dir.mkdir(parents=True, exist_ok=True)
            output = output_dir / f"{request.client_name}__经营流水分析报告_V4.0.xlsx"
            engine.build_workbook(
                analysis,
                detail,
                frame,
                request.client_name,
                str(output),
                whitelist,
                metrics,
            )
            sales = dict(detail.get("sales") or {})
            summary = {
                "有效流入": float(detail.get("eff_in") or 0),
                "有效流出": float(detail.get("eff_out") or 0),
                "年销售额中值": float(sales.get("mid") or 0),
                "需关注对手": len(detail.get("watch") or []),
                "夜间敏感支出": int(detail.get("n_night") or 0),
                "数据质量分": float(analysis.get("q_total") or 0),
                "数据质量等级": str(analysis.get("q_grade") or ""),
            }
            return BiAnalysisResult(
                bi_run_id=request.bi_run_id,
                statement_run_id=request.statement_run_id,
                status="DONE",
                artifacts={"bi_report_path": str(output)},
                ai_analysis_summary=summary,
                chart_data={"echarts_version": "5", "charts": []},
            )
        except Exception as exc:
            return BiAnalysisResult(
                bi_run_id=request.bi_run_id,
                statement_run_id=request.statement_run_id,
                status="ERROR",
                error=ServiceError(
                    code="BI_ANALYSIS_FAILED",
                    message=str(exc),
                ),
            )

    def _engine(self) -> Any:
        if str(self.script_dir) not in sys.path:
            sys.path.insert(0, str(self.script_dir))
        import build_bi_report_v4

        return build_bi_report_v4

    @staticmethod
    def _load_whitelist(path: str) -> dict[str, tuple[Any, ...]]:
        if not path:
            return {}
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("whitelist 必须是 JSON 对象")
        return {str(key): tuple(value) for key, value in payload.items()}

"""对现有 BI V4 引擎的同步 Service 包装。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sys

from .models import BiAnalysisRequest, BiAnalysisResult, ServiceError


class BiAnalysisService:
    def __init__(
        self,
        workspace_path: str | Path,
        script_dir: str | Path | None = None,
    ) -> None:
        self.workspace_path = self._initialize_workspace(workspace_path)
        self.input_root = self.workspace_path / "inputs"
        self.run_root = self.workspace_path / "runs"
        self.output_root = self.workspace_path / "bi_output"
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
            whitelist_path = self._optional_input_path(request.whitelist_path, "白名单")
            loans_path = self._optional_input_path(request.loans_path, "借据")
            whitelist = self._load_whitelist(whitelist_path)
            new_loan = request.new_loan or engine.NEW_LOAN
            if request.dataset:
                frame = request.dataset.get("transactions")
                if frame is None or not hasattr(frame, "columns"):
                    raise ValueError("dataset.transactions 必须是 DataFrame")
                # 内存路径直接消费同一主明细；BI 只补派生列，不复制整张交易表。
                daily_balance = request.dataset.get("daily_balances")
                validation = request.dataset.get("balance_checks")
                selected = None
            else:
                source = self._path_within(
                    request.standardized_file_path,
                    self.run_root,
                    "标准化产物",
                )
                if not source.exists():
                    raise FileNotFoundError(f"标准化产物不存在：{source}")
                selected = self._path_within(
                    engine.pick_input(str(source)),
                    self.run_root,
                    "标准化产物",
                )
                frame, daily_balance, validation = engine.load_v4(str(selected))
            frame = (
                engine.prep(frame, normalize_types=False)
                if selected is None
                else engine.prep(frame)
            )
            analysis = engine.analyze(
                frame,
                engine.daily_balance(frame, daily_balance),
                validation,
                whitelist,
                new_loan,
            )
            detail = engine.analyze_v4(frame, analysis, validation)
            loans = engine.load_loans(loans_path) if loans_path else None
            whitelist_names = set(whitelist.keys()) if whitelist else None
            metrics = engine.compute_spec_metrics(
                engine.spec_augment(frame),
                loans=loans,
                whitelist=whitelist_names,
            )
            output = self._output_path(request.client_name)
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
    def _initialize_workspace(workspace_path: str | Path) -> Path:
        raw = Path(workspace_path).expanduser()
        if not raw.is_absolute():
            raise ValueError("workspace_path 必须是绝对路径")
        workspace = raw.resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        for name in ("inputs", "runs", "bi_output"):
            child = workspace / name
            child.mkdir(parents=True, exist_ok=True)
            if child.resolve().parent != workspace:
                raise ValueError(f"Workspace 子目录不能通过软链接越界：{name}")
        return workspace

    @staticmethod
    def _path_within(value: str, root: Path, label: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{label}路径必须是绝对路径")
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"{label}必须位于 Workspace {root.name}/ 内")
        return resolved

    def _optional_input_path(self, value: str, label: str) -> str:
        if not value:
            return ""
        return str(self._path_within(value, self.input_root, label))

    def _output_path(self, client_name: str) -> Path:
        name = str(client_name or "").strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("客户名称不能包含路径字符")
        output = self.output_root / f"{name}__经营流水分析报告_V4.0.xlsx"
        if output.is_symlink():
            raise ValueError("BI 输出文件不能是软链接")
        return output

    @staticmethod
    def _load_whitelist(path: str) -> dict[str, tuple[Any, ...]]:
        if not path:
            return {}
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("whitelist 必须是 JSON 对象")
        return {str(key): tuple(value) for key, value in payload.items()}

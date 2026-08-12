# Python 同步执行接口

Python 只负责确定性配置转换与同步计算；任务、草稿、规则版本、测试记录和发布状态由上层应用持久化。

## YamlRuleService

```python
deserialize(yaml_content: str) -> RoutingRulesSnapshot
serialize(snapshot: RoutingRulesSnapshot) -> str
```

`deserialize` 同时完成 YAML 语法、规则结构、Reader/Handler 引用校验并计算 `version`。
快照是不可变对象，并保留 `source_yaml`，所以 `serialize` 不会丢失注释和原始顺序。正式或草稿由调用场景决定，不写入快照。

## StatementService

```python
execute_standardization(
    request: StandardizationRequest,
    rules: RoutingRulesSnapshot,
) -> StandardizationResult
```

`execute_standardization` 同步执行 Stage 1～4、QC 和 Validator。Run 启动时固定传入的规则快照，
整个 Stage 1 不会因外部 YAML 变化而混用版本。正式任务和草稿测试使用同一个接口；
上层传入正式或草稿的规则快照，并负责记录任务用途。

DTO 定义在 `models/`：

- `InputFile(file_name, file_path, file_md5="")`
- `StandardizationRequest(client_name, files, parent_run_id=None, remove_file_ids=(), file_passwords={})`
- `StandardizationResult(run_id, status, next_action, message, client, rule_snapshot, summary, file_results, stages, qc_client, business_summary, dataset, deliverable)`
- `ServiceError(code, message, details={})`

`StandardizationResult` 的完整目标结构示例见
[`examples/standardization_result.example.json`](examples/standardization_result.example.json)。
示例中的明细数组仅保留代表性记录；正式 Python 返回中的 `dataset`
保持 DataFrame。`to_summary_dict()` 返回不含明细的轻量结果，`write_zip()`
把 DataFrame 逐行编码并直接写入 ZIP，不在内存中构造完整 dict 或 JSON 字符串。

Child Run 的 `file_passwords` 只在当前同步调用内存中存在，并按相对路径把单个密码传给 Stage 1 Reader；不会生成密码提示文件，也不会进入结果 DTO、Run 日志或错误包。

## BiAnalysisService

BI 接口位于 `../../bank-statement-bi-analysis/bank_statement_bi_analysis/`：

```python
execute_analysis(request: BiAnalysisRequest) -> BiAnalysisResult
```

DTO 定义：

- `BiAnalysisRequest(bi_run_id, statement_run_id, standardized_file_path, client_name, output_dir="", whitelist_path="", loans_path="", new_loan=None)`
- `BiAnalysisResult(bi_run_id, statement_run_id, status, artifacts, ai_analysis_summary, chart_data, error)`

当前 BI V4 引擎生成 Excel 原生图表，但尚未生成 ECharts JSON，因此 `chart_data.charts` 当前为空数组；
接口保留该字段，后续由同一 BI 引擎补齐，Service 不伪造图表数据。

## 调用示例

```python
from services import (
    InputFile,
    StandardizationRequest,
    StatementService,
    YamlRuleService,
)

rules = YamlRuleService.deserialize(production_yaml)
service = StatementService("./runs")
result = service.execute_standardization(
    StandardizationRequest(
        client_name="客户甲",
        files=(InputFile("流水.xlsx", "/data/流水.xlsx", "md5:..."),),
    ),
    rules,
)
```

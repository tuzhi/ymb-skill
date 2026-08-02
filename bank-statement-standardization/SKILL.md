---
name: bank-statement-standardization
description: >-
  用确定性 Python 流水线完成银行/支付流水标准化、多文件整合、余额校验、交易打标和交付物生成。
  用户提交 Excel、CSV、PDF 或 zip 流水文件，或要求流水清洗、合并、校验、打标、尽调底表时使用。
  执行 Orchestrator 并按紧凑 RunResult 完成交付或路由。
---

# 银行流水标准化

## 执行

```powershell
python "<skill目录>\scripts\orchestrator.py" run --folder "<客户文件或zip>" --run-root ".\runs"
```

读取 stdout 最后一行的 `RunResult`，按 `next_action` 处理：

- `DELIVER`：返回 `artifact_refs` 中的交付物，并直接使用 `summary` 说明文件数量和 QC。
- `REQUEST_USER`：按 `message` 请求用户补充信息；收到回复后执行 `action`。
- `REPORT_ERROR`：返回 `message` 和 `context_ref`。
- `AI_FALLBACK`：执行 `action`，将后续角色交给确定性 Coordinator。

`next_action=DELIVER` 时任务完成。

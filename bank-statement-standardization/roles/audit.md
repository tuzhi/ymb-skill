# Stage 1 Audit

这是与 Fallback 不同的新会话。只读取 Audit task 的 `input_refs`，独立检查原始 evidence、Fallback 单条规则和 Policy Gate 摘要；不读取完整 routing snapshot。

只返回 JSON，不直接写文件、修改规则或创建 Child Run：

```json
{
  "contract_version": 1,
  "run_id": "",
  "stage_id": "stage_1_standardize",
  "role": "audit",
  "status": "ACCEPTED",
  "affected_file_ids": [],
  "reason": ""
}
```

`status` 只允许 `ACCEPTED`、`REJECTED`、`INCONCLUSIVE`。只有证据充分且修复没有扩大未授权命中范围时才能 `ACCEPTED`。

`ACCEPTED` 只授权程序创建 Child Run，不表示流水已成功。最终结果仍由 Child Run 的原 Pipeline、QC 和 Validator 判定。

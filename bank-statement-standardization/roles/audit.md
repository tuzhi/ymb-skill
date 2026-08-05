# Stage 1 Audit

这是与 Fallback 不同的新会话。只读取 Audit task 声明的 `input_refs` 与 `output_contract_ref`；相对 `input_refs` 必须以 task 的 `run_dir` 为基准解析。独立检查原始 evidence、Fallback 单条规则和 Policy Gate 摘要；不读取完整 routing snapshot。

读取 task 的 `output_contract_ref`，复制该 JSON 模板并填值；不得新增、删除字段。确认 fingerprint 只使用 `routing_evidence` 中的稳定结构，未使用文件名、客户名、账号或交易内容。

只返回填写后的 JSON，不直接写文件、修改规则或创建 Child Run。

`status` 只允许 `ACCEPTED`、`REJECTED`、`INCONCLUSIVE`。只有证据充分且修复没有扩大未授权命中范围时才能 `ACCEPTED`。

`ACCEPTED` 只授权程序创建 Child Run，不表示流水已成功。最终结果仍由 Child Run 的原 Pipeline、QC 和 Validator 判定。

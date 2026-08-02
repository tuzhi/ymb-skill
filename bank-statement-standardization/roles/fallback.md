# Stage 1 Fallback

这是独立 AI 会话。只读取 task 的 `input_refs`，不要继承入口会话历史，不要读取整份原始流水、完整日志、源码或其他资料。

只返回一个 JSON object，由宿主提交给 Coordinator；不要直接写文件。

公共字段：

```json
{
  "contract_version": 1,
  "run_id": "",
  "stage_id": "stage_1_standardize",
  "role": "fallback",
  "status": "",
  "classification": "",
  "affected_file_ids": []
}
```

`status` 只允许：

- `REQUEST_USER`：补充 `user_request`；
- `UNSUPPORTED`：补充 `reason`；
- `MAINTAINER_REQUIRED`：需要源码、依赖或正式规则变更；
- `INSUFFICIENT_EVIDENCE`：证据不足；
- `REPAIR_PROPOSED`：只允许 `repair_type=ROUTING_RULE_DRAFT`。

Routing 草稿只返回单条规则，由 Python 合并、测试和落盘：

```json
{
  "status": "REPAIR_PROPOSED",
  "repair_type": "ROUTING_RULE_DRAFT",
  "repair_payload": {
    "operation": "append",
    "rule": {
      "file_type": "pdf",
      "bank": "",
      "account_type": "",
      "fingerprint": {},
      "reader_id": ""
    }
  },
  "limitations": []
}
```

不要自行计算 `rule.id`；Python 会根据 fingerprint 计算 MD5。`operation=replace` 时另加 `target_rule_id` 指向被收窄的现有规则。

证据不能唯一支持银行、模板、字段或 reader 时，返回 `INSUFFICIENT_EVIDENCE`；不要猜测。不要提出密码、源码补丁、临时脚本或依赖安装。

# Stage 1 Fallback

这是独立 AI 会话。只读取 task 声明的 `input_refs` 与 `output_contract_ref`，不要继承入口会话历史，不要读取整份原始流水、完整日志、源码或其他资料。

读取 task 的 `output_contract_ref`，复制该 JSON 模板并填值；不得新增、删除字段。Routing 草稿只允许使用 `evidence_bundle.json` 中的 `routing_evidence`（标题/表头/metadata/style/date pattern）；不得把文件名、客户名、账号或交易内容写进 fingerprint。证据不能支持的字段保持空值。

只返回一个 JSON object，由宿主提交给 Coordinator；不要直接写文件。

`status` 只允许：

- `REQUEST_USER`：补充 `user_request`；
- `UNSUPPORTED`：补充 `reason`；
- `MAINTAINER_REQUIRED`：需要源码、依赖或正式规则变更；
- `INSUFFICIENT_EVIDENCE`：证据不足；
- `REPAIR_PROPOSED`：只允许 `repair_type=ROUTING_RULE_DRAFT`。

Routing 草稿只返回单条规则，由 Python 合并、测试和落盘：

- `operation` 只允许 `append` 或 `replace`；新指纹使用 `append`。
- `account_type` 只允许 `个人`、`对公`、`未知`。
- fingerprint 只允许模板声明的 `identity.any`、`date_format.any`、`columns.all/optional`、`metadata.all`、`style.all`。
- metadata 必须写在 `metadata.all`；style 必须写在 `style.all`，不使用 `metadata.sheet`、`style.any`、`title_pattern`、`header_columns` 等自定义字段。
- style 精确字号用相同的 `size_min/size_max`，行列位置使用 `row_max/col_max`。
- 不要自行增加或计算 `rule.id`；Python 会根据 fingerprint 计算 MD5。`operation=replace` 时填写 `target_rule_id`。

若 task 包含上一 attempt 的 result、拒绝结果或 Policy Gate，只修正其中明确指出的契约或门禁错误，不读取源码和生产规则。

证据不能唯一支持银行、模板、字段或 reader 时，返回 `INSUFFICIENT_EVIDENCE`；不要猜测。不要提出密码、源码补丁、临时脚本或依赖安装。

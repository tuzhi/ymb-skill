# 可逆 Token Vault 脱敏服务

本项目是 `ymb-skill` 流水标准化之前置服务：项目通过 `pyproject.toml` 依赖 `ymb-standardization-core` 完成标准化，标准化成功后再做可逆 Token 化，输出“结构不变的脱敏文件”和“调用方自行保管的 Token Vault 映射文件”。标准化失败时直接退出，不进入 AI 分析链路。

## 安全边界

- 服务不保存原始文件或脱敏文件；Token Vault 文件仍返回给调用方自持。
- 服务会在本机受控 SQLite 文件中持久化最近 200 个 `sha256 -> Token Vault` 缓存，用于 sha256 逆脱敏。
- SQLite 缓存文件包含可逆映射，不得提交到 Git，不得同步到外部 AI 或非受控目录。
- 日志只记录 `request_id`、文件大小、耗时、命中数量、标签计数、状态码和错误类型。
- 日志禁止写入原始报文、脱敏正文、Token Vault、原始文件名中的敏感信息。
- 可逆能力只在本服务内完成，WorkBuddy/外网 AI 只接触 Token 化后的内容。
- 当前实现不依赖外部模型运行时，规则识别和标准字段 Token 化优先。

## 安装

```powershell
cd D:\PYTHON_WORK\ymb-skill
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ymb-standardization-core -e token-vault-service[test]
```

## 启动

```powershell
cd D:\PYTHON_WORK\ymb-skill
.\.venv\Scripts\python.exe -m uvicorn token_vault_service.app:app --host 127.0.0.1 --port 8010
```

打开 `http://127.0.0.1:8010/` 使用 Web 页面。

## 主要接口

### 文件标准化并 Token 化

```powershell
$form = @{
  file = Get-Item "D:\path\statement.xlsx"
  enabled_labels = "person,phone,id_number,account,email,secret"
}
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8010/api/files/tokenize -Form $form -OutFile .\tokenized_bundle.zip
```

批量上传可使用 `files` 表单字段：

```powershell
$form = @{
  files = @(
    Get-Item "D:\path\statement-a.xlsx",
    Get-Item "D:\path\statement-b.xlsx"
  )
  enabled_labels = "person,phone,id_number,account,email,secret"
}
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8010/api/files/tokenize/batch -Form $form -OutFile .\tokenized_batch_bundle.zip
```

返回 zip 包说明：

单文件 `/api/files/tokenize` 返回给直接调用方的可逆包，包含：

- `*_tokenized.csv` 或 `*_tokenized.xlsx`
- `*_token_vault.json`
- `*_token_vault_ref.json`
- `*_summary.json`

批量 `/api/files/tokenize/batch` 返回给 WorkBuddy / `bank-statement-standardization` 的下游包，包含：

- `tokenized_batch_bundle/*__standardized.csv` 或对应表格文件
- `tokenized_batch_bundle/summary/*_summary.json`
- `tokenized_batch_bundle/summary/tokenized_batch_bundle_token_vault_ref.json`
- `tokenized_batch_bundle/summary/manifest.json`

批量下游包不包含 `*_token_vault.json`、`token_vault_manifest.json` 或原始 `archive_name`。真实 Token Vault 仅写入本机受控 SQLite 缓存；下游 manifest 只传递 `archive_id` 和 `client_alias`，其中 `client_alias` 来自 token 化后的归档/主体名称；多个 token 化名称用 `_` 拼接，无法识别时才退回批次技术别名。

### 文本 Token 化

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/api/tokenize/text -ContentType 'application/json' -Body '{
  "pages": [{"page_no": 1, "text": "客户姓名：张三 手机号：13800138000"}],
  "enabled_labels": ["person", "phone"]
}'
```

### 逆脱敏

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/api/detokenize -ContentType 'application/json' -Body '{
  "text": "张某001 手机号001",
  "file_sha256": "原始文件sha256"
}'
```

## 配置

- `TOKEN_VAULT_SERVICE_HOST`：服务监听地址，默认 `127.0.0.1`
- `TOKEN_VAULT_SERVICE_PORT`：服务端口，默认 `8010`
- `TOKEN_VAULT_SERVICE_MAX_CHARS`：文本接口最大字符数，默认 `20000`
- `TOKEN_VAULT_SERVICE_LOG_PATH`：审计日志路径，默认 `logs/token-vault-service.jsonl`
- `TOKEN_VAULT_SERVICE_VAULT_CACHE_PATH`：sha256 到 Token Vault 的本机 SQLite 缓存路径，默认 `data/token-vault-cache.sqlite3`
- `TOKEN_VAULT_SERVICE_VAULT_CACHE_SIZE`：SQLite 缓存最多保留条数，默认 `200`

## 验证

```powershell
cd D:\PYTHON_WORK\token_vault_service
.\.venv\Scripts\python.exe -m pytest -v --basetemp ..\.pytest-tmp -p no:cacheprovider
.\.venv\Scripts\python.exe -m build
```

日志检查应确认不包含原始文本、Token 化正文、Token Vault 或敏感字段明细。

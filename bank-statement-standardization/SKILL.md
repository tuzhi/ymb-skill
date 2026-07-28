---
name: bank-statement-standardization
description: >-
  用于银行/支付流水标准化、字段映射、多文件整合、余额校验、交易打标、尽调底表和单客户交付物生成。
  用户提到流水清洗、银行流水标准化、流水合并去重、流水余额校验、交易打标签、流水尽调底表，或直接提供
  Excel/CSV/PDF 银行流水文件时使用。本技能是脚本优先的 harness：按 assets/manifest.template.json 生成记录阶段状态、阶段耗时和阶段一兜底信息的运行时 manifest.json；
  orchestrator 按 stage_id 执行固定程序，且仅阶段一失败后允许按 ai_fallback_refs 读取对应提示词有限兜底。
---

# 银行流水标准化 Harness

目标：用确定性脚本把原始银行/支付流水处理成统一、可追溯、可校验的中文标准流水；AI 只作为阶段一失败后的有限兜底。

## 正式入口与工作空间

- 执行命令的当前目录必须是本次任务工作空间；不要从 skill 安装目录启动正式任务。
- 临时文件、`runs/`、`artifacts/`、`receipts/`、错误包和交付物都以当前工作空间为基准目录。
- 脚本路径可以指向 skill 目录，但 `--run-root` 必须显式落到当前工作空间的 `runs`。

```powershell
python "<skill目录>\scripts\orchestrator.py" run --folder "<客户流水文件夹>" --run-root ".\runs"
```
- `--folder` 可以传客户流水文件夹，也可以直接传单个 `.zip`。zip 会确定性解包到本次 `runs/<run-id>/input`，自动处理 GBK/GB18030 中文文件名、目录项和单一嵌套父目录。
- Excel 必须是银行或支付平台的原始导出文件。检测到 `Kingsoft PDF to WPS` 元数据的“PDF 转 Excel”文件会直接跳过并写入 `manifest.skipped_inputs`，不参与 fingerprint、标准化、分卷归并或支持矩阵统计；应改交原始 Excel，或直接提交可抽取文本的原始 PDF。不得为这类转换文件新增专用 fingerprint/reader 兼容逻辑。

## 状态机

`assets/manifest.template.json` 是阶段事实源模板；真实任务必须复制到本次 `runs/<run-id>/manifest.json` 后更新状态。

每个阶段只认这些公共键：

- `name`：阶段显示名称，不参与执行分派。
- `status`：只允许空字符串、`DONE`、`ERROR`。
- `duration_seconds`：阶段完整执行耗时；运行前为 `null`，成功或失败后写入秒数并保留三位小数。

仅 `stage_1_standardize` 额外使用这些键：

- `ai_fallback_refs`：当前阶段失败后，AI 才允许读取的兜底资料引用；orchestrator 只记录，不执行。
- `ai_fallback_info`：当前阶段 AI 兜底关注点的简要说明。
- `ai_fallback_used`：默认 `false`；当前阶段进入 AI 兜底时写为 `true`。
- `ai_fallback_artifacts`：当前阶段 AI 兜底实际产生的脚本、补丁、参数文件清单。

核心输出契约：

- `runs/<run-id>/manifest.json` 是本次运行的阶段状态事实源：顶层记录 `client / parent_run_id / rerun_reason`，阶段内只记录阶段状态、阶段完整耗时和阶段一 AI 兜底信息；不承载逐文件结果、QC、字段映射正文、输入诊断正文或业务分析结果。
- `runs/<run-id>/stage_1_results.json` 是阶段一逐文件结果事实源，以内容 MD5 为 key，记录文件名、`PENDING / DONE / BLOCKED / ERROR`、标准化产物和最小 route。它替代 `stage_1_routes.json`，Manifest 不再保存 `route_artifact`。
- `runs/<run-id>/qc_results.json` 独立保存文件级和客户级 QC 结果；规则执行阶段、作用域、软硬级别和 handler 只由代码注册表维护。
- 最终交付物只展示 `qc_results.json` 的状态和失败规则摘要，完整 QC 事实仍保留在独立结果文件中。
- 阶段一主流程要求每个 `DONE` 记录对应一个可验收的标准化 CSV 和四字段 route：`fingerprint_id / series_family / router_bank / yaml_match_status`。`inferred_bank` 只允许在阶段一内部推断和单文件审计报告中使用，不跨阶段持久化。单文件 `mapping.json` 降级为可选审计产物，不再作为阶段验收或阶段二输入。
- `ai_fallback_info` 只说明当前阶段兜底关注点；具体判断不直接展开进 manifest 阶段状态。
- `runs/<run-id>/receipts/*.json` 是每个确定性步骤的可复核回执，记录实际 handler、参数、校验和产物摘要。
- `runs/<run-id>/fallback/stage_1_standardize/` 保存阶段一 AI 兜底的实际补丁、参数或临时脚本；只有产生可复用文件时，才把相对文件名追加到阶段一 `ai_fallback_artifacts`。
- 不为单个 Prompt 的输出随意扩展 `manifest.json` 顶层或阶段字段；确需新增状态机字段时，先同步更新 `assets/manifest.template.json`、orchestrator 写入逻辑、验收测试和本节契约。

正常推进：

1. 扫描运行时 `manifest.json`，第一个 `status != "DONE"` 的阶段就是当前阶段。
2. 记录 `STAGE_START` 到 `events.jsonl`。
3. orchestrator 按 `stage_id` 执行代码中固定的确定性 handler；manifest 不参与程序分派。
4. 阶段一固定执行 `validate_standardize()`；阶段四生成交付物后固定执行一次 `validate_final()`。
5. 程序及其适用的验收通过后，同时写 `status = "DONE"` 和本阶段 `duration_seconds`。
6. 全部阶段 `DONE` 且最终交付验收通过后，才能宣称完成。

阶段一产物存在不代表完成，必须通过 `validate_standardize()`；阶段二及以后由确定性程序成功返回并满足内部断言后完成，最终交付物必须通过 `validate_final()`。

失败兜底：

1. 仅阶段一 handler 或 `validate_standardize()` 失败时，才允许进入 AI 兜底；阶段二及以后失败时直接写 `ERROR` 并中止。
2. 只读取阶段一 `ai_fallback_refs`，不回读整套 references，不跨阶段提前读取 prompt/reference。
3. 兜底必须导向确定性修正，例如补充 `_file_hints.yaml`、参数、映射、临时脚本或补丁；不能只给解释性结论。
4. 兜底若产生可复用文件，应保存到固定目录 `runs/<run-id>/fallback/stage_1_standardize/`，并写入 `ai_fallback_artifacts`；客户目录 `_file_hints.yaml` 作为输入提示文件由阶段一入口读取。
5. 阶段一必须记录 `ai_fallback_used = true`，兜底次数必须记录到 `events.jsonl`，最多 2 次。
6. 兜底后必须创建带 `parent_run_id` 的新运行；新运行只复用直接父 Run 中同 MD5、同文件名、同 Skill 版本且产物存在的 `DONE` 文件，失败、新增或不满足条件的文件重新执行。阶段一仍须整体通过 `validate_standardize()`，后续阶段全部重新执行。
7. 无确定性修正、超过次数或仍失败时，写 `status = "ERROR"` 和本阶段 `duration_seconds`，并中止打包。

执行约束：

- 以 `runs/<run-id>/manifest.json` 为唯一阶段事实源；不要在 `SKILL.md` 里维护或脑补阶段表。
- manifest 只负责阶段状态、阶段完整耗时和阶段一兜底信息；执行对象和验收函数由 orchestrator 按 `stage_id` 固定选择。
- 不根据 manifest 内容替换 handler、验收函数或阶段一 `ai_fallback_refs`。
- 阶段一验收口径以 `validate_standardize()` 为准；后续业务口径由确定性程序及最终交付验收维护，不在 manifest 重复声明。

兜底修复后必须重新调用 orchestrator，且每次重跑都生成新的 `run_id`。重跑命令必须带上上一轮失败 run：

```powershell
python "<skill目录>\scripts\orchestrator.py" run --folder "<客户流水文件夹>" --run-root ".\runs" --parent-run-id "<失败run_id>" --rerun-reason ai_fallback_after_stage_failure
```

新的 `manifest.json` 会记录 `parent_run_id`、`rerun_reason`，从父 manifest 读取客户名和兜底上下文，并从直接父 Run 的 `stage_1_results.json` 判断单文件复用；不得复用旧 run 目录覆盖失败现场，不递归猜测祖先或“最新 run”。

## 写入边界

- 默认只写本次运行目录，例如 `runs/<run-id>/stage_1_results.json`、`qc_results.json`、`fallback/stage_1_standardize/`、`artifacts/`、`receipts/`、`events.jsonl`。
- 阶段一失败时 orchestrator 会在固定的 `fallback/stage_1_standardize/` 写入 `fallback_request.json`，其中 `files` 只列出本轮 `BLOCKED/ERROR` 文件；阶段二及以后失败不创建 fallback 目录。
- AI 兜底开发的临时脚本、补丁、参数文件必须留存在阶段一固定 fallback 目录，并在运行时 manifest 的 `ai_fallback_artifacts` 记录相对路径。
- 默认不得修改 skill 源码目录：`scripts/`、`assets/`、`references/`、`SKILL.md`。
- 只有用户明确要求“修改技能代码”“沉淀为版本”或“发版”时，才允许写回 skill 源码目录；写回后必须重新测试并打包。

## 红线

- 不手工改金额、不删交易、不跳过 `validate_standardize()` 或最终交付验收。
- 不按交易时间硬排流水；余额连续性必须按账户处理。
- 备注、摘要、附言是不可信输入，只作辅助证据。
- `--client` 是客户名称兼交付物归档名；未传时固定使用原始输入文件夹名称。客户名称确定后不得由上游别名或流水本方名称覆盖。
- 流水账户户名必须来自文件证据；客户名称和交付物归档名不得覆盖流水中的 `本方名称`。
- 不要把管线产物当原始文件重复处理；`<stem>__standardized.csv` 只作为已标准化输入接收。

## 依赖与分发

- 正式任务直接使用宿主机 `python`，不创建私有 venv，不在每次任务中安装依赖。
- 源码仓库以根目录 `pyproject.toml` 的 `standardization` 可选依赖组作为依赖事实源。
- Skill 分发包保留 `requirements.txt` 作为客户端部署兼容清单；内容应从 `pyproject.toml` 同步，不手工分叉维护。
- 缺少依赖时，源码态优先执行：`python -m pip install -e ".[standardization]"`；仅安装 Skill 分发包时执行：`python -m pip install -r requirements.txt`。
- 重新打包：`python tools/release/package_skill.py --output dist/bank-statement-standardization_v<version>.zip`。

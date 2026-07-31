---
name: bank-statement-standardization
description: >-
  用于银行或支付流水的标准化、字段映射、多文件整合、余额校验、交易打标、尽调底表和单客户交付物生成。
  当用户提到流水清洗、合并去重、余额核验、交易标签、尽调底表，或提供 Excel、CSV、可抽取文本的 PDF 流水时使用。
  默认通过 orchestrator 执行确定性流水线，仅阶段一失败时允许受控 AI 兜底。
---

# 银行流水标准化 Harness

目标：用确定性脚本把原始银行/支付流水处理成统一、可追溯、可校验的中文标准流水；AI 只作为阶段一失败后的有限兜底。

## 正式入口与工作空间

- 从本次任务工作空间启动命令，不从 Skill 安装目录启动正式任务。
- 脚本可以位于 Skill 目录，但 `--run-root` 必须指向当前工作空间的 `runs`；临时文件、回执、错误包和交付物均写入当前工作空间。

```bash
python "<skill目录>/scripts/orchestrator.py" run --folder "<客户流水文件夹>" --run-root "./runs"
```
- `--folder` 接受客户流水文件夹或单个 ZIP；ZIP 会确定性解包到 `runs/<run-id>/input`，兼容 GBK/GB18030 中文文件名和单一嵌套父目录。
- Excel 必须是银行或支付平台的原始导出文件。检测到 `Kingsoft PDF to WPS` 的“PDF 转 Excel”文件时直接跳过并写入 `manifest.skipped_inputs`；应改交原始 Excel 或可抽取文本的原始 PDF，不为转换文件新增 fingerprint/reader。

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
- `runs/<run-id>/stage_1_results.json` 是阶段一逐文件结果事实源，以内容 MD5 为 key，记录文件名、`PENDING / DONE / BLOCKED / ERROR`、识别类型、标准交易笔数、标准化产物和最小 route。
- `runs/<run-id>/qc_results.json` 独立保存文件级和客户级 QC 结果；最终交付物只展示状态和失败规则摘要，完整 QC 事实保留在该文件中。
- 阶段一主流程要求每个 `DONE` 记录对应一个可验收的标准化 CSV 和四字段 route：`fingerprint_id / series_family / router_bank / yaml_match_status`。`inferred_bank` 只允许在阶段一内部推断和单文件审计报告中使用，不跨阶段持久化。单文件 `mapping.json` 降级为可选审计产物，不再作为阶段验收或阶段二输入。
- `runs/<run-id>/receipts/*.json` 是每个确定性步骤的可复核回执，记录实际 handler、参数、校验和产物摘要。
- 不为单个 Prompt 的输出随意扩展 `manifest.json` 顶层或阶段字段；确需新增状态机字段时，先同步更新 `assets/manifest.template.json`、orchestrator 写入逻辑、验收测试和本节契约。

正常推进：

1. 扫描运行时 `manifest.json`，第一个 `status != "DONE"` 的阶段就是当前阶段。
2. 记录 `STAGE_START` 到 `events.jsonl`。
3. orchestrator 按 `stage_id` 执行代码中固定的确定性 handler；manifest 不参与程序分派。
4. 阶段一固定执行 `validate_standardize()`；阶段四生成交付物后固定执行一次 `validate_final()`。
5. 程序及其适用的验收通过后，同时写 `status = "DONE"` 和本阶段 `duration_seconds`。
6. 全部阶段 `DONE` 且最终交付验收通过后，才能宣称完成。

产物存在不代表完成；必须通过当前阶段适用的验收，最终交付物必须通过 `validate_final()`。

失败兜底：

1. 仅阶段一 handler 或 `validate_standardize()` 失败时，才允许进入 AI 兜底；阶段二及以后失败时直接写 `ERROR` 并中止。
2. 只读取阶段一 `ai_fallback_refs`，不回读整套 references，不跨阶段提前读取 prompt/reference。
3. 兜底必须导向确定性修正，例如补充 `_file_hints.yaml`、参数、映射、临时脚本或补丁；不能只给解释性结论。
4. 兜底若产生可复用文件，应保存到固定目录 `runs/<run-id>/fallback/stage_1_standardize/`，并写入 `ai_fallback_artifacts`；客户目录 `_file_hints.yaml` 作为输入提示文件由阶段一入口读取。
5. 阶段一必须记录 `ai_fallback_used = true`，兜底次数必须记录到 `events.jsonl`，最多 2 次。
6. 兜底后必须创建带 `parent_run_id` 的新运行；新运行只复用直接父 Run 中同 MD5、同文件名、同 Skill 版本且产物存在的 `DONE` 文件，失败、新增或不满足条件的文件重新执行。阶段一仍须整体通过 `validate_standardize()`，后续阶段全部重新执行。
7. 无确定性修正、超过次数或仍失败时，写 `status = "ERROR"` 和本阶段 `duration_seconds`，并中止打包。

执行约束：

- `manifest.json` 只负责阶段状态、完整耗时和阶段一兜底信息；不要从 manifest 或 `SKILL.md` 推断、替换 handler 与验收函数。
- 阶段一验收以 `validate_standardize()` 为准；后续业务口径由确定性程序及最终交付验收维护。

兜底修复后必须重新调用 orchestrator，且每次重跑都生成新的 `run_id`。重跑命令必须带上上一轮失败 run：

```powershell
python "<skill目录>\scripts\orchestrator.py" run --folder "<客户流水文件夹>" --run-root ".\runs" --parent-run-id "<失败run_id>" --rerun-reason ai_fallback_after_stage_failure
```

新的 `manifest.json` 会记录 `parent_run_id`、`rerun_reason`，从父 manifest 读取客户名和兜底上下文，并从直接父 Run 的 `stage_1_results.json` 判断单文件复用；不得复用旧 run 目录覆盖失败现场，不递归猜测祖先或“最新 run”。

## 写入边界

- 默认只写本次 `runs/<run-id>/`，包括逐文件结果、QC、fallback、artifacts、receipts 和 events。
- 阶段一失败时，`fallback_request.json` 只列出本轮 `BLOCKED/ERROR` 文件；AI 兜底产物保存在同一 fallback 目录并记录到 `ai_fallback_artifacts`。
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

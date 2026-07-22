---
name: bank-statement-standardization
description: >-
  用于银行/支付流水标准化、字段映射、多文件整合、余额校验、交易打标、尽调底表和单客户交付物生成。
  用户提到流水清洗、银行流水标准化、流水合并去重、流水余额校验、交易打标签、流水尽调底表，或直接提供
  Excel/CSV/PDF 银行流水文件时使用。本技能是脚本优先的 harness：按 assets/manifest.template.json 生成运行时 manifest.json，并执行 script、validator、
  ai_fallback_refs、status；AI 只在阶段失败后读取对应提示词有限兜底。
---

# 银行流水标准化 Harness

目标：用确定性脚本把原始银行/支付流水处理成统一、可追溯、可校验的中文标准流水；AI 只作为阶段失败后的有限兜底。

## 正式入口与工作空间

- 执行命令的当前目录必须是本次任务工作空间；不要从 skill 安装目录启动正式任务。
- 临时文件、`runs/`、`artifacts/`、`receipts/`、错误包和交付物都以当前工作空间为基准目录。
- 脚本路径可以指向 skill 目录，但 `--run-root` 必须显式落到当前工作空间的 `runs`。

```powershell
python "<skill目录>\scripts\orchestrator.py" run --folder "<客户流水文件夹>" --run-root ".\runs"
```
- `--folder` 可以传客户流水文件夹，也可以直接传单个 `.zip`。zip 会在 `preflight` 阶段确定性解包到本次 `runs/<run-id>/input`，自动处理 GBK/GB18030 中文文件名、目录项和单一嵌套父目录，并把解包清单写入 `receipts/01-preflight.json`。
- Excel 必须是银行或支付平台的原始导出文件。检测到 `Kingsoft PDF to WPS` 元数据的“PDF 转 Excel”文件会直接跳过并写入 `manifest.skipped_inputs`，不参与 fingerprint、标准化、分卷归并或支持矩阵统计；应改交原始 Excel，或直接提交可抽取文本的原始 PDF。不得为这类转换文件新增专用 fingerprint/reader 兼容逻辑。

## 状态机

`assets/manifest.template.json` 是阶段事实源模板；真实任务必须复制到本次 `runs/<run-id>/manifest.json` 后更新状态。

每个阶段只认这些键：

- `script`：优先执行的确定性脚本。
- `validator`：阶段验收函数或脚本。
- `ai_fallback_refs`：当前阶段失败后，AI 才允许读取的兜底资料引用；orchestrator 只记录，不执行。
- `ai_fallback_info`：当前阶段 AI 兜底关注点的简要说明。
- `ai_fallback_used`：默认 `false`；当前阶段进入 AI 兜底时写为 `true`。
- `ai_fallback_artifacts`：当前阶段 AI 兜底实际产生的脚本、补丁、参数文件清单。
- `status`：只允许空字符串、`DONE`、`ERROR`。
- `route_artifact`：仅阶段一使用；指向客户级 `stage_1_routes.json`，manifest 本身不承载逐文件路由明细。

核心输出契约：

- `runs/<run-id>/manifest.json` 是本次运行的唯一事实源：顶层记录 `client / parent_run_id / rerun_reason`，阶段内记录程序路线、AI 兜底路线、状态和产物索引；不承载逐文件路由明细、字段映射正文、输入诊断正文或业务分析结果。
- 阶段一主流程要求标准化 CSV 与 `route_artifact` 指向的客户级路由索引一一对应。索引以标准化 CSV 文件名为键，每个文件只保存 `fingerprint_id / series_family / router_bank / inferred_bank / yaml_match_status` 五项。单文件 `mapping.json` 降级为可选审计产物，不再作为阶段验收或阶段二输入。
- `ai_fallback_info` 只说明当前阶段兜底关注点；具体判断不直接展开进 manifest 阶段状态。
- `runs/<run-id>/receipts/*.json` 是每个确定性步骤的可复核回执，记录脚本、参数、校验和产物摘要。
- `runs/<run-id>/fallback/<stage-id>/` 保存 AI 兜底的实际补丁、参数或临时脚本；只有产生可复用文件时，才把相对文件名追加到当前阶段 `ai_fallback_artifacts`。
- 不为单个 Prompt 的输出随意扩展 `manifest.json` 顶层或阶段字段；确需新增状态机字段时，先同步更新 `assets/manifest.template.json`、orchestrator 写入逻辑、validator/测试和本节契约。

正常推进：

1. 扫描运行时 `manifest.json`，第一个 `status != "DONE"` 的阶段就是当前阶段。
2. 记录 `STAGE_START` 到 `events.jsonl`。
3. 执行当前阶段 `script`。
4. 执行当前阶段 `validator`。
5. 验收通过后写 `status = "DONE"`。
6. 全部阶段 `DONE` 后执行最终验收，确认最终交付物存在，才能宣称完成。

产物存在不代表阶段完成；只有 `validator` 通过才算完成。

失败兜底：

1. 当前阶段 `script` 或 `validator` 失败时，才允许进入 AI 兜底。
2. 只读取当前阶段 `ai_fallback_refs`，不回读整套 references，不跨阶段提前读取 prompt/reference。
3. 兜底必须导向确定性修正，例如补充 `_file_hints.yaml`、参数、映射、临时脚本或补丁；不能只给解释性结论。
4. 兜底若产生可复用文件，应保存到固定目录 `runs/<run-id>/fallback/<stage-id>/`，并写入 `ai_fallback_artifacts`；客户目录 `_file_hints.yaml` 作为输入提示文件由阶段一入口读取。
5. 当前阶段必须记录 `ai_fallback_used = true`，兜底次数必须记录到 `events.jsonl`，最多 2 次。
6. 兜底后必须创建带 `parent_run_id` 的新运行；新运行从阶段一重新执行，失败阶段及其后续阶段都必须重新通过各自 `validator`。
7. 无确定性修正、超过次数或仍失败时，写 `status = "ERROR"` 并中止打包。

执行约束：

- 以 `runs/<run-id>/manifest.json` 为唯一阶段事实源；不要在 `SKILL.md` 里维护或脑补阶段表。
- 按当前阶段的 `script` 和 `validator` 判断本轮应执行、验收或兜底的对象。
- 不自行替换 `script`、`validator` 或 `ai_fallback_refs`。
- 字段、标签、附件口径以当前阶段 `validator` 和 `ai_fallback_refs` 为准，不在主文档重复维护。

兜底修复后必须重新调用 orchestrator，且每次重跑都生成新的 `run_id`。重跑命令必须带上上一轮失败 run：

```powershell
python "<skill目录>\scripts\orchestrator.py" run --folder "<客户流水文件夹>" --run-root ".\runs" --parent-run-id "<失败run_id>" --rerun-reason ai_fallback_after_stage_failure
```

新的 `manifest.json` 会记录 `parent_run_id`、`rerun_reason`，并从父 manifest 读取客户名、失败阶段和兜底产物；不得复用旧 run 目录覆盖失败现场。重跑必须显式指定 `parent_run_id`，不得通过“最新 run”猜测父运行。

## 写入边界

- 默认只写本次运行目录，例如 `runs/<run-id>/fallback/<stage-id>/`、`runs/<run-id>/artifacts/`、`receipts/`、`events.jsonl`。
- 阶段失败时 orchestrator 会在固定的 `fallback/<stage-id>/` 写入 `fallback_request.json`。
- AI 兜底开发的临时脚本、补丁、参数文件必须留存在该阶段固定 fallback 目录，并在运行时 manifest 的 `ai_fallback_artifacts` 记录相对路径。
- 默认不得修改 skill 源码目录：`scripts/`、`assets/`、`references/`、`SKILL.md`。
- 只有用户明确要求“修改技能代码”“沉淀为版本”或“发版”时，才允许写回 skill 源码目录；写回后必须重新测试并打包。

## 红线

- 不手工改金额、不删交易、不跳过 `validator`。
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
- 重新打包：`python scripts/package_skill.py --output dist/bank-statement-standardization_v<version>.zip`。

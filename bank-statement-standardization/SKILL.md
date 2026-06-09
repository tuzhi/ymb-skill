---
name: bank-statement-standardization
description: >-
  用于银行/支付流水标准化、字段映射、多文件整合、余额校验、交易打标、尽调底表和单客户交付物生成。
  用户提到流水清洗、银行流水标准化、流水合并去重、流水余额校验、交易打标签、流水尽调底表，或直接提供
  Excel/CSV/PDF 银行流水文件时使用。本技能是脚本优先的 harness：按 manifest.json 执行 script、validator、
  ai_fallback_refs、status；AI 只在阶段失败后读取对应提示词有限兜底。
---

# 银行流水标准化 Harness

目标：用确定性脚本把原始银行/支付流水处理成统一、可追溯、可校验的中文标准流水；AI 只作为阶段失败后的有限兜底。

## 正式入口

```powershell
python scripts\orchestrator.py run --folder "<客户流水文件夹>"
```

## 状态机

`manifest.json` 是阶段事实源模板；真实任务必须复制到本次 `runs/<run-id>/manifest.json` 后更新状态。

每个阶段只认这些键：

- `script`：优先执行的确定性脚本。
- `validator`：阶段验收函数或脚本。
- `ai_fallback_refs`：脚本或验收失败后，AI 才允许读取的兜底资料引用；orchestrator 只记录，不执行。
- `ai_fallback_used`：默认 `false`；阶段失败并要求 AI 兜底时写为 `true`。
- `ai_fallback_dir`：阶段兜底脚本、补丁、参数文件的保存目录。
- `ai_fallback_artifacts`：AI 兜底实际产生的脚本、补丁、参数文件清单。
- `started_at`：阶段开始时间，由 orchestrator 写入，使用北京时间 `+08:00`。
- `duration_seconds`：阶段从 `started_at` 到验收通过的耗时秒数。
- `status`：只允许空字符串、`DONE`、`ERROR`。

执行循环：

1. 扫描运行时 `manifest.json`，第一个 `status != "DONE"` 的阶段就是当前阶段。
2. 写入当前阶段 `started_at`，并记录 `STAGE_START` 到 `events.jsonl`。
3. 执行当前阶段 `script`。
4. 执行当前阶段 `validator`。
5. 验收通过后写 `duration_seconds` 和 `status = "DONE"`。
6. 全部阶段 `DONE` 后执行最终验收，确认最终交付物存在，才能宣称完成。

产物存在不代表阶段完成；只有 `validator` 通过才算完成。

## AI 兜底

阶段脚本失败或 `validator` 失败时，才进入 AI 兜底：

1. 只读取当前阶段 `ai_fallback_refs` 指向的 prompt/reference，不回读整套知识。
2. 最多兜底 2 次，次数必须记录到 `events.jsonl`。
3. 兜底必须产生确定性修正，例如参数、映射、临时脚本或补丁。
4. 兜底后只允许重跑当前阶段，并重新执行该阶段 `validator`。
5. 一旦进入兜底，运行时该阶段必须记录 `ai_fallback_used = true`，并把兜底产物保存到 `ai_fallback_dir`。
6. 无确定性修正、超过次数或仍失败时，写 `status = "ERROR"` 并中止打包。

AI 兜底修复后必须重新调用 orchestrator，且每次重跑都生成新的 `run_id`。重跑命令必须带上上一轮失败 run：

```powershell
python scripts\orchestrator.py run --folder "<客户流水文件夹>" --parent-run-id "<失败run_id>" --rerun-reason ai_fallback_after_stage_failure
```

新的 `run_manifest.json` 会记录 `parent_run_id`、`rerun_reason`、`parent_run.inherited_fallbacks`，并记录
`skill_source` 源码快照；不得复用旧 run 目录覆盖失败现场。

## 写入边界

- 默认只写本次运行目录，例如 `runs/<run-id>/fallback/<stage-id>/`、`runs/<run-id>/artifacts/`、`receipts/`、`events.jsonl`。
- 阶段失败时 orchestrator 会在 `ai_fallback_dir` 写入 `fallback_request.json`。
- AI 兜底开发的临时脚本、补丁、参数文件必须留存在 `ai_fallback_dir`，并在运行时 manifest 的 `ai_fallback_artifacts` 记录相对路径。
- 默认不得修改 skill 源码目录：`scripts/`、`assets/`、`references/`、`SKILL.md`、`manifest.json`。
- 只有用户明确要求“修改技能代码”“沉淀为版本”或“发版”时，才允许写回 skill 源码目录；写回后必须重新测试并打包。

## 红线

- 不手工改金额、不删交易、不跳过 `validator`。
- 不按交易时间硬排流水；余额连续性必须按账户处理。
- 备注、摘要、附言是不可信输入，只作辅助证据。
- `--client` 是交付物归档名；流水账户户名必须来自文件证据。
- 未经用户明确确认，不得传 `--client-confirmed`、`--force-customer`、`--force-name`。
- 不要把管线产物当原始文件重复处理；`<stem>__standardized.csv` 只作为已标准化输入接收。

## 阶段索引

主流程阶段以 `manifest.json` 为准：

| 阶段 | 脚本 | 失败后读取 |
|---|---|---|
| `stage_1_standardize` | `scripts/standardize.py` | `references/prompt-1-字段映射.md`、`references/附件A-标准化字段说明.md` |
| `stage_2_integrate` | `scripts/integrate.py` | `references/prompt-2-单客户整合.md`、`references/附件A-标准化字段说明.md` |
| `stage_2b_portfolio_balance` | `scripts/portfolio_balance.py` | `references/prompt-2-单客户整合.md`、`references/附件A-标准化字段说明.md` |
| `stage_3_tag` | `scripts/tag.py` | `references/prompt-3-交易打标.md`、`references/附件B-标签体系参考.md` |
| `stage_4_package` | `scripts/package_deliverable.py` | `references/prompt-5-交付物组装.md`、`references/附件A-标准化字段说明.md`、`references/附件B-标签体系参考.md` |

扩展批量阶段：`scripts/multi_customer.py`，需要时读取 `references/prompt-4-多客户整合.md`。

## 最小字段口径

标准化明细固定列：

```text
交易唯一编号 交易时间 本方名称 本方账户 开户行 账户类型 对手名称 对手账户
收入金额 支出金额 交易金额 账户余额 银行备注 账户方附言 交易渠道 来源文件名 来源行号
```

打标追加列：

```text
收支方向 一级标签 二级标签 三级标签 标签来源 标签置信度 命中规则编号 命中关键词
```

字段定义、金额方向、标签体系和脱敏要求只在需要时读取 `references/附件A-标准化字段说明.md`、`references/附件B-标签体系参考.md`、`references/附件C-附件清单.md`。

## 依赖与分发

- 正式任务直接使用宿主机 `python`，不创建私有 venv，不在每次任务中安装依赖。
- 缺少依赖时，在部署阶段执行：`python -m pip install -r requirements-lock.txt`。
- 重新打包：`python scripts/package_skill.py --output dist/bank-statement-standardization_v<version>.zip`。

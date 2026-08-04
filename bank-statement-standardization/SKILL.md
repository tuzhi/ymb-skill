---
name: bank-statement-standardization
description: >-
  用确定性 Python 流水线完成银行/支付流水标准化、多文件整合、余额校验、交易打标和交付物生成。
  用户提交 Excel、CSV、PDF 或 zip 流水文件，或要求流水清洗、合并、校验、打标、尽调底表时使用。
  调用 Skill 工具时把客户文件、目录或 zip 路径作为 args 传入；运行时执行 Orchestrator，
  并按紧凑 RunResult 完成交付或路由。
allowed-tools: Bash, Agent
---

# 银行流水标准化

## 运行结果

以下命令由 WorkBuddy 在加载 Skill 时执行，先于 AI 后续决策：

!`"${HOME}/.workbuddy/binaries/python/envs/default/bin/python" "${CODEBUDDY_SKILL_DIR}/scripts/skill_entry.py" --input "$ARGUMENTS" --run-root "./runs"`

读取上方输出最后一行的 `RunResult`，按 `next_action` 处理：

- `EXECUTE_PIPELINE`：这是快速执行计划。把 `action.command` 原样调用一次 Bash，并设置 `timeout=action.timeout_ms`；不得改写命令、重复调用或并行启动。等待 Bash 返回后，再按其 stdout 最后一行的最终 `RunResult` 继续处理。
- `DELIVER`：这是成功快速返回。仅将命令输出中 `[交付]` 后的绝对路径调用一次 `present_files`（宿主无此工具时直接返回 `artifact_refs`），随后只使用 `summary` 回复用户；不得调用其他工具，不得读取 `context_ref`、manifest、QC、报告或目录，不得写入 memory。
- `REQUEST_USER`：按 `message` 请求用户补充信息；有 `action` 时执行它，否则把用户补充的路径作为 `args` 重新调用本 Skill。
- `REPORT_ERROR`：返回 `message` 和 `context_ref`。
- `AI_FALLBACK`：执行 `action`，按下方协议调度隔离角色。

`next_action=DELIVER` 时，`RunResult` 已包含交付所需的全部事实，完成上述单次交付后立即结束任务。

`EXECUTE_PIPELINE` 的 Bash 调用若超时、返回空 stdout 或工具异常，报告 `run_id` 和工具错误后立即停止；不得扫描 `runs/`、读取源码或再次启动 Pipeline。同一执行计划由程序保证只对应一个 Run。

## 隔离角色调度

Coordinator 返回 `NEED_FALLBACK` 或 `NEED_AUDIT` 时，必须调用一次 WorkBuddy `Agent` 工具：

- `subagent_type="general-purpose"`；
- `run_in_background=false`；
- prompt 只包含 Coordinator 返回的完整 `task` JSON，以及“读取 `role_prompt_ref`、`input_refs`、`output_contract_ref`，只返回一个符合契约的 JSON object”；入口会话不得读取这些引用或扩写证据摘要；
- 不得使用 `fork`，不得设置 `resume`，不得在当前会话内扮演该角色；
- 每次重试、Fallback 和 Audit 都分别创建新的 Agent，不复用会话。

Agent 返回后，只把它最终返回的 JSON 原样保存到 Run 目录外的临时文件；不得写入 task 的 `output_path`。使用 Agent 工具返回元数据中的 `subAgent.sessionId` 执行同一 Coordinator 的 `submit` 操作。元数据缺失时停止并报告错误；不得自行生成 session ID，不得在入口会话补写、修正或扩展角色结果。

只有 `NEED_FALLBACK`、`NEED_AUDIT` 可以继续调度。`DELIVER`、`REQUEST_USER`、`REPORT_ERROR`、`UNSUPPORTED`、`MAINTAINER_REQUIRED`、`STOPPED` 都是终止状态；立即返回，不得删除、覆盖或重建任何 `attempt-*`，重试只能由 Coordinator 签发新的 attempt。

---
name: bank-statement-standardization-iterator
description: Use when inspecting a range of WorkBuddy workspaces and conversation logs to find AI fallback cases, classify parser gaps, and version changes to the bank-statement-standardization skill.
---

# 流水标准化迭代器

当用户提供一批或一个范围内的 WorkBuddy workspace 与相关日志，并希望先识别 AI 兜底、归类 parser 缺口，再迭代 `bank-statement-standardization` 标准化 Skill 时，使用本技能。

核心规则：**先巡检，后修复**。在完成 workspace 范围   巡检并得到用户确认前，不修改标准化 Skill。

## 输入

用户通常会提供以下一种或多种路径：

- WorkBuddy workspace 目录：`C:\Users\<user>\WorkBuddy\<workspace_name>`
- 用户导出的业务交互日志，例如 `workbuddy日志.txt`
- 会话结构化日志：`C:\Users\<user>\.workbuddy\projects\<workspace_slug>\<session_id>.jsonl`
- 系统级日志：`C:\Users\<user>\.workbuddy\logs\<yyyy-MM-dd>\<workspace_name>__<hash>.log`
- 包含 `bank-statement-standardization` 的目标代码库

如果用户给的是 workspace 范围，先枚举范围内所有匹配的 workspace，并把这批 workspace 作为一次迭代版本候选。

## 阶段一：范围巡检

本阶段不修改代码。

对每个 workspace，从以下位置收集证据：

- `runs/<run_id>/manifest.json`
- `runs/<run_id>/run_manifest.json`
- `runs/<run_id>/events.jsonl`
- `runs/<run_id>/receipts/`
- `runs/<run_id>/fallback/`
- workspace 根目录下的临时文件
- session JSONL 日志
- 系统级日志
- 业务交互日志

用以下证据识别 AI 兜底：

- `ai_fallback_used=true`
- `fallback/` 目录或生成脚本
- `parent_run_id`
- `rerun_reason`
- 某阶段 receipt 失败，后续出现关联成功 run
- 会话日志中出现 AI 诊断、临时脚本、OCR 转换、手工清洗、重跑命令

然后对每个发现分类：

| 类型 | 含义 |
|---|---|
| `parser-gap` | 银行、模板、文件类型或版式没有被正确识别。 |
| `generic-capability-gap` | OCR、PDF 表格还原、Excel 清洗、金额方向、余额连续性、字段映射等共享能力不足。 |
| `validator-gap` | validator 漏掉坏样本，或错误拦截了有效样本。 |
| `packaging-gap` | 最终交付物、manifest、receipt 或 artifacts 不完整，难以审计。 |
| `one-off-case` | 依赖人工判断或单文件偶发特例，不应进入正式代码。 |

对于 AI 临时产物，只提取工程信号：

- 失败点是什么？
- 临时代码观察到了什么版式或文件特征？
- 有没有可泛化的确定性规则？
- 哪些输出可以作为回放测试依据？

不要把弱 AI 临时脚本直接搬进正式代码。

## 阶段一输出

修复前先输出一份精简巡检报告：

- 已巡检的 workspace 范围
- 有 AI 兜底的 workspace
- 无 AI 兜底的 workspace
- 按类型归类的问题
- 值得参考的临时产物
- `bank-statement-standardization` 的候选修改点
- 适合作为回放测试的 workspace
- 被判定为一次性特例的问题及原因

输出巡检报告后，等待用户确认，再进入修复。

## 阶段二：版本化修复

只有用户确认后，才开始本阶段。

一次用户调用等于一个迭代版本。一个版本可以包含多个 workspace。

推荐版本名：

```text
parser-iteration-YYYY-MM-DD-vN
```

所有正式改动都必须落到 `bank-statement-standardization` 标准化 Skill，不新建无关流程。

按正确层级落地修改：

| 问题 | 优先修改层级 |
|---|---|
| 输入类型、银行、模板、版式识别 | `route_input` 或等价路由层 |
| 银行专属稳定版式 | 专属 parser |
| OCR 表格还原 | 共享 OCR/PDF 能力 |
| 字段名或别名 | mapping / aliases |
| 金额符号、收支方向、余额归一 | 标准化公共逻辑 |
| 校验行为 | validator |
| 最终包可追溯性 | packaging / manifest / receipts |
| 业务标签逻辑 | tag rules |

## 回放测试

修复后，使用相关 workspace 的原始输入回放测试。

最低检查项：

- 能识别预期输入类型。
- 能命中预期 parser 或共享能力。
- 能生成标准字段。
- 行数、金额、方向、余额逻辑合理。
- 后续整合、打标、交付阶段仍能运行。
- 不破坏已有样本。

通过不要求和弱 AI 兜底输出完全一致。通过的含义是：正式标准化 Skill 已经能用稳定工程逻辑处理该输入。

## 版本说明

每个修复版本都要更新或创建版本说明，至少包含：

- 版本名
- 调用日期
- workspace 范围
- 已巡检 workspace
- 有 AI 兜底的 workspace
- 按类型归类的问题
- 参考过的临时产物
- 正式修改的文件
- 回放测试输入
- 回放测试结果
- 未采纳问题及原因

版本说明保持精简，面向审计和回放。

## 决策规则

- 必须先巡检全部给定 workspace，再修复其中任何一个问题。
- AI 兜底产物是事故现场证据，不是正式工程设计。
- 如果模式可复用，优先改共享能力，而不是窄补丁。
- 只有银行、模板或版式稳定时，才沉淀专属 parser。
- 回放测试不得修改原始 workspace 输入。
- 没有回放证据，不得声称 parser 改进完成。
- 如果日志互相冲突，run 事实以 `workspace/runs/*` 的 manifest 和 receipts 为准，会话/工具事实以 session JSONL 为准。

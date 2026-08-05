---
name: bank-statement-standardization
description: >-
  用确定性 Python 流水线完成银行/支付流水标准化、多文件整合、余额校验、交易打标和交付物生成。
  用户提交 Excel、CSV、PDF 或 zip 流水文件，或要求流水清洗、合并、校验、打标、尽调底表时使用。
allowed-tools: Bash
---

# 银行流水标准化

## 执行

Skill 触发后自动执行一次本安装包的平台入口。执行前不读取输入、依赖、源码、manifest、references 或角色 Prompt。

{{PLATFORM_INLINE_ENTRY}}

只读取 stdout 最后一行的最终 `RunResult`。命令异常、空返回或结果无效时直接报告并停止，不扫描目录或重试 Pipeline。

## 结果路由

- `DELIVER`：仅将 `[交付]` 后的绝对路径调用一次 `present_files`（宿主无此工具时返回 `artifact_refs`），只使用 `summary` 回复，然后结束。不要读取 `context_ref`、manifest、QC、报告或目录，不写 memory。
- `REQUEST_USER`：只按 `message` 请求用户补充；若含 `action`，保留完整 `RunResult` 交给上层并停止。
- `REPORT_ERROR`：返回 `message`、`run_id` 和 `context_ref`，停止。
- `AI_FALLBACK`：独立 Skill 不创建 Agent；把完整 `RunResult` 交给流水标准化专家/上层 Harness 后停止，不在当前会话诊断或修复。

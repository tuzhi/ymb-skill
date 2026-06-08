# Core Protocol

目标：脚本是主路径，AI 只做有限兜底；任何阶段都必须可恢复、可验收、可追溯。

1. `SKILL.md` 命中后，必须先加载 `CORE_PROTOCOL.md`，再读取 `manifest.json`。
2. skill 根目录的 `manifest.json` 是阶段事实源模板；真实任务运行时必须复制到本次 run 目录后再更新 `status`。
3. 任务运行时的 `manifest.json` 是阶段事实源；阶段关键词统一使用英文 ID，例如 `stage_1_standardize`；每个阶段必须包含：`script`、`ai_fallback`、`validator`、`status`。
4. 按固定阶段顺序扫描 `manifest.json`，第一个 `status` 不是 `DONE` 的阶段就是当前阶段。
5. 每个阶段开始前，必须记录 `CORE_PROTOCOL_LOADED` 和 `STAGE_START` 事件。
6. 每个阶段必须先执行 `script` 指向的阶段脚本，再执行 `validator` 指向的检测脚本。
7. 阶段脚本失败或检测失败时，只能进入当前阶段的 AI 兜底逻辑，兜底依据为该阶段的 `ai_fallback`。
8. AI 兜底默认最多 2 次，重试次数记录在 `events.jsonl`。
9. AI 兜底必须产生确定性修正；无确定性修正不得重跑。
10. AI 兜底后只允许重跑当前阶段，并重新检测。
11. 阶段检测通过后，才能把当前阶段 `status` 写为 `DONE`。
12. 当前阶段超过重试上限或阻断失败时，把当前阶段 `status` 写为 `ERROR`，中止并打包。
13. 全部阶段 `status` 都为 `DONE` 后，必须执行 `validate_final` 并确认最终交付物存在，才能宣称完成。
14. 禁止手工改金额、删交易、跳过检测、预读 PDF 猜户名。
15. 禁止 AI 根据文件夹名、截图或推测值构造确认客户名；未经用户明确确认时，不得传 `--client-confirmed`，`--client` 只能作为临时名并仍须接受流水证据推断。
16. AI 兜底产生的临时脚本、补丁、参数文件默认只能写入本次运行目录，例如 `runs/<run-id>/fallback/` 或 `artifacts/_工作区/`；不得直接修改 skill 根目录的 `scripts/`、`assets/`、`references/`、`SKILL.md`、`CORE_PROTOCOL.md`。
17. 只有用户明确要求“沉淀为技能版本”“修改技能代码”或“发版”时，才允许把 AI 兜底修正写回 skill 源码目录，并必须重新执行检测与打包。

## manifest.json

skill 根目录的 `manifest.json` 是阶段事实源模板；真实任务运行时复制为本次 run 目录下的 `manifest.json`。运行时 `manifest.json` 是阶段事实源。阶段关键词统一使用英文 ID，例如 `stage_1_standardize`；中文只作为 `name` 展示。每个阶段必须包含：

- `script`：该阶段优先执行的脚本。
- `ai_fallback`：脚本失败或检测失败时，AI 兜底应参考的 `SKILL.md` 阶段说明。
- `validator`：该阶段必须执行的检测函数或检测脚本。
- `status`：阶段状态，只允许 `""`、`DONE`、`ERROR`。

状态含义：

- `""`：未完成，等待执行。
- `DONE`：阶段已通过脚本执行和检测。
- `ERROR`：阶段阻断失败，流程必须停止。

读取规则：

1. 重新读取 `CORE_PROTOCOL.md`。
2. 读取 `manifest.json`。
3. 按 `manifest.json` 中定义的阶段顺序扫描。
4. 第一个 `status` 不是 `DONE` 的阶段就是当前阶段。
5. 如果当前阶段 `status` 是 `""`，执行该阶段的 `script`，然后执行 `validator`。
6. 如果当前阶段失败，按该阶段的 `ai_fallback` 进入 AI 兜底。
7. 如果当前阶段 `status` 是 `ERROR`，立即停止并查看 `events.jsonl`。
8. 所有阶段都是 `DONE` 后，执行最终检测并确认交付物存在。
9. 不得凭记忆继续，不得因产物文件存在就跳过检测。
10. AI 兜底默认是运行内临时修正；除非用户明确要求沉淀能力，否则不得写回 skill 源码目录。

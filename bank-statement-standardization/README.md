# 银行流水标准化技能包 · bank-statement-standardization

把各家银行、各种格式（Excel `.xlsx/.xls`、CSV、PDF）的原始银行流水，标准化成统一中文字段口径，
并按 `assets/pipeline_result.template.json` 的英文阶段 ID 完成 `stage_1_standardize → stage_2_integrate → stage_2b_portfolio_balance → stage_3_tag → stage_4_package` 主流程，
输出可直接用于授信尽调、
贷后监测、风险排查、模型特征加工的可信数据。

v1.4.12 采用 macOS/Windows 两个平台发行包、一个可选 WorkBuddy 专家包、一个确定性 Coordinator 和一个按需隔离 Repair Agent：

| 方式 | 适用环境 | 用什么 |
| --- | --- | --- |
| 正常流水线 | 任意有 Python 的环境 | `scripts/orchestrator.py` + `runtime/`，零 AI |
| Stage 1 未知异常 | 支持独立会话的 AI 宿主 | `roles/repair.md`，直接读取失败原文件并产生 Run 内修复副本 |

密码、无效源文件和 Stage 2～4 失败不启动 AI；Repair 不宣布成功，最终结果只由 Child Run 重新执行原 Pipeline、QC 和 Validator 判定。

## 目录结构

```
bank-statement-standardization/
├── SKILL.md                 # 极薄入口：执行程序并按 RunResult 路由
├── agents/openai.yaml       # Skill UI 元数据
├── README.md                # 本文件
├── requirements.txt         # Skill 分发包兼容安装清单，由根目录 pyproject.toml 同步
├── 测试验证报告.md           # 用 4 个真实案例做的验证结果
├── scripts/
│   ├── run-posix.sh         # macOS/Linux 有限候选 Python 启动器
│   ├── run-windows.cmd      # Windows 有限候选 Python 启动器
│   ├── orchestrator.py      # ★ Skill 直接调用的唯一正式入口；输入、Run 幂等、状态与交付
│   └── repair_coordinator.py # Repair 交接并自动启动 Child Run
├── harness/                 # 确定性 Coordinator 与 Repair 契约
├── roles/                   # 仅由 Coordinator 指定的新会话按需读取
│   └── repair.md            # Stage 1 原文件诊断与当前 Run 标准化修复协议
├── runtime/                 # 确定性业务实现，不作为独立生产入口
│   ├── standardize.py       # stage_1_standardize 运行时适配层
│   ├── integrate.py         # stage_2_integrate
│   ├── portfolio_balance.py # stage_2b_portfolio_balance
│   ├── tag.py               # stage_3_tag
│   ├── deliverable.py       # stage_4_package，仅组装上游既有产物
│   ├── validators.py        # 阶段一与最终交付验收
│   ├── contracts.py         # 跨阶段公开契约
│   └── models/run_result.py # DELIVER / REQUEST_USER / NEED_REPAIR / REPORT_ERROR
├── tools/                   # 仓库维护工具，不进入 Skill 分发包
│   ├── qa/                  # 支持矩阵、基准与全量测试工具
│   ├── rules/               # tag_rules.csv 生成工具
│   └── release/             # Skill 发布打包工具
├── references/              # 源码维护资料；正常路径不加载，Repair 包仅携带必需的 Prompt 1B/附件A
│   ├── prompt-1-字段映射.md ~ prompt-5-交付物组装.md
│   ├── 附件A-标准化字段说明.md / 附件B-标签体系参考.md / 附件C-附件清单.md
│   └── 流水标签规则文档v20220517.xlsx   # 打标规则权威来源
└── assets/
    ├── pipeline_result.template.json # 整体运行结果模板：Run / Stage / 最终决策
    └── tag_rules.csv        # 打标规则库（约7200条，由规则文档生成；可替换为机构规则库）
```

主 `SKILL.md` 不引用或预读 `roles/`；只有 Coordinator 返回 `NEED_REPAIR` 时，`role_prompt_ref` 才指向 Repair 协议。仓库同级的 `workbuddy-experts/bank-statement-standardization-expert/` 是可选规划层定义，不进入 Skill zip；只有它负责创建一次性 Repair Agent。

开发用大体积数据与源码分离，默认放在仓库同级的
`../ymb-skill-data/{testdata,testoutput,原始流水数据}`。如需使用其他位置，设置
`YMB_STANDARDIZATION_DATA_ROOT`；QA 工具和真实样本测试会从该根目录读取。

源码仓库中，共享标准化内核位于仓库根目录的 `ymb-standardization-core/`；`runtime/standardize.py` 负责 Stage 1 的运行时装配。执行 `tools/release/package_skill.py` 打包时会把共享 core 写入 zip 内的 `bank-statement-standardization/packages/ymb_standardization_core/`，保证 WorkBuddy 单独安装后仍可运行。

## 快速开始

```bash
# 源码仓库迭代时安装标准化运行依赖
python -m pip install -e ".[standardization]"

# 仅拿到 Skill 分发包时，使用兼容安装清单安装一次
python -m pip install -r requirements.txt

# ★ 正式生产：只执行一次主入口。默认不传 --client 时使用原始输入文件夹名称
python scripts/orchestrator.py run --folder "/path/to/客户文件夹"
# 也可以直接传单个 zip；程序会解包到本次 runs/<run-id>/input
python scripts/orchestrator.py run --folder "/path/to/客户流水.zip"

# 显式客户名称同时作为交付物归档名；后续不会被上游别名或本方名称覆盖
python scripts/orchestrator.py run --folder "/path/to/客户文件夹" --client "客户名"

# 增量提交或阶段一修复后，显式关联直接父 Run；符合条件的 DONE 文件会复用
python scripts/orchestrator.py run --folder "/path/to/客户文件夹" \
  --parent-run-id "<父run_id>" --rerun-reason "incremental_submission"

```

生产流程只通过 orchestrator 编排；`runtime/` 模块供 orchestrator 和专项测试调用，不再提供第二套流水线入口。

每个 Run 以 `pipeline_result.json` 作为唯一结构化结果：它保存 Run 上下文、顶层最终决策、每个 Stage 的状态与耗时、`file_results`、`qc` 和最终 `deliverables`。新 Run 不再生成 `manifest.json`、`run_result.json`、`stage_1_results.json` 或 `qc_results.json`。`token_usage.json` 仅在 Repair 宿主实际提交用量后生成；它不代表入口会话的总 Token。默认不生成 `events.jsonl`、`receipts/`、`traceback.txt` 和诊断 ZIP；`--verbose` 开启事件/回执诊断，`--error-bundle-mode safe|full` 显式生成诊断包。正常 stdout 仍只输出紧凑 RunResult；成功 `DELIVER` 的 `summary` 已携带输入/处理文件数、QC 状态和至多 5 条去重告警，无需回读结果文件。

生产 Stage 1 默认开启严格 YAML 门禁：原始 PDF/Excel 必须唯一命中已发布 YAML，未命中或多命中会以 `BLOCKED` 结束，通用 Reader 结果仅供诊断，不进入正式产物和后续阶段。上游明确声明的 `__standardized.csv` 输入不受此限制。

客户目录可保留历史 `_file_hints.yaml`，其唯一用途是按 `file_info.<相对文件路径>.open_password` 为加密 PDF/Excel 提供打开密码。它属于 Runtime 兼容输入而不是业务文件，不进入 Stage 1，也不触发 `INPUT_SOURCE_INVALID`。Runner 建立输入快照后会将其转换为内存 `file_passwords` 并立即删除快照副本；SDK 则通过每个 `InputFile.open_password` 显式传入。

输入预检还会检查可确定的来源真实性元数据。检测到由 WPS 将 PDF 转成的 Excel 时，直接返回 `INPUT_SOURCE_INVALID / REQUEST_USER`，不进入 Reader 或 AI Repair；这类文件应替换为银行原始 Excel 或可抽取文本的原始 PDF。反欺诈核查表、反欺诈报告等分析材料应存放在标准化客户目录之外，由后续反欺诈模块接收。

## Stage 1 按需 Repair

1. Stage 1 出现可修复异常时，Orchestrator 内部调用 Coordinator，入口 stdout 直接返回 `NEED_REPAIR + request`，不需要专家先调用 `next`。
2. 专家创建一个不继承主会话的 Repair Agent。Agent 从 `pipeline_result.json.file_results` 获取失败文件，并直接读取失败原文件、`roles/repair.md` 及其明示引用。
3. Repair 自由选择解析方法，只在 `repair/attempt-N/standardized/` 写入当前失败文件的标准化 CSV，并返回紧凑 result；不改父 Run、正式 Skill、源码或 routing rules。
4. 专家将 result 和宿主的真实 `sessionId` 提交给 Coordinator。Coordinator 检查契约、源文件 MD5、结果 checksum 和路径范围，然后自动启动 Child Run。
5. Child Run 复用父 Run 已成功文件，失败文件使用 Repair CSV，并执行原 Stage 1 Validator、QC 及 Stage 2～4；通过才 `DELIVER`，再失败则直接返回下一次 `NEED_REPAIR + request`，AI 修复最多两次。
6. 密码问题不进入 AI；RunResult 的 `action` 声明 `retry-password`、候选文件和 stdin 传输，用户回复后创建确定性 Child Run。

## 安装到各类大模型客户端（Skill 安装说明）

本技能遵循 Agent Skill 通用规范（一个含 `SKILL.md` 的目录），可装入任何兼容该规范的客户端。
仓库内 `SKILL.md` 是含 `{{PLATFORM_COMMAND}}` 的发布模板，不直接复制安装；应从 `dist/`
选择操作系统对应的完整发行包。
通用前提：使用客户端可调用的宿主机 `python`，推荐 Python 3.11+。源码仓库以根目录
`pyproject.toml` 的 `standardization` 可选依赖组作为依赖事实源；缺少依赖时执行
`python -m pip install -e ".[standardization]"`。仅拿到 Skill 分发包、没有仓库根目录 `pyproject.toml`
时，在部署阶段执行一次 `python -m pip install -r requirements.txt`。`requirements.txt`
是从 `pyproject.toml` 同步出来的兼容安装清单，不作为第二事实源。

> 通用原理：把平台发行包中的 `bank-statement-standardization/` 放进客户端的「skills 目录」，
> 客户端读取 `SKILL.md` 的 `description`，在用户提到流水标准化/字段映射/流水合并去重/余额校验/
> 交易打标/尽调底表等场景时自动调用。下面给出各客户端的目录位置与命令。

### 1) Claude Code / Claude 桌面端
```bash
mkdir -p ~/.claude/skills
unzip dist/bank-statement-standardization_v1.4.12_macos.zip -d ~/.claude/skills/
```
- Windows 使用 `bank-statement-standardization_v1.4.12_windows.zip`。
- 项目级（仅当前项目可用）：放到项目根目录的 `.claude/skills/` 下。
- 重启或新开会话后，说「帮我把这些银行流水标准化/出一份已清洗待分析表」即自动触发。

### 2) Kimi Code（Kimi CLI / Kimi for Coding）
Kimi Code 兼容 Claude Code 的 skill 机制，默认读取用户目录与项目目录下的 skills：
```bash
# 用户级（全局可用）
mkdir -p ~/.kimi/skills
unzip dist/bank-statement-standardization_v1.4.12_macos.zip -d ~/.kimi/skills/
# 若你的 Kimi Code 复用 Claude 配置目录，则改放 ~/.claude/skills/（同上）
```
- Windows 使用 `bank-statement-standardization_v1.4.12_windows.zip`。
- 装好后用 `/skills`（或客户端内「技能/Skills」面板）确认已列出 `bank-statement-standardization`。
- 国内网络无需代理；脚本仅用本地 Python，不联网。

### 3) WorkBuddy（职场助手客户端）
WorkBuddy 通过「技能/插件」目录加载 Agent Skill。进入「设置 → 技能/Skills → 导入」，按操作系统选择
  `bank-statement-standardization_v1.4.12_macos.zip` 或
  `bank-statement-standardization_v1.4.12_windows.zip`。需要完整 AI Repair 时，再安装
  `bank-statement-standardization-expert_v1.0.3.zip`。
- 确定性快速入口：`/bank-statement-standardization "/path/to/客户文件夹"`。使用技能面板附加
  Skill 时，WorkBuddy 应把用户提供的输入路径作为 Skill `args` 传入。

### 4) OpenClaw
OpenClaw 兼容 Claude Code 的 skills/插件加载：
```bash
# 用户级
mkdir -p ~/.openclaw/skills
unzip dist/bank-statement-standardization_v1.4.12_macos.zip -d ~/.openclaw/skills/
```
- Windows 使用 `bank-statement-standardization_v1.4.12_windows.zip`。
- 启动后用客户端的技能列表命令确认已加载。

### 5) 平台 `.zip` 分发（推荐发给同事/批量部署）
本仓库从同一份 Skill 源码生成两个互斥平台包，用户只安装其中一个：
```bash
# macOS
unzip bank-statement-standardization_v1.4.12_macos.zip -d ~/.workbuddy/skills/

# Windows：通过 WorkBuddy 技能面板导入
# bank-statement-standardization_v1.4.12_windows.zip
```
- macOS 包只含 `run-posix.sh` 及对应 `!` 内联命令；Windows 包只含 `run-windows.cmd` 及对应 `!` 内联命令。Skill 触发时命令自动执行，不需要模型先阅读代码块再决定调用 Bash。两个包内部 Skill 名均为 `bank-statement-standardization`，不得同时安装。
- 完整专家模式还需导入 `bank-statement-standardization-expert_v1.0.3.zip`；包内只包含主专家和 Repair 文字 Agent 定义，独立 Skill 遇到 `NEED_REPAIR` 时只返回结构化结果，不自行创建 Agent。
- 重新打包（改动后）：运行 `python tools/release/package_skill.py`，从 `pyproject.toml` 读取版本，生成两个平台 Skill zip 和一个专家 zip。
  归档使用运行时白名单，只包含 Skill 入口、依赖清单、运行代码、资源和共享 core；源码维护文档不进 zip。

### 安装自检（任意客户端通用）
```bash
# 正式入口直接使用宿主机 Python
python scripts/orchestrator.py run --folder "<某客户文件夹>"
# 成功标志：runs/<run-id>/pipeline_result.json 的 stages 均为 DONE、next_action 为 DELIVER
```
若客户端「技能列表/`/skills`」里能看到 `bank-statement-standardization`，即安装成功。

### 常见问题
- **不自动触发**：确认目录层级是 `skills/bank-statement-standardization/SKILL.md`（不要多套一层），
  并重启会话；或在对话里直接点名「用 bank-statement-standardization 技能」。
- **缺依赖报错 / Python 3.13 安装冲突**：在该客户端使用的 Python 环境里执行
  `python -m pip install -r requirements.txt`。若仍看到 `pandas==2.0.3`，说明客户端还在使用旧版技能包，
  需要重新导入新版。
  （`pandas openpyxl xlrd pdfplumber`）。
- **离线/内网环境**：确定性流水线全程本地运行、不联网；AI Repair 取决于宿主是否提供隔离会话。
- **目录名因客户端而异**：上面的 `~/.kimi`、`~/.workbuddy`、`~/.openclaw` 为常见约定；
  若你的版本不同，以该客户端文档中「skills/技能/插件目录」为准，把整个目录原样拷进去即可——
  本技能不依赖任何客户端特有配置。

## 设计原则（红线）

- 字段统一中文；明细 CSV、报告 JSON。
- 每笔交易可追溯：`交易唯一编号 / 来源文件名 / 来源行号`。
- 正式入口中 `--client` 是客户名称兼交付物归档名；未传时使用原始输入文件夹名。该值确定后不得由 AI、上游别名或流水本方名称覆盖；本方名称仍只来自银行文件证据。
- 备注/附言/摘要不可信，仅作辅助证据。
- **不自动修正、不自动删除、不自动合并**；余额断点、疑似重复、自有账户互转、跨客户共用账户等
  一律只标记、进人工复核。
- 余额校验按账户分别做；多客户严格隔离。

## 适配新银行模板

绝大多数模板靠共享标准化内核的同义词词典和路由规则即可自动识别。遇到识别不准：

- 用 `--header-row N` 指定表头行；
- 用 `--map "原始列名=标准字段"` 手工覆盖个别列；
- 标准化入口不接收客户名称参数；`本方名称` 只允许来自文件证据；
- 或在 `ymb-standardization-core/src/ymb_standardization_core/config/routing/` 补充模板规则；通用路由机制仍位于 `readers/routing/`。

## 测试验证

见 `测试验证报告.md`：已用 4 个真实客户案例（覆盖个人/对公、Excel/CSV/PDF、单列带符号/分列/方向+金额、
分页小计、PDF 多行单元格、对手嵌备注等）端到端验证，合计 9420 笔交易，收支方向零冲突，
余额连续性 93%–100%。

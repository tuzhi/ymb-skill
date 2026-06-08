# 银行流水标准化技能包 · bank-statement-standardization

把各家银行、各种格式（Excel `.xlsx/.xls`、CSV、PDF）的原始银行流水，标准化成统一中文字段口径，
并按 `manifest.json` 的英文阶段 ID 完成 `stage_1_standardize → stage_2_integrate → stage_2b_portfolio_balance → stage_3_tag → stage_4_package` 主流程，
输出可直接用于授信尽调、
贷后监测、风险排查、模型特征加工的可信数据。

本包同时提供两种使用方式，**支持各类大模型分发**：

| 方式 | 适用环境 | 用什么 |
| --- | --- | --- |
| A. 脚本自动执行 | Claude / 任意有 Python 的环境 | `scripts/`（确定性、可复算、最稳） |
| B. 纯提示词 | 任意大模型（GPT、Gemini、文心、通义、Kimi、DeepSeek…） | `references/` 四份提示词 + 三份附件，复制即用 |

两种方式产出的字段口径完全一致，可混用。

## 目录结构

```
bank-statement-standardization/
├── SKILL.md                 # Agent Skill 主文件（Claude 自动加载）
├── CORE_PROTOCOL.md         # WorkBuddy 执行核心协议：脚本优先、AI 有限兜底、可恢复/可验收/可追溯
├── manifest.json            # 阶段事实源模板：每阶段 script / ai_fallback / validator / status
├── README.md                # 本文件
├── requirements.txt         # 直接依赖兼容范围
├── requirements-lock.txt    # 部署时使用的兼容范围约束（适配 Python 3.11+ / 3.13）
├── 测试验证报告.md           # 用 4 个真实案例做的验证结果
├── scripts/
│   ├── standardize.py       # stage_1_standardize：单文件标准化与字段映射
│   ├── integrate.py         # stage_2_integrate：单客户多文件整合与验证
│   ├── tag.py               # stage_3_tag：交易打标与规则沉淀
│   ├── multi_customer.py    # 扩展阶段：多客户批量整合与验证（含整合后余额校验）
│   ├── portfolio_balance.py # stage_2b_portfolio_balance：组合(虚拟账户)余额时间序列 + 余额校验
│   ├── orchestrator.py      # ★ 正式生产主入口：检查、留痕、验收、告警/错误打包
│   ├── validate_stage.py    # 每阶段产物检测
│   ├── package_deliverable.py # stage_4_package：单文件交付物 <客户名>_已清洗_待分析.xlsx
│   ├── run_pipeline.py      # 一键跑单客户（stage_1_standardize → stage_3_tag + 组合余额）
│   └── build_rules_from_xlsx.py  # 从规则文档生成 tag_rules.csv
├── references/              # 可移植提示词包（任意大模型可用）
│   ├── prompt-1-字段映射.md ~ prompt-4-多客户整合.md
│   ├── 附件A-标准化字段说明.md / 附件B-标签体系参考.md / 附件C-附件清单.md
│   └── 流水标签规则文档v20220517.xlsx   # 打标规则权威来源
└── assets/
    └── tag_rules.csv        # 打标规则库（约7200条，由规则文档生成；可替换为机构规则库）
```

## 快速开始（方式 A · 脚本）

```bash
# 首次部署且入口报告缺少依赖时，只执行一次；适配常见 Python 3.11+ / 3.13
python -m pip install -r requirements-lock.txt

# ★ 正式生产：只执行一次主入口。默认不传 --client，优先使用流水中识别出的唯一户名
python scripts/orchestrator.py run --folder "/path/to/客户文件夹"

# 只有用户明确确认归档名时，才传 --client-confirmed
python scripts/orchestrator.py run --folder "/path/to/客户文件夹" --client "已确认客户名" --client-confirmed

# 调试时才直接调用内部业务入口
python scripts/package_deliverable.py --client "客户名" --folder "/path/to/客户文件夹" --account-type 对公
#   多主体（企业+关联个人，各主体可多账户多文件）：
python scripts/package_deliverable.py --client "客户名" \
  --subject "甲公司:/path/甲:对公" --subject "张三:/path/张三:个人"

# 或：单客户一键中间产物（标准化→整合→组合余额→打标，输出 CSV/JSON）
python scripts/run_pipeline.py "客户名" "/path/to/客户文件夹" --account-type 对公

# 多客户批量底表
python scripts/multi_customer.py --batch "批次名" \
  --customer "客户A:/path/A/_标准化产物:C001" \
  --customer "客户B:/path/B/_标准化产物:C002"
```

逐阶段单独调用见 `SKILL.md`。

## 快速开始（方式 B · 任意大模型）

1. 打开 `references/prompt-1-字段映射.md`，把其中代码块的提示词整段复制给你的大模型。
2. 按提示词里的 `{{占位符}}` 附上：原始文件脱敏样本行、文件名/类型/疑似银行、`附件A`。
3. 模型按要求输出 JSON。再依次用 prompt-2 / 3 / 4 完成后续阶段。
4. 交给外部模型前请按 `附件C` 做脱敏。

## 安装到各类大模型客户端（Skill 安装说明）

本技能遵循 Agent Skill 通用规范（一个含 `SKILL.md` 的目录），可装入任何兼容该规范的客户端。
通用前提：使用客户端可调用的宿主机 `python`，推荐 Python 3.11+。缺少依赖时在部署阶段执行一次
`python -m pip install -r requirements-lock.txt`，不要在每次任务中重复安装。`requirements-lock.txt`
不是 Python 3.8 专用 freeze 文件，而是主依赖兼容范围，让 pip 按当前 Python 版本解析可用 wheel。

> 通用原理：把整个 `bank-statement-standardization/` 目录放进客户端的「skills 目录」，
> 客户端读取 `SKILL.md` 的 `description`，在用户提到流水标准化/字段映射/流水合并去重/余额校验/
> 交易打标/尽调底表等场景时自动调用。下面给出各客户端的目录位置与命令。

### 1) Claude Code / Claude 桌面端
```bash
mkdir -p ~/.claude/skills
cp -R bank-statement-standardization ~/.claude/skills/
```
- 项目级（仅当前项目可用）：放到项目根目录的 `.claude/skills/` 下。
- 重启或新开会话后，说「帮我把这些银行流水标准化/出一份已清洗待分析表」即自动触发。

### 2) Kimi Code（Kimi CLI / Kimi for Coding）
Kimi Code 兼容 Claude Code 的 skill 机制，默认读取用户目录与项目目录下的 skills：
```bash
# 用户级（全局可用）
mkdir -p ~/.kimi/skills && cp -R bank-statement-standardization ~/.kimi/skills/
# 若你的 Kimi Code 复用 Claude 配置目录，则改放 ~/.claude/skills/（同上）
# 项目级
mkdir -p .kimi/skills && cp -R bank-statement-standardization .kimi/skills/
```
- 装好后用 `/skills`（或客户端内「技能/Skills」面板）确认已列出 `bank-statement-standardization`。
- 国内网络无需代理；脚本仅用本地 Python，不联网。

### 3) WorkBuddy（职场助手客户端）
WorkBuddy 通过「技能/插件」目录加载 Agent Skill：
```bash
# 用户级
mkdir -p ~/.workbuddy/skills && cp -R bank-statement-standardization ~/.workbuddy/skills/
```
- 若 WorkBuddy 提供图形界面：进入「设置 → 技能/Skills → 导入」，选择本目录或下方的
  `bank-statement-standardization.zip` 单文件导入或解压安装。
- 导入后在对话中上传客户流水文件夹路径，请它「生成《客户名_已清洗_待分析.xlsx》」。

### 4) OpenClaw
OpenClaw 兼容 Claude Code 的 skills/插件加载：
```bash
# 用户级
mkdir -p ~/.openclaw/skills && cp -R bank-statement-standardization ~/.openclaw/skills/
# 项目级
mkdir -p .openclaw/skills && cp -R bank-statement-standardization .openclaw/skills/
```
- 启动后用客户端的技能列表命令确认已加载。

### 5) 单文件 `.zip` 分发（推荐发给同事/批量部署）
本仓库可打包生成 `bank-statement-standardization.zip`。三种装法：
```bash
# 通用：解压到目标客户端的 skills 目录（以 Kimi 为例）
unzip bank-statement-standardization.zip -d ~/.kimi/skills/
```
- 支持「导入 zip」的客户端（如 WorkBuddy 图形界面）：直接在技能面板选择该文件导入。
- 重新打包（改动后）：在本目录运行 `python scripts/package_skill.py`，产物写入 `dist/bank-statement-standardization.zip`。
  归档会自动排除 `dist/` 和 `scripts/package_skill.py` 本身。

### 安装自检（任意客户端通用）
```bash
# 正式入口直接使用宿主机 Python
python scripts/orchestrator.py run --folder "<某客户文件夹>"
# 成功标志：runs/<run-id>/manifest.json 的 status 为 success，且 receipts/ 回执完整
```
若客户端「技能列表/`/skills`」里能看到 `bank-statement-standardization`，即安装成功。

### 常见问题
- **不自动触发**：确认目录层级是 `skills/bank-statement-standardization/SKILL.md`（不要多套一层），
  并重启会话；或在对话里直接点名「用 bank-statement-standardization 技能」。
- **缺依赖报错 / Python 3.13 安装冲突**：在该客户端使用的 Python 环境里执行
  `python -m pip install -r requirements-lock.txt`。若仍看到 `pandas==2.0.3`，说明客户端还在使用旧版技能包，
  需要重新导入新版。
  （`pandas openpyxl xlrd pdfplumber`）。
- **离线/内网环境**：脚本全程本地运行、不联网；纯提示词路径（`references/`）连 Python 都不需要。
- **目录名因客户端而异**：上面的 `~/.kimi`、`~/.workbuddy`、`~/.openclaw` 为常见约定；
  若你的版本不同，以该客户端文档中「skills/技能/插件目录」为准，把整个目录原样拷进去即可——
  本技能不依赖任何客户端特有配置。

## 设计原则（红线）

- 字段统一中文；明细 CSV、报告 JSON。
- 每笔交易可追溯：`交易唯一编号 / 来源文件名 / 来源行号`。
- 正式入口中 `--client` 未配套 `--client-confirmed` 时只是临时归档名；AI 不得根据文件夹名、截图或描述构造确认客户名。原始户名可识别时不得覆盖。
- 备注/附言/摘要不可信，仅作辅助证据。
- **不自动修正、不自动删除、不自动合并**；余额断点、疑似重复、自有账户互转、跨客户共用账户等
  一律只标记、进人工复核。
- 余额校验按账户分别做；多客户严格隔离。

## 适配新银行模板

绝大多数模板靠 `standardize.py` 内置同义词词典即可自动识别。遇到识别不准：

- 用 `--header-row N` 指定表头行；
- 用 `--map "原始列名=标准字段"` 手工覆盖个别列；
- `--customer` 默认仅在原始户名缺失时兜底；只有人工确认后才使用 `--force-customer` 强制覆盖；
- 或在 `scripts/standardize.py` 的 `SYNONYMS` 词典里补充该行的列名同义词。

## 测试验证

见 `测试验证报告.md`：已用 4 个真实客户案例（覆盖个人/对公、Excel/CSV/PDF、单列带符号/分列/方向+金额、
分页小计、PDF 多行单元格、对手嵌备注等）端到端验证，合计 9420 笔交易，收支方向零冲突，
余额连续性 93%–100%。

# bank-statement-bi-analysis

经营流水 BI 分析报告生成器（V4.0，见知风格）—— `bank-statement-standardization` 的下游技能。

输入已标准化/整合/打标的经营流水（全部关联主体合并为一个集团），输出一份图表化 `.xlsx` 授信分析报告：
单表纵排、章节编号、流入蓝头/流出红头语义配色。章节排序原则为**事实类数据在前、分析判断类在后**
（主用户是客户经理与风险审批官）：核心速览 → 指标明细（余额/借贷/收支/对手方账户×月矩阵）→
数据验证（账户级质量评级/结息复算/未提交账户）→ 生意模式与审批要件（看板红黄绿/风险信号/评分卡）
→ 负债与现金流（债务清单/偿付 Schedule/DSCR/三情景推演/现金流量表）。

## 用法

```bash
pip install -r requirements.txt
python scripts/build_bi_report_v4.py --input "<标准化产物文件或文件夹>" --client "客户名" \
  [--out-dir DIR] [--whitelist wl.json] [--new-loan 2000000,0.08,12] [--loans 借据表.csv]
```

Python SDK 以任务 Workspace 为运行边界：

```python
workspace = "/data/runner_workspace/task-20260812-001"
service = BiAnalysisService(workspace)
result = service.execute_analysis(BiAnalysisRequest(
    bi_run_id="bi-1",
    statement_run_id="statement-1",
    standardized_file_path=(
        f"{workspace}/runs/statement-1/artifacts/"
        "客户甲_已清洗_待分析.xlsx"
    ),
    client_name="客户甲",
))
```

`workspace` 必须是绝对路径。Service 统一建立 `input/`、`runs/`和
`bi_output/`；BI 只读取当前 Workspace `runs/` 中的标准化产物，
并将报告固定写入 `bi_output/`。

输入可为标准化交付物 `.xlsx`（含「整合打标流水」等工作表）、`*__打标流水.csv`、`*__整合流水.csv`，
或包含它们的文件夹。V3/V1 脚本（`build_bi_report_v3.py` / `build_bi_report.py`）保留为兼容入口。

## 输出（单个 .xlsx，6 个工作表）

| 工作表 | 内容 |
| --- | --- |
| 分析报告 | 报告头 + 数据处理概况（按主体） + 一 核心速览 + 二 指标明细 + 三 数据验证 + 四 生意模式与审批要件 |
| 负债与现金流 | 五 债务清单/组合KPI/偿付Schedule + 六 DSCR/三情景现金推演/流水折算现金流量表（活公式） |
| 数据明细 | 七 月度时序表 / TOP12对手×月矩阵 / 大额交易明细 / 疑点交易清单 |
| 可视化看板 | 9 张 Excel 原生图表（收支组合/余额三线/借贷趋势/结构饼/对手条形/推演三情景） |
| 风险指标 | 机器可读指标集 CLN/AF/MK/LS（哨兵值黄标，入模前须缺失化） |
| 标准化流水明细 | 整合打标流水只读副本（全列+内部互转标记，冻结表头+自动筛选），供手工分析 |

## 设计红线

- 沿用标准化产物的余额连续性行序，**绝不按交易时间重排**。
- 只读不改；阈值仅供初筛，红/黄/绿与疑似度不构成授信结论；机器不下最终定性。
- 缺数据如实标注降级（无余额列/无对手账户/无结息/未配置白名单/多币种未折算）。
- 详见 [SKILL.md](SKILL.md)、[references/design-v4.md](references/design-v4.md)
  与 [references/methodology.md](references/methodology.md)。

## 安装到 skills 目录

把整个 `bank-statement-bi-analysis/` 文件夹拷入客户端 skills 目录（如 Claude Code `~/.claude/skills/`），
或分发单文件 `dist/bank-statement-bi-analysis.skill`（zip 格式，解压到 skills 目录或在支持导入的客户端内导入）。
打包命令：`python scripts/package_skill.py`（同时产出 `dist/<name>/` 干净文件夹与 `dist/<name>.skill`）。

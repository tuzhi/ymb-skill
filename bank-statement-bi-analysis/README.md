# bank-statement-bi-analysis

经营流水 BI 分析报告生成器 —— `bank-statement-standardization` 的下游技能。

输入已标准化/整合/打标的经营流水，输出每个经营主体一份图表化 `.xlsx` 授信分析报告：
余额波动、收支结构、交易对手、上下游、流入流出趋势 + 审批要件指标看板（带参考阈值，红黄绿判断）。
报告结构参考《发票分析报告》模板的「经营观察 + 授信决策」两模块布局，但不做评分卡/测额。

## 用法

```bash
pip install -r requirements.txt
python scripts/build_bi_report.py --input "<标准化产物文件或文件夹>" --client "客户名" [--out-dir DIR]
```

输入可为标准化交付物 `.xlsx`、`*__打标流水.csv`、`*__整合流水.csv`，或包含它们的文件夹。

## 输出（单个 .xlsx，5 个工作表）

| 工作表 | 内容 |
| --- | --- |
| 分析报告 | 抬头 + 模块一(概况/收支结构) + 模块二(审批要件指标看板) |
| 月度时序表 | 月度流入/流出/净流入/笔数/月末·月均·月最低余额 |
| 收支结构 | 收入、支出按二级用途金额表 |
| 交易对手 | 十大流入/流出对手、净上下游、双向往来对手 |
| 可视化看板 | 6 张 Excel 原生图表（收支组合图/余额折线/收支饼图/对手条形图） |

## 设计红线

- 沿用标准化产物的余额连续性行序，**绝不按交易时间重排**。
- 只读不改；阈值仅供初筛，不构成授信结论。
- 详见 [SKILL.md](SKILL.md) 与 [references/methodology.md](references/methodology.md)。

`assets/sample_standardized.csv` 为合成测试样本，可直接跑通验证。

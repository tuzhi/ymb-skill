# 标准化回归集合

本目录维护银行流水标准化的轻量回归配置。默认集合 `p0_smoke`
用于日常 parser、fingerprint、字段映射和金额方向迭代后的快速护栏。

## 运行

对比已认可的 baseline：

```bash
venv/bin/python bank-statement-standardization/scripts/run_regression.py --suite p0_smoke
```

首次建立或人工确认后更新 baseline：

```bash
venv/bin/python bank-statement-standardization/scripts/run_regression.py --suite p0_smoke --update-baseline
```

## 文件分工

- `regression_cases.yaml`：回归样本清单和选择原因。
- `../testdata/regression/baselines/`：已认可的指标 baseline，属于 `testdata` 本地 git。
- `../testdata/regression/runs/`：每次运行报告，属于 `testdata` 本地 git。

默认运行不会覆盖 baseline，也不会跑全量 `support_matrix.xlsx`。

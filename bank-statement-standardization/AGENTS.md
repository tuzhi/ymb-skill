# 项目协作规范

本仓库是银行流水标准化项目。核心目标是把 Excel/CSV/可抽取文本 PDF 流水转换为统一中文字段，并支持整合、余额校验、打标、交付物生成和 testdata 支持矩阵回归。

## 设计入口

银行流水标准化相关任务的设计、模块分层、代码入口和验收入口，以 Obsidian 中的模块索引为导航事实源：

- `D:\SynologyDrive\DESKTOP-1H746BQ\D\SynologyDrive\Obsidian_document\流水分析工程\流水标准化模块索引.md`

执行要求：

1. 先读取上述模块索引。
2. 按索引定位对应设计文档、代码模块和验收入口。
3. 再用 Codegraph 查看调用链和影响面。
4. 实现后运行索引中对应的最小验收。
5. 增删模块，必须要更新`流水标准化模块索引.md`

## AI交互原则

- 模块术语以`流水标准化模块索引.md`定义的为准
- 就近原则：新增规则、约束或说明应放在它实际生效的执行阶段、模块或失败分支附近；只影响阶段一的内容，不放到全局流程里展开。
- 文档最小化原则：每篇文档尽可能精简，只写当前文档职责内必须承载的规则、边界和入口；不要把可由模块索引、阶段 prompt、代码或测试表达的内容重复写入。
- 新增专题设计文档不以数量多少判断，而以内容是否独立、会反复复用、容易放错层为准；新增后也必须保持单篇文档精简。
- 运行时 `manifest.json` / `run_manifest.json` / `receipts/` 的输出契约，归属到 `bank-statement-standardization/SKILL.md` 的状态机与写入边界附近维护；`AGENTS.md` 只保留归属规则，不重复字段清单。


## 项目规范

- Python 项目元数据以 `pyproject.toml` 为事实源。
- 共享标准化内核位于 `ymb-standardization-core/`。
- Skill 封装位于 `bank-statement-standardization/`。
- PDF/Excel 输入识别优先放在 shared core 的 parser/router/input 层。
- `testdata/` 为测试数据目录。
- `scripts/package_skill.py` 为打包脚本。
- 代码注释优先中文说明。

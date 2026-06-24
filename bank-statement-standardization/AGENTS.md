# 项目协作规范
本仓库是银行流水标准化项目。核心目标是把 Excel/CSV/可抽取文本 PDF 流水转换为统一中文字段，并支持整合、余额校验、打标、交付物生成和 testdata 支持矩阵回归。

## 设计

本项目的银行流水标准化设计不在本文件内重复维护。涉及标准化 Skill 流程、状态机、AI 兜底、Router 层次、parser 迭代沉淀等设计判断时，必须直接读取并遵循 Obsidian vault 中的设计文档：

- `D:\SynologyDrive\DESKTOP-1H746BQ\D\SynologyDrive\Obsidian_document\流水分析工程\流水标准化skill流程设计.md`
- `D:\SynologyDrive\DESKTOP-1H746BQ\D\SynologyDrive\Obsidian_document\流水分析工程\银行流水标准化 Router 层次设计.md`
- `D:\SynologyDrive\DESKTOP-1H746BQ\D\SynologyDrive\Obsidian_document\流水分析工程\流水标准化skill迭代器.md`

## 架构
- Python 项目元数据以 pyproject.toml 为事实源。
- 共享标准化内核位于 ymb-standardization-core/。
- skill 封装位于 bank-statement-standardization/。
- PDF/Excel 输入识别优先放在 shared core 的 parser/router/input 层。
- testdata为测试数据目录。
- scripts/package_skill.py为打包脚本。
- 代码注释优先中文说明。

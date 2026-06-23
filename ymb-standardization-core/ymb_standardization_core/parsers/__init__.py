"""银行流水输入解析器包。

parser 只负责把原始文件结构还原为二维 rows 和路由审计信息；
标准字段归一、金额方向、交易唯一编号、mapping 落盘仍由 ymb_standardization_core.core 统一处理。
"""

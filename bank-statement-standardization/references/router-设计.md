# Router 设计

## 职责边界

输入读取分三层：

1. `fingerprint` 负责识别文件模板。它只使用稳定结构证据，例如标题、列名、元数据、样式和日期格式，不使用交易对手、金额、账号、摘要等交易内容。
2. `reader_id` 负责选择读取方式。它不是银行名，也不是字段映射名。
3. `columns.all` 负责把读取到的原始列映射到标准字段。key 是原始列名，value 是标准字段名；只用于识别、不进入标准字段的结构列写 `null`。

reader 不硬编码银行字段名，也不猜测业务语义。模板差异通过 YAML 表达，代码只执行通用读取和配置化清洗。

## Reader ID

当前 YAML 中使用的 `reader_id` 分两类。

通用 PDF reader：

- `pdfplumber_table`：直接使用 `pdfplumber.extract_tables()` 读取有表格结构的 PDF。
- `pdfplumber_line_table`：`extract_tables()` 读不到表时，使用 PDF 真实横线推断行、显式竖向边界推断列。
- `pdfplumber_word_column_table`：按文字坐标列读取，没有完整表格线但列位置稳定，常用于 word/坐标列式 PDF。
- `pdfplumber_text_separator_table`：读取文本层里用分隔符或文本行表达的表格。

专用或文本层 reader 标识：

- `pdf_fixed_width`：固定宽度/专用文本 PDF。路由命中后由 fingerprint 对应的专用 reader 接管，例如农行文本 PDF、江西农商、开泰银行、浙江庆元农商、江西裕民银行等。
- `pdfplumber_text_lines`：文本行解析型 PDF。它不是普通 `extract_tables()` reader；路由命中后由文本行 fallback 或专用文本解析逻辑处理，例如招商交易流水、九江银行文本流水、民生个人对账单等。

Excel 当前使用：

- `openpyxl_grid`：读取 Excel 网格数据，再由 `columns.all` 完成列到标准字段映射。


## Column Transforms

`fingerprint.column_transforms` 用于描述已识别模板中某些列的单元格文本清洗策略，尤其是 PDF 单元格内换行。它挂在 fingerprint 下，是模板结构的一部分；它不能包含交易内容，也不能用于识别银行身份。

配置示例：

```yaml
fingerprint:
  columns:
    all:
      交易日期: 交易时间
      收(付)方名称: 对手名称
      收(付)方账号: 对手账户
      交易类型: 交易渠道
  column_transforms:
    交易日期:
      newline: space
    收(付)方名称:
      newline: cjk_join
    收(付)方账号:
      newline: remove_all
    交易类型:
      newline: cjk_join
```

支持的 `newline` 策略：

- `space`：把换行和连续空白压缩成一个空格。适合日期时间，例如 `2025-05-06\n14:12:41` -> `2025-05-06 14:12:41`。
- `cjk_join`：中文字符之间的断行直接拼接，其他断点保留一个空格。适合公司名、交易类型、中文摘要，例如 `上海寻梦信息技\n术有限公司` -> `上海寻梦信息技术有限公司`。
- `remove_all`：删除所有空白。适合账号、订单号等不应含空格的字段，例如 `97915485007001\n9810` -> `979154850070019810`。

未配置 `column_transforms` 的列默认使用 `space`，保持历史行为。

## 数据例子

`testdata/斑马商业对公流水/斑马商业招行一般户（青山湖支行）-1221流水.1.pdf` 的 `pdfplumber_line_table` 原始单元格样式：

```text
交易日期: '2025-05-06\n14:12:41'
收(付)方名称: '上海寻梦信息技\n术有限公司'
收(付)方账号: '62152099913769'
交易类型: '对公转账正\n常提出'
```

配置化清洗后：

```text
交易日期: '2025-05-06 14:12:41'
收(付)方名称: '上海寻梦信息技术有限公司'
收(付)方账号: '62152099913769'
交易类型: '对公转账正常提出'
```

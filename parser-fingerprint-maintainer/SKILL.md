---
name: parser-fingerprint-maintainer
description: "当维护银行流水 reader fingerprint、检查 Excel/PDF 是否命中 ymb-standardization-core YAML 规则、处理 unmatched 或 ambiguous 路由、对比 support_matrix.xlsx 样本、或新增保守指纹且不能过度声明银行身份时使用。"
---

# Parser Fingerprint Maintainer

用于维护 `bank-statement-standardization` 的流水格式指纹。目标是让路由判断稳定、唯一、可解释，同时避免把文件名、交易内容或猜测出来的银行身份写进规则。

## 适用场景

当用户要求你处理这些问题时使用本技能：

- 判断某个 Excel/PDF 是否命中已有 YAML fingerprint
- 分析 `generic_*`、`unmatched`、`ambiguous_router_match`
- 对比 `support_matrix.xlsx` 中同一 fingerprint 的历史样本
- 新增或收窄统一的 `routing_rules.yaml`（用 `file_type` 区分 Excel/PDF）
- 判断某个 fingerprint 是否过宽、过窄，或错误声明了银行身份
- 维护 `reader_id`、列到标准字段的映射、支持矩阵中的 reader 命中结果

## 核心规则

fingerprint 路由必须保守、确定、唯一：

1. 调查阶段，一个文件可能命中 0 个、1 个或多个 fingerprint。
2. 生产路由只有在“刚好命中 1 个 fingerprint”时才算可靠。
3. 如果命中多个 fingerprint，不要新增规则；应收窄已有重叠规则，直到唯一命中。
4. 如果没有命中，先检查目标文件结构和 `support_matrix.xlsx` 同类样本，再决定是否新增规则。
5. 不要仅凭文件名、账号前缀、客户目录名、常见网银词，写入具体银行名。
6. fingerprint 只能使用模板结构证据，不能使用交易数据内容。

## 禁止进入 Fingerprint 的内容

不要把这些内容写进 `fingerprint.identity`、`fingerprint.columns`、`metadata`、`style` 或其他识别条件：

- 交易对手名称、个人姓名、交易对手账号、本方具体账号
- 交易摘要、用途、备注、附言，例如“货款”“借款”“往来款”“工资”“超级网银往贷”“行内转账”
- 来自交易行的对手银行、支行名称
- 具体金额、余额、流水号、日期范围
- 只在单个客户样本中出现的业务文本

可以使用的证据：

- 稳定标题、文件证明标题、固定导出系统文字
- 稳定列名、表头字段、列顺序和列到标准字段的映射
- 文件元数据，例如 `creator`、`application`、`Producer`
- 稳定样式证据，例如字体、粗体、固定行列位置
- 日期格式模式，例如 `yyyy-mm-dd hh:mm:ss`，但不是某个具体日期

## 工作流程

### 1. 检查目标文件

```bash
python parser-fingerprint-maintainer/scripts/inspect_fingerprint.py \
  --repo-root /path/to/ymb-skill \
  --file bank-statement-standardization/testdata/<客户>/<文件>
```

重点读取 JSON 中这些字段：

- `route_info.decision`
- `route_info.reader_id`
- `route_info.fingerprint_id`
- `route_info.candidates`
- `route_info.candidate_fingerprints`
- `target.signature`
- `support_matrix.peers`
- `recommendation`

### 2. 判断结果

`matched_unique` / `decision=matched`：

- 和 `support_matrix.xlsx` 中同一 fingerprint 的样本对比。
- 如果结构一致，可以维护到这个 fingerprint。
- 如果样本差异明显，说明 fingerprint 可能过宽，需要收窄。

`unmatched`：

- 如果同类未识别样本很多，且结构稳定，可以新增 fingerprint。
- 如果只是单个异常文件，只给出候选分析，不要过度扩展规则。
- 没有可靠银行证据时，`bank` 保持 `未识别`。

`ambiguous`：

- 不要新增 fingerprint。
- 找出重叠证据，用更严格的 `columns`、`metadata`、`style`、`date_format` 收窄已有规则。

### 3. 对比支持矩阵样本

```bash
python parser-fingerprint-maintainer/scripts/compare_support_matrix.py \
  --repo-root /path/to/ymb-skill \
  --file bank-statement-standardization/testdata/<客户>/<文件> \
  --fingerprint-id <fingerprint_id>
```

当一个文件已经唯一命中 fingerprint，但需要确认它和历史样本是否同类时使用。

## YAML 结构

所有识别证据都放在 `fingerprint` 内。`reader_id` 表示读取方式，fingerprint 表示格式识别。

```yaml
- id: md5:<fingerprint_md5>
  file_type: excel
  bank: 未识别
  account_type: 对公
  reader_id: openpyxl_grid
  fingerprint:
    identity:
      any:
      - '[HISTORYDETAIL]'
    columns:
      all:
        '[HISTORYDETAIL]': null
        交易时间: 交易时间
        账户余额: 账户余额
    metadata:
      all:
        creator: Apache POI
    style:
      all:
      - text: '[HISTORYDETAIL]'
        font: Calibri
        row_max: 1
    date_format:
      any:
      - yyyy-mm-dd hh:mm:ss
```

约定：

- `id` 是 `fingerprint` 节点规范化后的 md5。
- `columns.all` 的 key 是读取到的列名，value 是标准字段名；只用于识别、不映射的结构列可以写 `null`。
- 不再使用顶层 `parser`、`parser_id`、`identity`、`layout`、`column_mapping`。
- 不再使用 `fingerprint.data.same_row_all` 表达列匹配；列匹配统一放进 `columns.all`。

## 判断准则

- 只为稳定、可复用的格式新增 fingerprint，不为一次性文件新增。
- 能收窄已有规则时，优先收窄，不要复制出相似规则。
- 银行身份和格式身份分开：格式可以识别，银行仍可保持 `未识别`。
- `account_type` 只有在结构证据支持时才写。
- 更新 `support_matrix.xlsx` 应基于单文件或小批量验证成功；除非用户明确要求，不要全量重跑。

## 脚本

- `scripts/inspect_fingerprint.py`：检查单个文件的路由、签名、support_matrix peers，并输出建议。
- `scripts/compare_support_matrix.py`：把单个文件和 support_matrix 中同一 fingerprint 的样本做结构对比。

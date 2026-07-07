import re


def read_abc_text_pdf(pdf):
    """解析中国农业银行账户活期交易明细清单文本版 PDF。

    这类 PDF 没有表格边框，pdfplumber.extract_tables() 会返回空列表，
    但 extract_text() 可以稳定取得每笔交易。续行并入交易附言，避免漏笔。
    """
    header = ["交易日期", "交易时间", "交易摘要", "交易金额", "本次余额",
              "对手信息", "日志号", "交易渠道", "交易附言"]
    txn_re = re.compile(
        r"^(\d{8})\s+(?:(\d{6})\s+)?(\S+)\s+([+-]\d[\d,]*\.\d{2})\s+"
        r"([\d,]+\.\d{2})\s+(.*)$")
    journal_re = re.compile(r"^(.*?)\s+([A-Za-z]\d{9}|\d{10})\s*(.*)$")
    channels = ("掌上银行", "电子商务", "中国人寿代扣", "个人活期结息",
                "超级网银", "短信费", "自动柜员机")
    rows = [header]
    preamble = []

    for page in pdf.pages:
        for raw_line in (page.extract_text() or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("中国农业银行账户活期交易明细清单", "币种：", "起止日期：",
                                "交易日期 ", "该交易明细因", "第")):
                if line.startswith(("中国农业银行", "币种：", "起止日期：")):
                    preamble.append(line)
                continue
            if line.startswith("户名："):
                preamble.append(line)
                continue

            m = txn_re.match(line)
            if m:
                date, time, summary, amount, balance, tail = m.groups()
                # 农行文本尾部通常是「对手信息 日志号 渠道/附言」，先切日志号再识别渠道。
                jm = journal_re.match(tail)
                if jm:
                    opponent, journal, remainder = jm.groups()
                else:
                    opponent, journal, remainder = tail, "", ""
                channel = ""
                memo = remainder
                for candidate in channels:
                    if remainder.startswith(candidate):
                        channel = candidate
                        memo = remainder[len(candidate):].strip()
                        break
                rows.append([date, time or "", summary, amount, balance,
                             opponent.strip(), journal, channel, memo])
                continue

            # PDF 列宽导致的对手名称/附言换行，保留在上一笔附言中供追溯。
            if len(rows) > 1:
                rows[-1][-1] = (rows[-1][-1] + " " + line).strip()

    return " ".join(preamble), rows if len(rows) > 1 else []

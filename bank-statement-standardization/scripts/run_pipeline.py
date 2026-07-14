#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_pipeline.py — 单客户端到端编排：标准化 -> 整合 -> 组合余额 -> 打标（阶段一~三）

把一个客户文件夹里的所有原始流水文件跑完整条标准化流水线，产物统一放到
该文件夹下的 _标准化产物/ 目录里。多客户批量请在此之后用 multi_customer.py。

用法：
  python run_pipeline.py <客户名> <客户原始文件夹> [--bank 银行] [--account-type 对公|个人]

会处理文件夹内所有 .xlsx/.xls/.csv/.pdf。
"""
import argparse, glob, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import standardize as S
import integrate as I
import tag as T
import portfolio_balance as PB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("client")
    ap.add_argument("folder")
    ap.add_argument("--bank")
    ap.add_argument("--account-type", choices=["对公", "个人", "未知"])
    ap.add_argument("--out-dir")
    args = ap.parse_args()

    out_dir = args.out_dir or os.path.join(args.folder, "_标准化产物")
    os.makedirs(out_dir, exist_ok=True)

    raw_files, skipped = S.screen_files(sorted(glob.glob(os.path.join(args.folder, "*"))))
    if not raw_files:
        msg = f"未在 {args.folder} 找到可处理的银行流水文件（支持 Excel/CSV/文本/非图片PDF）"
        if skipped:
            msg += "；已跳过：" + "，".join(f"{n}（{w}）" for n, w in skipped)
        sys.exit(msg)

    print(f"=== 客户：{args.client}  候选流水文件 {len(raw_files)} 个 ===")
    print("\n[阶段一] 单文件标准化与字段映射")
    for f in raw_files:
        try:
            csv_path, json_path, rep = S.standardize(
                f, out_dir=out_dir, bank=args.bank, account_type=args.account_type)
            st = rep["标准化统计"]
            nrev = len(rep["人工复核事项"])
            print(f"  [OK] {os.path.basename(f)} -> {st['交易笔数']} 笔"
                  f"（{st['金额结构']}）" + (f"，复核{nrev}项" if nrev else ""))
        except S.NotABankStatement as e:
            skipped.append((os.path.basename(f), e.reason))
            print(f"  [SKIP] {os.path.basename(f)}：{e.reason}")
        except Exception as e:
            skipped.append((os.path.basename(f), f"解析失败：{e}"))
            print(f"  ! {os.path.basename(f)} 失败：{e}")

    if skipped:
        print(f"\n[已跳过 {len(skipped)} 个非流水/无法解析文件]")
        for n, w in skipped:
            print(f"  [SKIP] {n}：{w}")

    print("\n[阶段二] 单客户多文件整合与验证")
    int_csv, int_json, irep = I.integrate(args.client, [out_dir], out_dir=out_dir)
    o = irep["客户整合概览"]
    print(f"  账户 {o['整合账户数']} / 交易 {o['整合交易数']} / 质量评分 {o['整体质量评分']} / "
          f"期间 {o['交易期间']['开始日期']}~{o['交易期间']['结束日期']}")
    print(f"  疑似重复 {len(irep['疑似重复交易组'])} 组 / 互转候选 {len(irep['自有账户互转组'])} 组 / "
          f"余额预警账户 {sum(1 for b in irep['余额校验'] if b['校验状态']=='预警')}")

    print("\n[阶段二·补] 组合（虚拟账户）余额与余额校验")
    try:
        pb_csv, pb_json, pbrep = PB.run(int_csv, out_dir=out_dir)
        v = pbrep["组合虚拟账户"]
        bc = pbrep["账户余额校验"]
        print(f"  组合期初 {v['期初合计余额']} -> 期末 {v['期末合计余额']}；峰值 {v['峰值合计余额']} 谷值 {v['谷值合计余额']}")
        print(f"  账户余额校验 {bc['通过账户数']}/{bc['账户总数']} 通过；组合连续性：{pbrep['组合连续性校验']['结论']}")
    except Exception as e:
        print(f"  ! 组合余额计算失败：{e}")

    print("\n[阶段三] 交易标签梳理与规则沉淀")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rules = os.path.join(here, "assets", "tag_rules.csv")
    tag_csv, tag_json, srep = T.tag(int_csv, rules, out_dir=out_dir)
    s = srep["标签梳理概览"]
    print(f"  规则命中 {s['规则命中数量']}/{s['交易总数']}（{s['规则命中率']:.0%}），"
          f"兜底其他类 {s.get('兜底其他类数量', s.get('待人工复核数量',0))}")

    print(f"\n=== 完成，产物目录：{out_dir} ===")
    print(f"  整合流水 -> {os.path.basename(int_csv)}")
    print(f"  组合日余额 -> {os.path.basename(int_csv).replace('.csv','')}__组合日余额.csv")
    print(f"  打标流水 -> {os.path.basename(tag_csv)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给中文文本打 AIGC 检出分数（本地检测器，不联网）。

用途：在改写前后各跑一次，看分数实际降了多少；也可单独用来定位最可疑的段落。

     python3 scripts/score_text.py 论文.txt
     python3 scripts/score_text.py 论文.docx --agg mean
     python3 scripts/score_text.py 论文.txt --json > 分数.json

说明
----
- 分数含义：该段落被判为 AI 生成的概率（0~1），由本地开源检测器给出。
- **这不是知网/PaperPass/超星学习通的结果**。那些是私有黑盒，本机无法调用；
  这里给出的是开源检测器上的代理指标，两者没有校准关系。
- 默认 `--agg max`（取最可疑段落），偏保守。

三种聚合口径，**不要混用**
--------------------------
| 口径 | 算法 | 用途 |
|---|---|---|
| `max` | 最可疑段落的分数 | 保守自查：先看最糟的一段 |
| `mean` | 段落等权平均 | 诊断：看整体分布 |
| `char_weighted` | **按字数加权**，并给出"被标疑似段落的字数占比" | **与平台口径对齐的那个** |

`char_weighted` 为什么存在：知网一手字段是
`疑似AIGC占全文比 = 疑似AI生成文字字数 / 文本总字数`（见
`references/platforms.md` §1.5），**是按字数加权的，不是段落等权**。
段落等权会让一个 20 字的过渡段与一个 200 字的论证段权重相同 ——
而平台的分子分母都按字数算。

⚠️ 两点边界：
1. 片段划分规则平台不公开（知网自述"片段为检测**自动划分**，与原文自然段不同"），
   本脚本只能用**自然段**近似，**不等价**。
2. 判定线 0.5 是知网对**片段**的线（"AIGC 值>=0.5，即为疑似 AI 生成"），
   不是对文档的线。文档级达标线由**学校**定（观测 30% / 40%）。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench  # noqa: E402
from aggregate import aggregate, flagged_char_ratio  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="AIGC 分数打分（本地检测器）")
    ap.add_argument("path", help="文本文件（.txt/.md/.docx/.doc）")
    ap.add_argument("--detectors", nargs="+",
                    default=["d_hello", "d_cmj"],
                    help="检测器简称：d_hello / d_cmj / d_zhv3")
    ap.add_argument("--agg", choices=["max", "mean", "char_weighted"], default="char_weighted",
                    help="max=最可疑段；mean=段落等权；char_weighted=按字数加权（与平台口径对齐，默认）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="判定线，仅用于标注哪些段落可疑")
    args = ap.parse_args()

    bench = _bench.find_bench()
    _bench.ensure_venv(bench)      # 自动切到 bench 的 venv（torch 在那里）
    _bench.ensure_importable(bench)
    from corpus import n_chars, split_paragraphs
    from detectors import Detector

    text = _bench.read_text(args.path)
    paras = split_paragraphs(text) or [text]
    paths = _bench.detector_paths(bench, args.detectors)

    out = {"file": os.path.basename(args.path), "chars": n_chars(text),
           "n_para": len(paras), "agg": args.agg, "detectors": {}}

    if not args.json:
        print(f"文本 {n_chars(text)} 字，{len(paras)} 段")
        print(f"（本地开源检测器代理指标，非商业平台结果；聚合={args.agg}）\n")

    for p in paths:
        name = os.path.basename(p)
        det = Detector(name, p)
        ps = det.score_paragraphs(paras)
        wts = [max(n_chars(t), 1) for t in paras]
        agg = aggregate(ps, wts, args.agg)
        flagged_chars, total_chars, flagged_ratio = flagged_char_ratio(
            ps, wts, args.threshold)
        out["detectors"][name] = {
            "paragraph_scores": [round(float(x), 4) for x in ps],
            "paragraph_chars": wts,
            "aggregate": round(float(agg), 4),
            "n_flagged": sum(1 for x in ps if x >= args.threshold),
            # 与平台 `aigcRatio` 同构的字段（片段划分仍不等价，见 docstring）
            "flagged_chars": flagged_chars,
            "flagged_char_ratio": round(flagged_ratio, 4),
        }
        if not args.json:
            print(f"{name}")
            for i, (x, t) in enumerate(zip(ps, paras), 1):
                mark = "  <-- 可疑" if x >= args.threshold else ""
                print(f"  段{i:3d} ({n_chars(t):4d}字) {x:.3f}{mark}")
            d = out["detectors"][name]
            print(f"  聚合({args.agg}) {agg:.3f}   "
                  f"可疑段落 {d['n_flagged']}/{len(ps)}   "
                  f"**可疑字数占比 {d['flagged_char_ratio']:.1%}**"
                  f"（{flagged_chars}/{total_chars} 字）\n")

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("提示：优先用 scripts/anchor.py 补具体信息（实测唯一有效的编辑）；"
              "reduce_aigc.py 的 LLM 改写实测**不降分**，见 references/edit-effects.md。")
        print("提醒：可疑字数占比是本机**自然段近似**下的代理指标，"
              "与平台的片段划分不等价，不构成对任何平台结果的预测。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

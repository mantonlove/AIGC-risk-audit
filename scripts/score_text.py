#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给中文文本打本地检测器 AI 类得分（不联网）。

用途：在改写前后各跑一次，看分数实际降了多少；也可单独用来定位最可疑的段落。

     python3 scripts/score_text.py 论文.txt
     python3 scripts/score_text.py 论文.docx --agg mean
     python3 scripts/score_text.py 论文.txt --json > 分数.json

说明
----
- 分数含义：本地开源检测器 softmax 的 AI 类得分（0~1），未经概率校准。
- 本地按非空行分段、长行按 token 窗口覆盖；不是知网/PaperPass/学习通
  的检测字段，也不能预测那些平台的百分比。
- 默认 `--agg char_weighted`，便于看长短行的本地加权摘要。

三种聚合口径，**不要混用**
--------------------------
| 口径 | 算法 | 用途 |
|---|---|---|
| `max` | 最可疑段落的分数 | 保守自查：先看最糟的一段 |
| `mean` | 段落等权平均 | 诊断：看整体分布 |
| `char_weighted` | **按汉字数加权**，并给出"本地标记行的汉字数占比" | 本地近似诊断 |

`char_weighted` 为什么存在：长短段落等权会产生明显偏差，所以另给出
按汉字数加权的本地摘要。平台的片段划分和计数字段均不能由此还原。
段落等权会让一个 20 字的过渡段与一个 200 字的论证段权重相同。

⚠️ 两点边界：
1. 新版分数使用所有非空行和覆盖全文的 token 窗口；旧版过滤短行、截断长行，
   两版数值不可直接比较。
2. `--threshold` 只是本地标记线，不是任何学校或商业平台的达标线。
3. 纯 ASCII/公式文本的汉字权重全为 0 时，`char_weighted` 退回段落等权；
   标记汉字占比记为 0/0、显示 0.0。此时该占比没有可解释的汉字分母。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench  # noqa: E402
from aggregate import aggregate, flagged_char_ratio  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="本地检测器 AI 类得分（未经平台校准）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("path", help="文本文件（.txt/.md/.docx/.doc）")
    ap.add_argument("--detectors", nargs="+",
                    default=["d_hello", "d_cmj"],
                    help="检测器简称：d_hello / d_cmj / d_zhv3")
    ap.add_argument("--agg", choices=["max", "mean", "char_weighted"], default="char_weighted",
                    help="max=最高行；mean=非空行等权；char_weighted=按汉字数加权（本地近似，默认）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="判定线，仅用于标注哪些段落可疑")
    args = ap.parse_args()

    bench = _bench.find_bench()
    _bench.ensure_venv(bench)      # 自动切到 bench 的 venv（torch 在那里）
    _bench.ensure_importable(bench)
    from corpus import line_spans, n_chars
    from detectors import Detector, SCORING_VERSION

    text = _bench.read_text(args.path)
    paras = [raw.strip() for _, _, raw in line_spans(text)] or [text]
    paths = _bench.detector_paths(bench, args.detectors)
    if len(paths) != len(args.detectors):
        raise SystemExit("请求的检测器路径不齐，已停止评分；请补齐模型后重试。")

    out = {"file": os.path.basename(args.path), "chars": n_chars(text),
           "scoring_version": f"{SCORING_VERSION}/all-nonblank-lines",
           "score_meaning": "未经校准的本地检测器 AI 类 softmax 得分",
           "n_para": len(paras), "agg": args.agg, "detectors": {}}

    if not args.json:
        print(f"文本 {n_chars(text)} 字，{len(paras)} 段")
        print(f"（本地模型 AI 类得分；非商业平台结果；聚合={args.agg}）\n")

    for p in paths:
        name = os.path.basename(p)
        det = Detector(name, p)
        ps = det.score_paragraphs(paras)
        wts = [n_chars(t) for t in paras]
        agg = aggregate(ps, wts, args.agg)
        flagged_chars, total_chars, flagged_ratio = flagged_char_ratio(
            ps, wts, args.threshold)
        out["detectors"][name] = {
            "paragraph_scores": [round(float(x), 4) for x in ps],
            "paragraph_chars": wts,
            "aggregate": round(float(agg), 4),
            "n_flagged": sum(1 for x in ps if x >= args.threshold),
            # 仅本地按汉字数计算，不能等同任何平台字段。
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
                  f"本地标记汉字占比 {d['flagged_char_ratio']:.1%}"
                  f"（{flagged_chars}/{total_chars} 字）\n")

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("提示：可用 scripts/anchor.py 定位需要作者补充真实依据的位置；"
              "LLM 改写在既有本地实验中没有稳定降低检出率，见 references/edit-effects.md。")
        print("提醒：本地按非空行和 token 窗口近似；标记占比不能等同知网字段，"
              "也不能预测知网、PaperPass 或学习通的百分比。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

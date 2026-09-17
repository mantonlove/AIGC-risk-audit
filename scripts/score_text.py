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
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="AIGC 分数打分（本地检测器）")
    ap.add_argument("path", help="文本文件（.txt/.md/.docx/.doc）")
    ap.add_argument("--detectors", nargs="+",
                    default=["d_hello", "d_cmj"],
                    help="检测器简称：d_hello / d_cmj / d_zhv3")
    ap.add_argument("--agg", choices=["max", "mean"], default="max")
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
        agg = max(ps) if args.agg == "max" else sum(ps) / len(ps)
        out["detectors"][name] = {
            "paragraph_scores": [round(float(x), 4) for x in ps],
            "aggregate": round(float(agg), 4),
            "n_flagged": sum(1 for x in ps if x >= args.threshold),
        }
        if not args.json:
            print(f"{name}")
            for i, (x, t) in enumerate(zip(ps, paras), 1):
                mark = "  <-- 可疑" if x >= args.threshold else ""
                print(f"  段{i:3d} ({n_chars(t):4d}字) {x:.3f}{mark}")
            print(f"  聚合 {agg:.3f}   超过 {args.threshold} 的段落数 "
                  f"{out['detectors'][name]['n_flagged']}/{len(ps)}\n")

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("提示：要降分请用 scripts/reduce_aigc.py；"
              "要看写作质量用 scripts/diagnose.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

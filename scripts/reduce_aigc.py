#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心：把文本的 AIGC 检出分数压下来（检测器引导的迭代改写）。

怎么用
------
    # 1) 先看初始分数，找出最可疑的段落
    python3 scripts/score_text.py 论文.txt

    # 2) 改写降分（默认目标 0.2，迭代 3 轮）
    python3 scripts/reduce_aigc.py 论文.txt --out 论文_改写.txt

    # 3) 更激进：目标 0.1、5 轮
    python3 scripts/reduce_aigc.py 论文.txt --target 0.1 --max-iters 5 --out out.txt

    # 只看某些段落：先切好文件，或直接粘贴单段文本到 .txt

**先读这条**：语料实测显示 LLM 改写**不降低检出率**（n=240/组，三检测器）。
真正有效的是"把你自己知道的真实信息补进去"——那是 `scripts/anchor.py`。
所以正常顺序是：**先 anchor.py 补事实，再考虑本引擎**。详见
`references/edit-effects.md`。

工作原理（检测器引导，不靠经验删词）
------------------------------------
1. 逐段用**本地检测器**打分，找出最可疑的段落；
2. 对可疑段落并行生成 4 种风格不同的候选改写（改措辞/句式/句序，不改事实）；
3. 每个候选**重新打分**，只保留分数更低者；
4. 迭代，直到低于目标分数或达到迭代上限；
5. 输出逐段分数变化，每一步都可核对。

为什么必须以检测器分数为准
--------------------------
文献实测同一编辑动作对不同检测器的影响方向可能相反
（Style as a Confound, arXiv:2608.26710：13 个检测器 FPR 从 0.0% 到 100.0%）。
本项目自己也在 120 对同题文本上复现了这一点（见 references/edit-effects.md）：
"加自我设限"在 d_hello 上 Δ=−0.263，在 d_cmj 上 Δ=+0.069。

⚠️ **默认的两个检测器不是独立证据。** 实测 corr(d_cmj, d_zhv3) = +0.928（近冗余），
而它们与 d_hello 只有 +0.42 / +0.34。所以 --detectors 取多个时，
只能在报告里说"在 N 个**中度相关**的检测器上"，不要说"多个独立检测器都同意"。

⚠️ **本引擎用 LLM 生成候选改写，而实测显示 LLM 改写不降低检出率**
（语料里 M10/M01/M11 三种 LLM 改写相对基底持平或略升，n=240/组）。
真正有效的是"把已有的具体信息搬进来"——那是 scripts/anchor.py 的职责，
不是本引擎能做的（本引擎不增删事实）。**先用 anchor.py 补具体信息，再来跑本引擎。**

边界（必须知道）
----------------
- 这里测的是**开源检测器**，**不是**知网/PaperPass/超星学习通。那些平台是私有黑盒，
  本机调不到，两者也没有校准关系。分数下降只说明在这些开源检测器上下降了。
- 引擎只做**表层重写**：不增删事实、不编造数据与文献。这一点是硬约束——
  编造内容不是降分技术，而是学术不端，且新增的具体信息往往会被判为更可疑。
- 改写会改变表达。**提交前请自己通读**，确认每一句你都认同、都能负责。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="降 AIGC 检出分数（检测器引导的迭代改写）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("path", help="输入文本（.txt/.md/.docx/.doc）")
    ap.add_argument("--out", default=None,
                    help="改写结果输出路径（默认 <原名>_降低AIGC.txt）")
    ap.add_argument("--target", type=float, default=0.2,
                    help="目标分数上限，达到即停（默认 0.2）")
    ap.add_argument("--max-iters", type=int, default=3, help="每段最多迭代轮数")
    ap.add_argument("--detectors", nargs="+", default=["d_hello", "d_cmj"],
                    help="用于打分与择优的检测器：d_hello / d_cmj / d_zhv3")
    ap.add_argument("--agg", choices=["max", "mean"], default="max",
                    help="段落聚合：max=最可疑段（保守，默认）")
    ap.add_argument("--only-flagged", action="store_true",
                    help="只改超过 --flag-threshold 的段落，其余原样保留（省算力）")
    ap.add_argument("--flag-threshold", type=float, default=0.5)
    ap.add_argument("--report", default=None, help="逐段分数报告（JSON）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    bench = _bench.find_bench()
    _bench.ensure_venv(bench)      # 自动切到 bench 的 venv（torch 在那里）
    _bench.ensure_importable(bench)
    from corpus import n_chars, split_paragraphs
    from rewriter import Rewriter, ScoreBoard

    text = _bench.read_text(args.path)
    paras = split_paragraphs(text) or [text]
    det_paths = _bench.detector_paths(bench, args.detectors)
    model = _bench.generator_path(bench)

    print(f"输入：{os.path.basename(args.path)}  {n_chars(text)} 字  {len(paras)} 段")
    print(f"检测器：{[os.path.basename(p)[:30] for p in det_paths]}")
    print("（本地开源检测器代理指标，非商业平台结果）\n")

    sb = ScoreBoard("", det_paths)

    def agg_of(scores):
        vals = list(scores.values())
        return max(vals) if args.agg == "max" else sum(vals) / len(vals)

    base_all = sb.score(text)
    base = agg_of(base_all)
    print(f"初始分数 {base:.3f}   " +
          "  ".join(f"{k.split('__')[-1][:18]}={v:.3f}" for k, v in base_all.items()))

    if base <= args.target:
        print(f"\n已低于目标 {args.target}，无需改写。")
        if args.out:
            open(args.out, "w", encoding="utf-8").write(text)
        return 0

    # 逐段分数，决定改哪些段
    para_scores = []
    for i, p in enumerate(paras, 1):
        sc, _ = sb.worst(p, agg=args.agg)
        para_scores.append({"idx": i, "score": round(sc, 4), "chars": n_chars(p)})
    todo = [i for i, s in enumerate(para_scores)
            if (not args.only_flagged) or s["score"] >= args.flag_threshold]
    print(f"需改写段落 {len(todo)}/{len(paras)} 段\n")

    # **先判断有没有必要载入生成模型。**
    # 生成模型是数 GB 的权重，载入本身就要几十秒；而如果没有任何段落需要改，
    # 载入纯属浪费。更实际的场景是用户在**还没补具体信息**时就来跑这个引擎 ——
    # 那种情况下先跑 anchor.py 更有效（实测见 references/edit-effects.md）。
    if not todo:
        print(f"没有段落超过阈值 {args.flag_threshold}，无需 LLM 改写。")
        if args.out:
            open(args.out, "w", encoding="utf-8").write(text)
        return 0

    rw = Rewriter(model, sb)
    out_paras, history = [], []
    for i, p in enumerate(paras):
        if i not in todo:
            out_paras.append(p)
            history.append({"idx": i + 1, "score_before": para_scores[i]["score"],
                            "score_after": para_scores[i]["score"], "changed": False})
            continue
        newp, sc, hist = rw.reduce_paragraph(p, args.target, args.max_iters,
                                             verbose=not args.quiet)
        out_paras.append(newp)
        history.append({"idx": i + 1, "score_before": para_scores[i]["score"],
                        "score_after": round(sc, 4), "changed": newp != p,
                        "chars_before": n_chars(p), "chars_after": n_chars(newp),
                        "trace": hist})
        if not args.quiet:
            print(f"  段{i+1}: {para_scores[i]['score']:.3f} → {sc:.3f} "
                  f"{'✓' if sc <= args.target else '(未达目标)'}")

    result = "\n\n".join(out_paras)
    final_all = sb.score(result)
    final = agg_of(final_all)
    changed = sum(1 for h in history if h["changed"])

    print("\n" + "=" * 64)
    print(f"初始 {base:.3f}  →  最终 {final:.3f}   "
          f"降幅 {base - final:+.3f}（{(base - final) / max(base, 1e-9):.1%}）")
    print(f"改写段落 {changed}/{len(paras)}   "
          f"字数 {n_chars(text)} → {n_chars(result)}")
    print("  ".join(f"{k.split('__')[-1][:18]}={v:.3f}" for k, v in final_all.items()))
    hit = final <= args.target
    print(f"目标 {args.target}：{'✓ 达标' if hit else '未达标（可提高 --max-iters 重试）'}")
    print("=" * 64)
    print("提醒：这是**开源检测器上的**分数变化。知网/PaperPass/学习通等平台"
          "本机无法测量，不构成对它们的预测。提交前请通读改写稿。")
    print()
    print("⚠️ **本引擎的机制与实测结论相冲突，必须知道**：")
    print("   语料实测（n=240/组，三检测器）显示 "
          "**LLM 改写不降低检出率**：")
    print("     M00 基底 75% → M10 加事实 74% → M01 加立场 75% → M11 两者 72%")
    print("     （d_hello，阈值 0.5；d_cmj/d_zhv3 上是持平或略升）")
    print("   原因见 references/edit-effects.md：让 LLM『改写得更具体』，")
    print("   产出的是**更流畅的概括**，正好是平台『改写后信息量差』信号的靶心。")
    print("   → **优先用 scripts/anchor.py 补你自己的真实信息**；")
    print("     本引擎只适合已经补完事实、还想试表层改写的场景。")

    out_path = args.out or (os.path.splitext(args.path)[0] + "_降低AIGC.txt")
    open(out_path, "w", encoding="utf-8").write(result)
    print(f"\n改写结果 -> {out_path}")

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        json.dump({"input": os.path.basename(args.path), "detectors": det_paths,
                   "target": args.target, "agg": args.agg,
                   "score_before": round(base, 4), "score_after": round(final, 4),
                   "paragraphs": history},
                  open(args.report, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"逐段报告 -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

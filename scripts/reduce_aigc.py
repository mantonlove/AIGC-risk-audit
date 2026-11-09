#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地模型辅助的候选改写与核查工具。

怎么用
------
    # 1) 查看本地模型输出，定位需要人工核对的行
    python3 scripts/score_text.py 论文.txt

    # 2) 生成候选并保存逐行核查稿
    python3 scripts/reduce_aigc.py 论文.txt --out 论文_候选核查.txt

    # 3) 调整本地停止线与尝试次数
    python3 scripts/reduce_aigc.py 论文.txt --target 0.1 --max-iters 5 --out out.txt

    # 只看某些段落：先切好文件，或直接粘贴单段文本到 .txt

**先读这条**：在这批本地语料中，LLM 改写未见稳定降低检出率
（n=240/组，三检测器）。
可先用 `scripts/anchor.py` 找到需要作者补充真实依据的位置。详见
`references/edit-effects.md`。

工作原理（仅用于本地审查）
------------------------------------
1. 用本地检测器查看各行的模型输出；
2. 只对足够长的行尝试生成候选，标题、公式和未选行按原文 offset 保留；
3. 数字、引文、否定和结论方向等显式字段核对失败时，候选直接丢弃；
4. 记录本地模型变化与拒绝原因，供作者逐句核查。

分数的局限
--------------------------
文献实测同一编辑动作对不同检测器的影响方向可能相反
（Style as a Confound, arXiv:2608.26710：13 个检测器 FPR 从 0.0% 到 100.0%）。
本项目自己也在 120 对同题文本上复现了这一点（见 references/edit-effects.md）：
"加自我设限"在 d_hello 上 Δ=−0.263，在 d_cmj 上 Δ=+0.069。

⚠️ **默认的两个检测器不是独立证据。** 实测 corr(d_cmj, d_zhv3) = +0.928（近冗余），
而它们与 d_hello 只有 +0.42 / +0.34。所以 --detectors 取多个时，
只能在报告里说"在 N 个**中度相关**的检测器上"，不要说"多个独立检测器都同意"。

⚠️ **本工具用 LLM 生成候选；这批本地语料未见稳定改善**
（语料里 M10/M01/M11 三种 LLM 改写相对基底持平或略升，n=240/组）。
补真实信息必须由作者提供；`scripts/anchor.py` 只负责定位待核查位置。

边界（必须知道）
----------------
- 这里是**本地开源检测器未经校准的 AI 类得分**，不是知网/PaperPass/学习通的
  检测字段，不能预测这些平台的百分比。
- 确定性字段核查不能证明完整语义等价。**提交前请作者逐句通读**。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="本地模型辅助的中文稿件候选审查",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("path", help="输入文本（.txt/.md/.docx/.doc）")
    ap.add_argument("--out", default=None,
                    help="候选核查稿输出路径（默认 <原名>_候选核查.txt）")
    ap.add_argument("--target", type=float, default=0.2,
                    help="本地模型停止线，达到即停（默认 0.2；非平台阈值）")
    ap.add_argument("--max-iters", type=int, default=3, help="每行最多尝试轮数")
    ap.add_argument("--detectors", nargs="+", default=["d_hello", "d_cmj"],
                    help="用于打分与择优的检测器：d_hello / d_cmj / d_zhv3")
    ap.add_argument("--agg", choices=["max", "mean"], default="max",
                    help="非空行聚合：max=最高行（默认）；mean=等权平均")
    ap.add_argument("--only-flagged", action="store_true",
                    help="只尝试超过本地标记线的正文行，其余原样保留")
    ap.add_argument("--flag-threshold", type=float, default=0.5)
    ap.add_argument("--report", default=None, help="逐行本地得分报告（JSON）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    bench = _bench.find_bench()
    _bench.ensure_venv(bench)      # 自动切到 bench 的 venv（torch 在那里）
    _bench.ensure_importable(bench)
    from corpus import editable_line_spans, line_spans, n_chars, replace_spans
    from rewriter import Rewriter, ScoreBoard

    text = _bench.read_text(args.path)
    out_path = args.out or (os.path.splitext(args.path)[0] + "_候选核查.txt")
    if os.path.abspath(out_path) == os.path.abspath(args.path):
        raise SystemExit("输出路径不能与输入文件相同，以免覆盖原稿。")

    def save_result(result):
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(result)
        print(f"候选核查稿 -> {out_path}")

    spans = line_spans(text)
    editable = {(start, end) for start, end, _ in editable_line_spans(text)}
    det_paths = _bench.detector_paths(bench, args.detectors)
    if len(det_paths) != len(args.detectors):
        raise SystemExit("请求的检测器路径不齐，已停止评分；请补齐模型后重试。")

    print(f"输入：{os.path.basename(args.path)}  {n_chars(text)} 字  {len(spans)} 行")
    if os.path.splitext(args.path)[1].lower() in (".doc", ".docx"):
        print("文档已提取为纯文本；输出为 .txt，不保留原文件排版。")
    print(f"检测器：{[os.path.basename(p)[:30] for p in det_paths]}")
    print("（未经校准的本地模型 AI 类得分；不能预测商业平台百分比）\n")

    sb = ScoreBoard("", det_paths)

    def agg_of(scores):
        vals = list(scores.values())
        return max(vals) if args.agg == "max" else sum(vals) / len(vals)

    base_all = sb.score(text, agg=args.agg)
    base = agg_of(base_all)

    def save_report(final_score, history, note):
        if not args.report:
            return
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump({"input": os.path.basename(args.path), "detectors": det_paths,
                       "scoring_version": "token-window-v2/all-nonblank-lines",
                       "target": args.target, "agg": args.agg,
                       "score_before": round(base, 4),
                       "score_after": round(final_score, 4),
                       "note": note, "paragraphs": history},
                      fh, ensure_ascii=False, indent=2)
        print(f"逐行报告 -> {args.report}")

    print(f"初始分数 {base:.3f}   " +
          "  ".join(f"{k.split('__')[-1][:18]}={v:.3f}" for k, v in base_all.items()))

    if base <= args.target:
        print(f"\n本地得分已低于停止线 {args.target}，未生成候选。")
        save_result(text)
        save_report(base, [], "已低于本地停止线，原文保留")
        return 0

    # 每个非空行保留原文 offset；短行也计入审查，但不交给改写模型。
    para_scores = []
    for i, (start, end, raw) in enumerate(spans, 1):
        p = raw.strip()
        sc, _ = sb.worst(p, agg=args.agg)
        para_scores.append({"idx": i, "start": start, "end": end,
                            "score": round(sc, 4), "chars": n_chars(p)})
    todo = [i for i, s in enumerate(para_scores)
            if (s["start"], s["end"]) in editable and
            ((not args.only_flagged) or s["score"] >= args.flag_threshold)]
    print(f"可尝试改写 {len(todo)}/{len(spans)} 行（短行与结构性文本原样保留）\n")

    # 没有可编辑正文行时，不载入数 GB 的生成模型。
    if not todo:
        print("没有符合条件的正文行（可能为短行、结构性文本或低于本地标记线）。")
        save_result(text)
        save_report(base, [], "没有符合条件的正文行，原文保留")
        return 0

    model = _bench.generator_path(bench)
    rw = Rewriter(model, sb)
    replacements, history = [], []
    for i, (start, end, raw) in enumerate(spans):
        p = raw.strip()
        if i not in todo:
            history.append({"idx": i + 1, "start": start, "end": end,
                            "score_before": para_scores[i]["score"],
                            "score_after": para_scores[i]["score"], "changed": False,
                            "reason": ("短行或结构性文本保留"
                                       if (start, end) not in editable else "未超过改写线")})
            continue
        newp, sc, hist = rw.reduce_paragraph(p, args.target, args.max_iters,
                                             verbose=not args.quiet, agg=args.agg)
        if newp != p:
            lead = raw[:len(raw) - len(raw.lstrip())]
            trail = raw[len(raw.rstrip()):]
            replacements.append((start, end, lead + newp + trail))
        history.append({"idx": i + 1, "start": start, "end": end,
                        "score_before": para_scores[i]["score"],
                        "score_after": round(sc, 4), "changed": newp != p,
                        "chars_before": n_chars(p), "chars_after": n_chars(newp),
                        "trace": hist})
        if not args.quiet:
            print(f"  行{i+1}: {para_scores[i]['score']:.3f} → {sc:.3f} "
                  f"{'已到本地停止线' if sc <= args.target else '未到本地停止线'}")

    result = replace_spans(text, replacements)
    final_all = sb.score(result, agg=args.agg)
    final = agg_of(final_all)
    changed = sum(1 for h in history if h["changed"])

    print("\n" + "=" * 64)
    print(f"初始 {base:.3f}  →  最终 {final:.3f}   "
          f"本地得分差 {base - final:+.3f}")
    print(f"改写行 {changed}/{len(spans)}   "
          f"字数 {n_chars(text)} → {n_chars(result)}")
    print("  ".join(f"{k.split('__')[-1][:18]}={v:.3f}" for k, v in final_all.items()))
    hit = final <= args.target
    print(f"本地停止线 {args.target}：{'已达到' if hit else '未达到'}")
    print("=" * 64)
    print("提醒：这里只显示本地模型输出，不能预测知网/PaperPass/学习通结果。"
          "确定性门禁无法证明完整语义，提交前须由作者逐句核对。")
    print()
    print("这批本地语料未见 LLM 改写稳定改善检测结果；"
          "可用 scripts/anchor.py 定位待补的真实依据。")

    print()
    save_result(result)

    save_report(final, history, "仅供本地模型与作者核查")
    return 0


if __name__ == "__main__":
    sys.exit(main())

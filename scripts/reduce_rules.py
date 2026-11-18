#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性规则算子：不调用任何生成模型，逐段施加、逐段复核、只保留降分的改动。

为什么有它
----------
`references/edit-effects.md` 与 `aigc-bench/out/FINAL_REPORT.md` 的结论是：
本地 LLM 改写在这批语料上**没有稳定降低检出率**（72 个 Δ 无一通过 Holm 校正，
多种改写反而升分）。原因之一是改写器与生成器是同一个模型，同族指纹自我强化。

`aigc-bench/harness/dose_response.py` 与 `structural_ops.py` 在 240 篇同源文本上
测得的确定性算子效应（三个本地检测器的均值 Δ，基线 d_hello=0.744 / d_cmj=0.914 /
d_zhv3=0.938）：

    算子                    d_hello   d_cmj    d_zhv3
    拆句(逗号→句号, 全量)     −0.146   −0.029   −0.034
    等长词面替换(饱和 4.8%)   −0.125   −0.033   −0.024
    删连接词                 −0.080   −0.016   −0.009
    删"的"(每两个删一个)      −0.041   −0.014   −0.011
    合句                     ≈0       ≈0       ≈0
    打乱句序(诊断)            −0.006   −0.005   −0.004

两条由此确立的事实：
1. **词面替换很快就饱和**——34 对词表在 170 字量级的段落上最多只能改到约 4.8%
   的字符，再加剂量也没有更多可替换位点。
2. **效应几乎全部集中在基线低分位的段落**。按基线三分位拆开，拆句在低分位
   为 −0.219（d_hello）/ −0.099（d_zhv3），中分位约 0.00，高分位为 0.000。
   也就是说：**对已经 0.9 以上的段落做任何编辑都是零效应**，值得动手的只有
   本来就临近标记线的段落。

因此本工具的做法是：**先分段打分，只对超过标记线的段落施算子，每步重打分，
不降就回退**。它不做全文统一改写，也不追求把 0.95 压到 0.90。

怎么用
------
    # 只看报告，不写文件
    python3 scripts/reduce_rules.py 论文.txt --dry-run

    # 输出候选稿与逐段报告
    python3 scripts/reduce_rules.py 论文.txt --out 论文_候选.txt --report 报告.json

    # 调标记线与算子开关
    python3 scripts/reduce_rules.py 论文.txt --threshold 0.5 --split-min 12 \
        --no-lexicon --max-rounds 3

边界（必须知道）
----------------
- 这里的分数是**本地开源检测器未经校准的 AI 类得分**，不是知网/维普/PaperPass
  的检测字段，不能预测它们的百分比，也不构成达标承诺。
- 算子只做句读与词面层面的等长替换，**不改数字、否定、模态词和术语**；
  但"只改标点"仍会改变行文节奏，作者必须逐段通读确认可读性与语气。
- 工具保留原稿，输出另存；不会覆盖输入文件。
"""

import argparse
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench  # noqa: E402
import plain_lexicon as _lx  # noqa: E402

# ---------------------------------------------------------------- 算子定义

NO_TOUCH = re.compile(r"\d|不|无|非|未|没|应|须|必须|禁止|不得")

CONNS = ["因此", "此外", "然而", "同时", "并且", "综上", "总之",
         "首先", "其次", "另外", "继而", "从而"]

# 词表已迁到 plain_lexicon.py（按类别组织、允许不等长、带复合词守卫）。
# 旧版那张 34 对等长表只允许等长替换，实测剂量上限锁死在 4.8%，瓶颈在词表不在剂量。

SENT_END = "。！？；"

# 语法守卫：不加这些守卫，"拆句"会把句子切坏，分数掉下来是因为文本不通了。
# 实测（aigc-bench/out3/band_ops.json vs guarded_ops.json）：盲目拆句在带宽内
# 跨阈率 55.6%/75.7%，但输出里大量出现"其教学强调经世致用。以"实学"为主。"
# "实验结果表明。该算法具有…"这类残句。加守卫后跨阈率降到 29%/44%，文本可读。
QUOTE_PAIRS = {"“": "”", "‘": "’", "「": "」", "『": "』", "《": "》",
               "（": "）", "(": ")"}

# 左段以这些谓词结尾时，右段是它的宾语，不能断
NO_SPLIT_AFTER = ("表明", "显示", "说明", "认为", "指出", "发现", "证明", "可见",
                  "意味着", "包括", "例如", "比如", "在于", "导致", "使得",
                  "提出", "强调", "主张", "反映", "体现", "表示", "得出", "得到")

# 右段以这些词开头时，多半是没有主语的延续成分，不能断
NO_SPLIT_BEFORE = ("以", "而", "并", "且", "或", "及", "与", "但", "却", "也", "都",
                   "就", "才", "则", "即", "仍", "更", "还", "又", "因", "由于",
                   "为了", "通过", "根据", "在", "对", "从", "向", "给", "被", "把",
                   "由", "使", "令", "让", "其", "此", "之", "所", "等", "地", "得",
                   "很", "较", "均", "皆", "亦", "故", "若", "虽", "如", "否则",
                   "从而", "进而", "以便", "以免", "以及", "能", "可", "会", "应",
                   "须", "需", "将", "已", "未", "有", "没", "不", "用", "按", "依",
                   "据", "同")


def _quote_mask(text):
    """标记每个字符是否在成对引号/括号内（含端点）。"""
    mask = [False] * len(text)
    stack = []
    for i, ch in enumerate(text):
        if ch in QUOTE_PAIRS:
            stack.append(QUOTE_PAIRS[ch])
            mask[i] = True
        elif stack and ch == stack[-1]:
            stack.pop()
            mask[i] = True
        elif stack:
            mask[i] = True
    return mask


def _next_clause(text, i):
    j = i + 1
    while j < len(text) and text[j] not in SENT_END:
        j += 1
    return text[i + 1:j]


def op_split(text, min_clause=12, prob=0.5, rng=None, min_next=8):
    """把长度达到 min_clause 的分句后的逗号改成句号，并施加语法守卫。

    守卫（缺一不可，否则会切出残句）：
    1. 引号/括号内不断；
    2. 右侧分句少于 min_next 字不断；
    3. 左段以带宾语谓词结尾不断（"表明/说明/在于…"后面是宾语）；
    4. 右段以从属/介词/连词/副词开头不断（"以…为主""从而…"会被切成残句）。
    prob 控制拆开比例，用来在可读性不变的前提下控制剂量。
    """
    rng = rng or random.Random(0)
    qm = _quote_mask(text)
    res, seg_start = [], 0
    for i, ch in enumerate(text):
        if ch in SENT_END:
            seg_start = i + 1
            res.append(ch)
            continue
        if ch != "，" or qm[i]:
            res.append(ch)
            continue
        prev = text[seg_start:i].strip()
        nxt = _next_clause(text, i).strip()
        if len(prev) < min_clause or len(nxt) < min_next:
            res.append(ch)
            continue
        if any(prev.endswith(w) for w in NO_SPLIT_AFTER):
            res.append(ch)
            continue
        if any(nxt.startswith(w) for w in NO_SPLIT_BEFORE):
            res.append(ch)
            continue
        if rng.random() > prob:
            res.append(ch)
            continue
        res.append("。")
        seg_start = i + 1
    return "".join(res)


def op_delconn(text, rng=None):
    """删连接词。只在句首或逗号后删，避免拆散词内。"""
    for c in sorted(CONNS, key=len, reverse=True):
        text = re.sub(rf"(^|(?<=[。！？；，])){c}", "", text)
    return re.sub(r"[，,]{2,}", "，", text).strip("，") or text


def op_lexicon(text, ratio=0.04, rng=None, level="safe"):
    """等义压缩：把冗长的学术套话换成同义的简洁说法。

    与旧实现的差别（旧版实测剂量上限只有 4.8%，瓶颈在词表不在剂量）：
    - 词表从 34 对扩到 268 对（safe）/291 对（plain），并允许不等长替换；
    - 复合词守卫：`研究|生`、`大|部分` 这类匹配一律跳过，避免"研究生"被改成"探讨生"；
    - 数字与否定词邻域跳过；长词优先；只在原文上定位，不做链式替换
      （所以不会出现"因此→所以→…"的二次改写）。

    level="safe" 只做等义压缩，不改文体，适合论文正文；
    level="plain" 允许口语化（方法→办法、能力→本事），可能进一步降分但文体会受损。
    """
    out, _n, _c = _lx.apply(text, ratio=ratio, rng=rng, level=level)
    return out


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(
        description="确定性规则算子：只改超阈段落，只保留降分的改动",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("path", help="输入文本（.txt/.md/.docx/.doc）")
    ap.add_argument("--out", default=None, help="候选稿输出路径（默认不写）")
    ap.add_argument("--report", default=None, help="逐段报告 JSON 输出路径")
    ap.add_argument("--dry-run", action="store_true", help="只出报告，不写候选稿")
    ap.add_argument("--detectors", nargs="+", default=["d_hello", "d_cmj"],
                    help="用于打分与择优：d_hello / d_cmj / d_zhv3")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="标记线；只处理达到该值的段落（默认 0.5，非平台阈值）")
    ap.add_argument("--agg", choices=["max", "mean"], default="max",
                    help="多检测器聚合：max=最坏检测器（默认），mean=等权")
    ap.add_argument("--lexicon-level", choices=["safe", "plain"], default="safe",
                    help="safe=只做等义压缩（默认）；plain=允许口语化替换，可能更降分但文体会受损")
    ap.add_argument("--split-min", type=int, default=12,
                    help="拆句时要求逗号左侧分句至少多少字（默认 12）")
    ap.add_argument("--split-prob", type=float, default=0.5,
                    help="达到 --split-min 的逗号被拆开的概率（默认 0.5）")
    ap.add_argument("--no-lexicon", action="store_true", help="关闭词面替换")
    ap.add_argument("--no-delconn", action="store_true", help="关闭连接词删除")
    ap.add_argument("--lexicon-ratio", type=float, default=0.04,
                    help="词面替换目标改动字符占比（默认 0.04）")
    ap.add_argument("--max-rounds", type=int, default=3, help="每段最多尝试轮数")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--json", action="store_true", help="把报告打到 stdout")
    args = ap.parse_args()

    bench = _bench.find_bench()
    _bench.ensure_venv(bench)
    _bench.ensure_importable(bench)
    from corpus import line_spans, n_chars
    from detectors import Detector

    text = _bench.read_text(args.path)
    spans = [(s, e, raw) for s, e, raw in line_spans(text) if raw.strip()]
    paras = [raw.strip() for _, _, raw in spans]
    paths = _bench.detector_paths(bench, args.detectors)
    if len(paths) != len(args.detectors):
        raise SystemExit("请求的检测器路径不齐，已停止评分；请补齐模型后重试。")

    dets = [(os.path.basename(p), Detector(os.path.basename(p), p)) for p in paths]

    def score_of(t):
        vs = [float(d.score_batch([t])[0]) for _, d in dets]
        return (max(vs) if args.agg == "max" else sum(vs) / len(vs)), vs

    if not args.json:
        print(f"文本 {n_chars(text)} 字，{len(paras)} 段；"
              f"标记线 {args.threshold}（本地标记线，非平台阈值）")
        print(f"检测器：{[n for n, _ in dets]}  聚合={args.agg}\n")

    # 组装算子阶梯（按实测强度从强到弱，但保守的先做）
    ladder = []
    if args.split_prob > 0:
        ladder.append(("split", lambda t, r: op_split(
            t, min_clause=args.split_min, prob=args.split_prob, rng=r)))
    if not args.no_delconn:
        ladder.append(("delconn", op_delconn))
    if not args.no_lexicon:
        ladder.append(("lexicon", lambda t, r: op_lexicon(
            t, ratio=args.lexicon_ratio, rng=r, level=args.lexicon_level)))

    records, out_paras = [], []
    for idx, p in enumerate(paras, 1):
        before, before_by = score_of(p)
        cur, cur_score, applied = p, before, []
        if before >= args.threshold:
            for rnd in range(args.max_rounds):
                best = None
                for name, fn in ladder:
                    if name in applied:
                        continue
                    cand = fn(cur, random.Random(args.seed + rnd))
                    if cand == cur:
                        continue
                    s, _ = score_of(cand)
                    if s < cur_score - 1e-6 and (best is None or s < best[1]):
                        best = (cand, s, name)
                if best is None:
                    break
                cur, cur_score = best[0], best[1]
                applied.append(best[2])
                if cur_score < args.threshold:
                    break
        out_paras.append(cur)
        after, after_by = score_of(cur) if cur != p else (before, before_by)
        records.append({
            "index": idx, "chars": n_chars(p),
            "before": round(before, 4), "after": round(after, 4),
            "delta": round(after - before, 4),
            "before_by_detector": [round(x, 4) for x in before_by],
            "after_by_detector": [round(x, 4) for x in after_by],
            "applied": applied,
        })
        if not args.json and (applied or before >= args.threshold):
            tag = "→".join(applied) if applied else "无可用算子"
            print(f"  段{idx:3d} ({n_chars(p):4d}字) {before:.3f} → {after:.3f} "
                  f"({after - before:+.3f})  {tag}")

    # 还原全文：非空行替换，空行与结构原样保留
    result_lines, k = [], 0
    for s, e, raw in line_spans(text):
        if raw.strip():
            result_lines.append(out_paras[k])
            k += 1
        else:
            result_lines.append(raw)
    result = "\n".join(result_lines)

    touched = [r for r in records if r["applied"]]
    summary = {
        "file": os.path.basename(args.path), "chars": n_chars(text),
        "n_para": len(paras), "n_touched": len(touched),
        "detectors": [n for n, _ in dets], "agg": args.agg,
        "threshold": args.threshold,
        "before_mean": round(sum(r["before"] for r in records) / len(records), 4),
        "after_mean": round(sum(r["after"] for r in records) / len(records), 4),
        "n_above_before": sum(1 for r in records if r["before"] >= args.threshold),
        "n_above_after": sum(1 for r in records if r["after"] >= args.threshold),
        "chars_above_before": sum(r["chars"] for r in records
                                  if r["before"] >= args.threshold),
        "chars_above_after": sum(r["chars"] for r in records
                                 if r["after"] >= args.threshold),
        "params": {
            "split_min": args.split_min, "split_prob": args.split_prob,
            "lexicon_ratio": args.lexicon_ratio,
            "delconn": not args.no_delconn, "lexicon": not args.no_lexicon,
            "max_rounds": args.max_rounds, "seed": args.seed,
        },
        "paragraphs": records,
    }

    if not args.json:
        print(f"\n触及段落 {len(touched)}/{len(paras)}；"
              f"均值 {summary['before_mean']:.4f} → {summary['after_mean']:.4f}")
        print(f"超阈段落 {summary['n_above_before']} → {summary['n_above_after']}；"
              f"超阈字数 {summary['chars_above_before']} → "
              f"{summary['chars_above_after']}")
        print("提醒：本地检测器得分，不能预测知网、维普、PaperPass 的百分比；"
              "标点级改动仍需作者逐段通读。")

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)) or ".", exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        if not args.json:
            print(f"报告 -> {args.report}")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.out and not args.dry_run:
        if os.path.abspath(args.out) == os.path.abspath(args.path):
            raise SystemExit("输出路径不能与输入文件相同，以免覆盖原稿。")
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(result)
        print(f"候选稿 -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

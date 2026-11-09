#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文风规整度指标（secondary profile）—— 可计算、有文献来源的表面特征。

定位（必读）
------------
本模块输出的是**可计算的表面统计量**，用于定位"值得人工复核"的段落，
**不是**检测器分数，**不是**生成来源判定，**不是**"改了就降分"的因果清单。

依据与来源
----------
1. `levmo1/aigc-detector`（MIT，中文论文 AI 写作痕迹工具）给出的 6 个统计特征及
   其阈值：句长变异系数、字符 3-gram 重复率、连接词密度、标点密度、
   词汇多样性、三段式并列。本模块采用其阈值的**量级**，但阈值在本项目内
   重新标定（见 --calibrate）。
2. 消融研究显示，删掉 top-10 判别特征后检测器准确率仅降约 1%
   (arXiv:2505.24523)，因此**单项命中不构成任何判定依据**。本模块全部输出
   只用于排序复核优先级。
3. `Style as a Confound`（arXiv:2608.26710）：文本被编辑得更规范时，
   不同检测器的分数**方向相反**。因此本模块不预测分数方向。

用法
----
    python3 style_profile.py 论文.txt
    python3 style_profile.py 论文.txt --json
"""

import argparse
import json
import os
import re
import statistics
import sys

RE_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")

TRANSITIONS = ["首先", "其次", "再次", "最后", "此外", "另外", "同时", "因此",
               "所以", "然而", "但是", "总之", "综上所述", "由此可见",
               "值得注意的是", "一方面", "另一方面", "换言之", "简而言之"]
DASH_COLON = re.compile(r"[——：:]")
RULE_OF_THREE = re.compile(r"[\u4e00-\u9fff]{2,8}、[\u4e00-\u9fff]{2,8}、[\u4e00-\u9fff]{2,8}")


def cjk_len(t):
    return len(RE_CJK.findall(t or ""))


def sentences(text):
    return [s.strip() for s in RE_SENT_SPLIT.split(text or "") if cjk_len(s) > 0]


def paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if cjk_len(p) >= 15]


def metric_sentence_cv(sents):
    if len(sents) < 3:
        return None
    lens = [cjk_len(s) for s in sents]
    m = statistics.mean(lens)
    if m == 0:
        return None
    return statistics.stdev(lens) / m


def metric_ngram_repeat(text, n=3):
    """字符 n-gram 重复率 = 1 - 唯一 n-gram 数 / 总 n-gram 数。"""
    chars = "".join(RE_CJK.findall(text or ""))
    if len(chars) < n + 1:
        return None
    grams = [chars[i:i + n] for i in range(len(chars) - n + 1)]
    return 1.0 - len(set(grams)) / len(grams)


def metric_transition_density(text):
    n = cjk_len(text)
    if n == 0:
        return None
    return sum(text.count(t) for t in TRANSITIONS) / n


def metric_dash_colon_density(text):
    n = cjk_len(text)
    if n == 0:
        return None
    return len(DASH_COLON.findall(text)) / n


def metric_lexical_diversity(text):
    chars = RE_CJK.findall(text or "")
    if len(chars) < 20:
        return None
    return len(set(chars)) / len(chars)


def metric_rule_of_three(text):
    return len(RULE_OF_THREE.findall(text or ""))


# 阈值：按数据集中标记为人类的中文论文摘要分布设置（n=240）。
# 标定原则：取各指标的 p05/p95 作为筛查界；这不保证组合触发率或泛化误标率。
# 这取代了原先照搬英文工具的量级——实测显示那些量级对中文严重不符
# （例如人类中文的字符 3-gram 重复率中位就有 0.111，原阈值 0.04 会误标绝大多数人类文本）。
#
# 实测人类分布（n=240 篇 CNKI 论文摘要）：
#   指标                  p05     p25     中位     p75     p95
#   sentence_cv          0.159   0.316   0.416   0.522   0.744
#   ngram_repeat_3       0.019   0.067   0.111   0.149   0.235
#   transition_density   0.000   0.000   0.000   0.005   0.010
#   dash_colon_density   0.000   0.000   0.000   0.000   0.011
#   lexical_diversity    0.438   0.510   0.552   0.592   0.654
THRESHOLDS = {
    # 偏低更规整
    "sentence_cv": {"low": 0.16, "very_low": 0.10,
                    "direction": "lower_is_more_regular"},
    "lexical_diversity": {"low": 0.44, "very_low": 0.36, "direction": "lower"},
    # 偏高更规整
    "ngram_repeat_3": {"high": 0.235, "very_high": 0.30,
                       "direction": "higher_is_more_regular"},
    "transition_density": {"high": 0.010, "very_high": 0.016, "direction": "higher"},
    "dash_colon_density": {"high": 0.011, "very_high": 0.020, "direction": "higher"},
    # 三段式并列次数为**计数**而非密度：人类论文摘要中位为 0、p95 为 0，
    # 故只要有 1 次即列为待核查；3 次以上归入更高的启发式档位
    "rule_of_three": {"high": 1, "very_high": 3, "direction": "higher"},
}

# 验证结果（在带标注语料上实测：480 条人类 + 240 条 GPT-3.5 + 124 条 Qwen2.5）
#   组别              单项≥1   组合≥2   强组合      n
#   真实人类论文摘要   43.8%     7.5%     3.8%     240
#   真实人类问答       50.0%    12.1%     5.0%     240
#   GPT-3.5 输出       72.5%    36.7%    25.0%     240
#   Qwen2.5 生成       79.4%    23.8%    11.1%     124
#
# 这些是旧版验证记录中的样本观察值，逐篇指标和拆分索引未随仓库保存；
# 当前只把组合触发作为人工复核排序线索，不作为来源判定或误标率承诺。
# 局限：
#   - AI 样本来自两个生成模型族（GPT-3.5 / Qwen2.5），对更新的模型未验证；
#   - 该指标与"检测器分数"是两回事，**不得互相替代**；
#   - 阈值由论文摘要设置，用于全文或其他文体时触发率可能改变。

# 标定信息（供报告披露）
CALIBRATION = {
    "source": "240 篇数据集中标记为人类撰写的中文论文摘要",
    "rule": "各单项低/高界取样本 p05/p95；组合触发率需另行验证",
    "caveat": ("样本分位数不保证误标率；换领域需重新标定并用独立样本验证。"
               "阈值不是诊断界值，也不预测检测器分数方向。"),
}


def profile(text):
    sents = sentences(text)
    paras = paragraphs(text)
    m = {
        "chars_cjk": cjk_len(text),
        "n_sent": len(sents),
        "n_para": len(paras),
        "sentence_cv": metric_sentence_cv(sents),
        "ngram_repeat_3": metric_ngram_repeat(text),
        "transition_density": metric_transition_density(text),
        "dash_colon_density": metric_dash_colon_density(text),
        "lexical_diversity": metric_lexical_diversity(text),
        "rule_of_three": metric_rule_of_three(text),
    }

    flags = []
    for key, th in THRESHOLDS.items():
        v = m.get(key)
        if v is None:
            continue
        if th["direction"] == "lower_is_more_regular":
            if v <= th["very_low"]:
                flags.append((key, v, "very_low"))
            elif v <= th["low"]:
                flags.append((key, v, "low"))
        elif th["direction"] == "lower":
            if v <= th["very_low"]:
                flags.append((key, v, "very_low"))
            elif v <= th["low"]:
                flags.append((key, v, "low"))
        else:
            if v >= th["very_high"]:
                flags.append((key, v, "very_high"))
            elif v >= th["high"]:
                flags.append((key, v, "high"))
    m["_flags"] = flags
    m["_flag_count"] = len(flags)
    m["_very_count"] = sum(1 for _, _, lv in flags if lv.startswith("very"))
    # 组合规则：单项命中噪声大（人类基线 20-38%），
    # 历史样本中「≥2 项同时触发」的频率低于单项命中；不作泛化保证。
    m["_combined"] = len(flags) >= 2
    m["_combined_strong"] = (len(flags) >= 2 and m["_very_count"] >= 1) or len(flags) >= 3
    return m


def render(text, prof):
    L = []
    A = L.append
    A("=" * 66)
    A("文风规整度指标（secondary profile）")
    A("=" * 66)
    A("!! 这是可计算的表面统计量，不是检测器分数，也不能判定生成来源。")
    A("!! 单项命中不构成任何判定依据；本表只用于排序人工复核的优先级。")
    A("")
    A(f"中文 {prof['chars_cjk']} 字 ｜ 句 {prof['n_sent']} ｜ 段 {prof['n_para']}")
    A("")
    fmt = [("sentence_cv", "句长变异系数", "偏低=句长过匀"),
           ("ngram_repeat_3", "字符3-gram重复率", "偏高=用词重复"),
           ("transition_density", "连接词密度", "偏高=路标词堆叠"),
           ("dash_colon_density", "破折号/冒号密度", "偏高=解释性插入语堆叠"),
           ("lexical_diversity", "字面多样性(唯字/总字)", "偏低=用字单一"),
           ("rule_of_three", "三段式并列次数", "偏高=模板化排比")]
    for key, name, hint in fmt:
        v = prof.get(key)
        vs = "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))
        A(f"  {name:20s} {vs:>10s}   ({hint})")
    A("")
    A(f"标定语料：{CALIBRATION['source']}")
    A(f"标定规则：{CALIBRATION['rule']}")
    A(f"适用边界：{CALIBRATION['caveat']}")
    A("")
    if prof["_flags"]:
        A(f"触发 {prof['_flag_count']} 项筛查界（其中 {prof['_very_count']} 项为较强触发）：")
        for k, v, lv in prof["_flags"]:
            A(f"  - {k} = {v:.4f}  [{lv}]")
        A("")
        A(f"组合判据（≥2 项同时触发）：{'**命中**' if prof['_combined'] else '未命中'}"
          f"　强组合判据：{'**命中**' if prof['_combined_strong'] else '未命中'}")
        A("旧版样本记录的组合触发率：人类组 7.5%~12.1%、AI 组 23.8%~36.7%；"
          "逐篇验证数据未保存，此处只作人工复核排序线索。")
        A("这些条目提示**值得逐段复核**，不提示分数会怎么变。")
    else:
        A("未触发任何阈值项。")
    A("")
    A("正确用法：针对触发的段落问“这里能不能写得更具体、更能负责”。")
    A("错误用法：把这些数字当成要压下去的指标。")
    A("=" * 66)
    return "\n".join(L)


def calibrate(paths, lo_pct=0.05, hi_pct=0.95):
    """在给定语料上重新标定阈值。

    换领域时应重新估计分位数，并在独立样本上验证组合触发率。
    对每个指标取分布的下/上分位数作为界，不保证约 lo_pct/hi_pct
    的文档最终被组合规则标记。
    """
    rows = []
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("text")
                if t and len(RE_CJK.findall(t)) >= 120:
                    rows.append(t)
    if len(rows) < 30:
        raise SystemExit(f"标定语料不足（{len(rows)} 条，建议 >= 200 条人类文本）")

    vals = {k: [] for k in ("sentence_cv", "ngram_repeat_3", "transition_density",
                            "dash_colon_density", "lexical_diversity")}
    for t in rows:
        m = profile(t)
        for k in vals:
            if m.get(k) is not None:
                vals[k].append(m[k])

    def q(a, p):
        a = sorted(a)
        return a[min(len(a) - 1, max(0, int(p * (len(a) - 1))))]

    out = {}
    for k, a in vals.items():
        if not a:
            continue
        if THRESHOLDS[k].get("direction") in ("lower_is_more_regular", "lower"):
            out[k] = {"low": round(q(a, lo_pct), 4),
                      "very_low": round(q(a, lo_pct / 3), 4),
                      "direction": THRESHOLDS[k]["direction"]}
        else:
            out[k] = {"high": round(q(a, hi_pct), 4),
                      "very_high": round(q(a, min(0.99, hi_pct + (1 - hi_pct) / 2)), 4),
                      "direction": THRESHOLDS[k]["direction"]}
    return {"n_calib": len(rows), "thresholds": out}


def main():
    ap = argparse.ArgumentParser(description="文风规整度指标（非检测器分数）")
    ap.add_argument("path", nargs="?", help="待分析文本文件")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--calibrate", nargs="+", metavar="JSONL",
                    help="在这些人类语料 JSONL 上重新标定阈值（换领域时必需）")
    args = ap.parse_args()

    if args.calibrate:
        res = calibrate(args.calibrate)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        print(f"\n已用 {res['n_calib']} 条人类文本重新标定。")
        print("把上面的 thresholds 覆盖到 THRESHOLDS 常量，并更新 CALIBRATION.source。")
        print("注意：请用独立留出样本测量组合触发率；分位数标定不保证误标率。")
        return 0

    if not args.path:
        ap.error("需要给出文本路径，或使用 --calibrate")
    if not os.path.exists(args.path):
        raise SystemExit(f"文件不存在：{args.path}")
    with open(args.path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    prof = profile(text)
    if args.json:
        print(json.dumps(prof, ensure_ascii=False, indent=2))
    else:
        print(render(text, prof))
    return 0


if __name__ == "__main__":
    sys.exit(main())

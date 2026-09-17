#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIGC 误判风险体检器 —— 纯本地启发式文本体检。

用途
    对中文学术文本做统计学体检，输出"疑似机器写作特征"的位置与证据，
    供人工判断和改写。不连接任何检测平台，不输出"AI 率"。

重要声明
    本脚本计算的是**写作质量与机器指纹特征**，不是任何平台的检测分数。
    脚本输出低风险 ≠ 平台会判低，输出高风险 ≠ 平台会判高。
    各平台阈值、分块规则、模型版本均为非公开内部参数，且会随版本变化。

用法
    python3 diagnose.py 论文.md
    python3 diagnose.py 论文.docx          # 自动调用 textutil/pandoc 提取文本
    python3 diagnose.py 论文.txt --json    # 机器可读输出
    python3 diagnose.py 论文.txt --min-sent 8   # 只报句长 >= 8 字且过长的句子

无第三方依赖，Python 3.8+。
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys

# ---------------------------------------------------------------- 常量表

# 话语连接词 / 框架词。诊断器只统计"覆盖段落的比例"，
# 因为问题不在于出现，而在于几乎每段都靠它们开头。
CONNECTIVES = [
    "综上所述", "总而言之", "总的来说", "由此可见", "不难看出", "值得注意的是",
    "需要指出的是", "不言而喻", "众所周知", "换言之", "简而言之", "一言以蔽之",
    "首先", "其次", "再次", "最后", "此外", "另外", "同时", "然而", "因此",
    "所以", "并且", "而且", "总之", "一方面", "另一方面",
]

# 万能开头句：把主语换成任何题目都成立 —— 这是最典型的空心化标志
BOILERPLATE_OPENERS = [
    "随着", "在当今", "在当今社会", "近年来", "在信息化", "在数字化",
    "伴随着", "随着我国", "在新时代", "进入21世纪", "在全球化",
]

# 商业/互联网套话，侵入学术写作后是强机器信号
BUZZWORDS = [
    "赋能", "抓手", "闭环", "生态", "生态圈", "链路", "打法", "颗粒度",
    "对齐", "痛点", "颠覆", "重塑", "范式", "底层逻辑", "顶层设计",
    "深度融合", "全方位", "多维度", "立体化", "一体化", "高质量发展",
    "保驾护航", "添砖加瓦", "注入新动能", "新引擎", "新篇章",
]

# 空泛指代：读者无法据此核查，等价于没有信息
VAGUE_REFERENCE = [
    "某高校", "某企业", "某公司", "某个", "一些学者", "有学者", "有研究",
    "相关研究", "大量研究", "国内外学者", "许多研究", "部分研究",
    "相关人员", "有关部门", "业内人士", "相关领域",
]

# 对称并列表述：成对出现且语义重复
PAIRED_SYMMETRY = [
    "不仅", "而且", "既", "又", "一方面", "另一方面", "不仅能", "还能",
    "既要", "也要", "既是", "也是",
]

# 作者在场标记：本人立场、方法选择、真实困难。缺失是重要负面信号
AUTHORIAL_MARKERS = [
    "本文认为", "笔者认为", "本研究", "本文尝试", "本文选择", "本文采用",
    "限于", "受限于", "不足之处", "尚需", "有待", "未能", "无法排除",
    "需要注意的是", "出乎意料", "与预期不符", "未能验证", "存在争议",
    "本文不打算", "本文无意", "需要说明的是", "必须承认",
]

# 具体性锚点：数字 / 年份 / 引用 / 单位 / 专有名词密度
RE_NUMERIC = re.compile(r"\d+(?:\.\d+)?\s*(?:%|‰|％|万|亿|元|人|年|月|日|倍|个|次|篇|项|台|例|组|分|秒|小时|公里|km|kg|ml|℃|个百分点)?")
RE_CITATION = re.compile(r"[（(]\s*[A-Z][A-Za-z\-]+\s*,?\s*(?:19|20)\d{2}[a-z]?\s*[)）]|\[\d+(?:[-,]\d+)*\]|【\d+】")
RE_QUOTED = re.compile(r"[“\"]([^”\"]{4,60})[”\"]")
RE_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-\.]{1,}")
RE_PARA_SPLIT = re.compile(r"\n\s*\n+")
# 中文字符计数（排除标点空白）
RE_CJK = re.compile(r"[\u4e00-\u9fff]")

# markdown 残留 / 格式伪影
FORMAT_ARTIFACTS = [
    (re.compile(r"^\s{0,3}#{1,6}\s", re.M), "markdown 标题标记"),
    (re.compile(r"\*\*[^*\n]+\*\*"), "markdown 粗体标记"),
    (re.compile(r"^\s*[-*+]\s+", re.M), "markdown 列表标记"),
    (re.compile(r"\|\s*[-:]+\s*\|"), "markdown 表格分隔行"),
    (re.compile(r"^\s*>\s", re.M), "markdown 引用块"),
    (re.compile(r"^\s*```", re.M), "代码围栏"),
    (re.compile(r"\u200b|\u200c|\u200d|\ufeff"), "零宽字符"),
    (re.compile(r"[\uE000-\uF8FF]"), "私用区字符"),
]

# 占位符残留 —— 这是最强的"AI 生成未清理"证据
PLACEHOLDERS = [
    (re.compile(r"\[(?:待补|待填|请填|插入|此处|TODO|TBD|待定|XX|某某)[^\]]*\]", re.I), "方括号占位符"),
    (re.compile(r"(?:XX|某某)(?:大学|学院|公司|企业|机构|教授|老师)"), "XX/某某占位"),
    (re.compile(r"(?:待补|待填|TODO|TBD|此处省略)"), "待补标记"),
    (re.compile(r"(?:20\d{2}|19\d{2})\s*年\s*(?:X|某|xx)月"), "占位日期"),
]

# 破折号 / 括号 / 引号 —— 人类学术写作里的插入语痕迹
RE_INSERTION = re.compile(r"[——]|（[^）]{1,40}）|\([^)]{1,40}\)")


def cjk_len(text):
    return len(RE_CJK.findall(text))


def split_sentences(paragraph):
    """按中文句末标点切句。"""
    parts = re.split(r"(?<=[。！？!?；;])", paragraph)
    return [p.strip() for p in parts if cjk_len(p.strip()) > 0]


def hhi(counts):
    """赫芬达尔指数：0 = 完全分散，1 = 完全集中。"""
    total = sum(counts)
    if total == 0:
        return 0.0
    return sum((c / total) ** 2 for c in counts)


def extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md", ".markdown"):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), "plain"
    if ext in (".docx", ".doc"):
        for cmd in (["textutil", "-convert", "txt", "-stdout", path],
                    ["pandoc", "-t", "plain", path]):
            try:
                out = subprocess.run(cmd, capture_output=True, check=True)
                return out.stdout.decode("utf-8", errors="replace"), cmd[0]
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        raise SystemExit(
            "无法提取 .docx/.doc 文本：需要 macOS 的 textutil 或已安装 pandoc。\n"
            "替代办法：请在 Word 中另存为 .txt，或直接把正文粘贴给我。"
        )
    raise SystemExit("暂不支持该扩展名：%s（支持 .txt/.md/.docx/.doc）" % ext)


def analyze(text, min_sent=1):
    paragraphs = []
    for block in RE_PARA_SPLIT.split(text):
        stripped = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if cjk_len(stripped) >= 20:  # 过滤标题、页眉、参考文献碎片
            paragraphs.append(stripped)

    all_sentences = []
    for idx, para in enumerate(paragraphs, 1):
        for sent in split_sentences(para):
            all_sentences.append({"para": idx, "text": sent, "len": cjk_len(sent)})

    total_cjk = cjk_len(text)
    report = {
        "file_chars_cjk": total_cjk,
        "paragraph_count": len(paragraphs),
        "sentence_count": len(all_sentences),
        "warnings": [],
        "findings": {},
    }

    if len(paragraphs) < 5 or len(all_sentences) < 25:
        report["warnings"].append(
            "样本过小（%d 段 / %d 句）：统计指标不稳定，请只把结果当作参考，"
            "并优先按 references/fingerprints.md 逐段人工复查。"
            % (len(paragraphs), len(all_sentences))
        )

    # ---------- 1. 句长分布 ----------
    lens = [s["len"] for s in all_sentences]
    if len(lens) >= 5:
        mean = statistics.mean(lens)
        try:
            stdev = statistics.stdev(lens)
        except statistics.StatisticsError:
            stdev = 0.0
        cv = stdev / mean if mean else 0.0
        buckets = [0, 15, 30, 50, 80, 10 ** 9]
        bucket_counts = []
        for lo, hi in zip(buckets, buckets[1:]):
            bucket_counts.append(sum(1 for L in lens if lo <= L < hi))
        report["findings"]["sentence_length"] = {
            "mean": round(mean, 1),
            "stdev": round(stdev, 1),
            "cv": round(cv, 3),
            "min": min(lens),
            "max": max(lens),
            "bucket_0_15": bucket_counts[0],
            "bucket_15_30": bucket_counts[1],
            "bucket_30_50": bucket_counts[2],
            "bucket_50_80": bucket_counts[3],
            "bucket_80_plus": bucket_counts[4],
            "longest": sorted(
                [s for s in all_sentences if s["len"] >= 60 and s["len"] >= min_sent],
                key=lambda s: -s["len"],
            )[:8],
        }

    # ---------- 2. 段落长度节奏 ----------
    plens = [cjk_len(p) for p in paragraphs]
    if len(plens) >= 4:
        pm = statistics.mean(plens)
        try:
            psd = statistics.stdev(plens)
        except statistics.StatisticsError:
            psd = 0.0
        report["findings"]["paragraph_rhythm"] = {
            "mean": round(pm, 1),
            "stdev": round(psd, 1),
            "cv": round(psd / pm, 3) if pm else 0.0,
            "min": min(plens),
            "max": max(plens),
        }

    # ---------- 3. 连接词段落覆盖率 ----------
    if paragraphs:
        hit_paras = {}
        for name in CONNECTIVES:
            paras = [i for i, p in enumerate(paragraphs, 1) if name in p]
            if paras:
                hit_paras[name] = paras
        covered = set()
        for paras in hit_paras.values():
            covered.update(paras)
        counts = sorted(hit_paras.items(), key=lambda kv: -len(kv[1]))
        report["findings"]["connectives"] = {
            "coverage_ratio": round(len(covered) / len(paragraphs), 3),
            "distinct_count": len(hit_paras),
            "hhi": round(hhi([len(v) for v in hit_paras.values()]), 4) if hit_paras else 0.0,
            "top": [{"word": w, "paragraphs": v} for w, v in counts[:12]],
        }

    # ---------- 4. 空泛指代 ----------
    vague_hits = []
    for name in VAGUE_REFERENCE:
        count = text.count(name)
        if count:
            vague_hits.append({"term": name, "count": count})
    vague_hits.sort(key=lambda d: -d["count"])
    vague_total = sum(d["count"] for d in vague_hits)
    report["findings"]["vague_reference"] = {
        "total": vague_total,
        "per_1000_cjk": round(vague_total / max(total_cjk, 1) * 1000, 2),
        "hits": vague_hits,
    }

    # ---------- 5. 套话与万能开头 ----------
    def term_hits(terms):
        out = []
        for t in terms:
            c = text.count(t)
            if c:
                out.append({"term": t, "count": c})
        return sorted(out, key=lambda d: -d["count"])

    boiler = term_hits(BOILERPLATE_OPENERS)
    buzz = term_hits(BUZZWORDS)
    buzz_total = sum(d["count"] for d in buzz)
    report["findings"]["boilerplate"] = {
        "hits": boiler, "total": sum(d["count"] for d in boiler)}
    report["findings"]["buzzwords"] = {
        "hits": buzz,
        "total": buzz_total,
        "per_1000_cjk": round(buzz_total / max(total_cjk, 1) * 1000, 2),
    }

    # ---------- 6. 作者在场标记密度 ----------
    auth = term_hits(AUTHORIAL_MARKERS)
    auth_total = sum(d["count"] for d in auth)
    report["findings"]["authorial_presence"] = {
        "hits": auth,
        "total": auth_total,
        "per_1000_cjk": round(auth_total / max(total_cjk, 1) * 1000, 2),
    }

    # ---------- 7. 具体性锚点密度 ----------
    numeric = RE_NUMERIC.findall(text)
    citations = RE_CITATION.findall(text)
    quotes = RE_QUOTED.findall(text)
    insertions = RE_INSERTION.findall(text)
    latin = RE_LATIN_TOKEN.findall(text)
    report["findings"]["specificity"] = {
        "numeric_tokens": len(numeric),
        "numeric_per_1000_cjk": round(len(numeric) / max(total_cjk, 1) * 1000, 2),
        "citations": len(citations),
        "citation_per_1000_cjk": round(len(citations) / max(total_cjk, 1) * 1000, 2),
        "direct_quotes": len(quotes),
        "parenthetical_insertions": len(insertions),
        "insertion_per_1000_cjk": round(len(insertions) / max(total_cjk, 1) * 1000, 2),
        "latin_tokens": len(latin),
    }

    # ---------- 8. 格式伪影与占位符 ----------
    artifacts = []
    for pattern, label in FORMAT_ARTIFACTS:
        n = len(pattern.findall(text))
        if n:
            artifacts.append({"label": label, "count": n})
    placeholders = []
    for pattern, label in PLACEHOLDERS:
        matches = pattern.findall(text)
        if matches:
            sample = matches[:6] if isinstance(matches[0], str) else [str(m) for m in matches[:6]]
            placeholders.append({"label": label, "count": len(matches), "samples": sample})
    report["findings"]["format_artifacts"] = {"items": artifacts}
    report["findings"]["placeholders"] = {"items": placeholders}

    # ---------- 9. 句首重复 ----------
    openers = {}
    for s in all_sentences:
        head = s["text"][:6]
        if cjk_len(head) >= 3:
            openers.setdefault(head, []).append(s["para"])
    repeated = sorted(
        [(k, v) for k, v in openers.items() if len(v) >= 3],
        key=lambda kv: -len(kv[1]),
    )[:10]
    report["findings"]["repeated_openers"] = {
        "top": [{"opener": k, "count": len(v), "paragraphs": v} for k, v in repeated]
    }

    # ---------- 10. 对称并列 ----------
    paired = term_hits(PAIRED_SYMMETRY)
    paired_total = sum(d["count"] for d in paired)
    report["findings"]["paired_symmetry"] = {
        "hits": paired,
        "total": paired_total,
        "per_1000_cjk": round(paired_total / max(total_cjk, 1) * 1000, 2),
    }

    return report


# ---------------------------------------------------------------- 渲染

def band(value, warn, high, reverse=False):
    """reverse=False: 越大越糟；reverse=True: 越小越糟。"""
    if reverse:
        if value <= high:
            return "高"
        if value <= warn:
            return "中"
        return "低"
    if value >= high:
        return "高"
    if value >= warn:
        return "中"
    return "低"


def render(report):
    f = report["findings"]
    lines = []
    add = lines.append
    add("=" * 68)
    add("AIGC 误判风险体检报告")
    add("=" * 68)
    add("正文中文字数 %d ｜ 段落 %d ｜ 句子 %d" % (
        report["file_chars_cjk"], report["paragraph_count"], report["sentence_count"]))
    add("")
    add("!! 本报告是写作统计特征的体检，不是任何平台的检测分数。")
    add("!! 低风险不等于平台判低，高风险不等于平台判高。各平台阈值非公开。")
    add("")

    for w in report["warnings"]:
        add("[样本提示] " + w)
    if report["warnings"]:
        add("")

    def risk_row(name, level, key_figures, action):
        add("┌ %-14s 风险: %s" % (name, level))
        add("│ 关键数字: " + key_figures)
        add("└ 建议动作: " + action)
        add("")

    sl = f.get("sentence_length")
    if sl:
        lvl = band(sl["cv"], 0.45, 0.30, reverse=True)
        risk_row(
            "句长节奏", lvl,
            "均值 %.1f 字，标准差 %.1f，变异系数 %.3f（短句≤15字 %d 句，长句≥80字 %d 句）" % (
                sl["mean"], sl["stdev"], sl["cv"], sl["bucket_0_15"], sl["bucket_80_plus"]),
            "变异系数越低＝句长越均匀＝机器味越重。人工改法：每 3-4 句插入一个 8-15 字的短句；"
            "把超过 80 字的句子拆开，或把两个短句合成一个长句。" if lvl != "低" else "节奏尚可，保持长短交替。",
        )

    pr = f.get("paragraph_rhythm")
    if pr:
        lvl = band(pr["cv"], 0.45, 0.28, reverse=True)
        risk_row(
            "段落节奏", lvl,
            "均值 %.1f 字，标准差 %.1f，变异系数 %.3f（最短 %d / 最长 %d）" % (
                pr["mean"], pr["stdev"], pr["cv"], pr["min"], pr["max"]),
            "段落长度过于整齐是结构僵化标志。人工改法：让某些段落只有 1-2 句（判断句、过渡句），"
            "某些段落展开到 8-10 句（案例分析、数据描述）。" if lvl != "低" else "段落长度有自然起伏。",
        )

    cn = f.get("connectives")
    if cn:
        lvl = band(cn["coverage_ratio"], 0.55, 0.75)
        top = "、".join("%s(%d段)" % (d["word"], len(d["paragraphs"])) for d in cn["top"][:8])
        risk_row(
            "连接词模板化", lvl,
            "覆盖 %.0f%% 的段落，出现 %d 种连接词。高频：%s" % (
                cn["coverage_ratio"] * 100, cn["distinct_count"], top or "无"),
            "连接词覆盖过半段落＝论证靠路标词而非逻辑衔接。人工改法：删掉一半连接词，"
            "改写为内容本身的因果/转折（把\"因此\"换成前句结果的直接陈述）。" if lvl != "低" else "连接词使用克制。",
        )

    vr = f.get("vague_reference")
    if vr:
        lvl = band(vr["per_1000_cjk"], 2.0, 4.0)
        top = "、".join("%s×%d" % (d["term"], d["count"]) for d in vr["hits"][:8])
        risk_row(
            "空泛指代", lvl,
            "共 %d 处（每千字 %.2f）。%s" % (vr["total"], vr["per_1000_cjk"], top or "无"),
            "把每一处换成可核查的具体对象：机构全名、年份、样本量、文献作者。"
            "查不出来的，说明这部分你本来就没读原文 —— 那才是真问题。" if lvl != "低" else "指代具体。",
        )

    bp = f.get("boilerplate")
    if bp:
        lvl = band(bp["total"], 3, 6)
        top = "、".join("%s×%d" % (d["term"], d["count"]) for d in bp["hits"][:8])
        risk_row(
            "万能开头句", lvl,
            "共 %d 处。%s" % (bp["total"], top or "无"),
            "\"随着……的发展\"这类句子把主语换成任何题目都成立，等于零信息。"
            "改法：第一句直接给本文要处理的具体矛盾或数据。" if lvl != "低" else "开头句有实质内容。",
        )

    bz = f.get("buzzwords")
    if bz:
        lvl = band(bz["per_1000_cjk"], 1.0, 3.0)
        top = "、".join("%s×%d" % (d["term"], d["count"]) for d in bz["hits"][:10])
        risk_row(
            "商业/互联网套话", lvl,
            "共 %d 处（每千字 %.2f）。%s" % (bz["total"], bz["per_1000_cjk"], top or "无"),
            "学术写作中\"赋能/闭环/抓手/生态\"属于外来套话，是强机器信号。"
            "逐个换成学科内既有的准确术语。" if lvl != "低" else "术语使用规范。",
        )

    au = f.get("authorial_presence")
    if au:
        lvl = band(au["per_1000_cjk"], 1.5, 0.8, reverse=True)
        top = "、".join("%s×%d" % (d["term"], d["count"]) for d in au["hits"][:10])
        risk_row(
            "作者在场标记", lvl,
            "共 %d 处（每千字 %.2f）。%s" % (au["total"], au["per_1000_cjk"], top or "无"),
            "缺失作者立场是重要的负面信号：全文没有一句是你自己的判断。"
            "人工改法：至少补 5 处\"本文认为/限于数据/未能排除\"这类真实的第一人称判断与自我设限。",
        )

    sp = f.get("specificity")
    if sp:
        lvl = band(sp["numeric_per_1000_cjk"], 3.0, 1.2, reverse=True)
        risk_row(
            "具体性锚点", lvl,
            "数字/量词 %d（每千字 %.2f）｜引用 %d（每千字 %.2f）｜直接引语 %d｜括号插入语 %d" % (
                sp["numeric_tokens"], sp["numeric_per_1000_cjk"],
                sp["citations"], sp["citation_per_1000_cjk"],
                sp["direct_quotes"], sp["parenthetical_insertions"]),
            "这是最重要的一项：具体数字、真实引文、括号补充说明是最难被统计模型预测的内容，"
            "也是最有效的改写方向。优先补真实数据、文献页码、样本量、时间点。" if lvl != "低" else "具体性锚点充足。",
        )

    ps = f.get("paired_symmetry")
    if ps:
        lvl = band(ps["per_1000_cjk"], 3.0, 6.0)
        risk_row(
            "对称并列滥用", lvl,
            "共 %d 处（每千字 %.2f）" % (ps["total"], ps["per_1000_cjk"]),
            "\"不仅……而且……\"\"既要……也要……\"成串出现是典型机器句式。"
            "拆掉对称结构，改成有主次的论述：先给主要结论，再补限定条件。" if lvl != "低" else "并列结构使用适度。",
        )

    ro = f.get("repeated_openers")
    if ro and ro["top"]:
        lvl = band(ro["top"][0]["count"], 4, 7)
        top = "、".join("\"%s\"×%d" % (d["opener"], d["count"]) for d in ro["top"][:8])
        risk_row(
            "句首重复", lvl,
            "最高频句首：%s" % top,
            "同一句式反复起头会显著抬高文本可预测性。改法：换句式（陈述/设问/让步/引证交替）。"
            if lvl != "低" else "句首多样。",
        )

    fa = f.get("format_artifacts")
    if fa and fa["items"]:
        add("┌ 格式伪影  风险: 高")
        add("│ " + "、".join("%s×%d" % (d["label"], d["count"]) for d in fa["items"]))
        add("└ 建议动作: 这是最直接的\"未经人工处理\"证据，必须在定稿前清除。")
        add("")

    ph = f.get("placeholders")
    if ph and ph["items"]:
        add("┌ 占位符残留  风险: 高（最强证据）")
        for d in ph["items"]:
            add("│ %s×%d  例：%s" % (d["label"], d["count"], "、".join(d["samples"])))
        add("└ 建议动作: 逐处补齐真实信息或删段。占位符留在送检稿里，是最容易被判定为"
            "\"AI 生成未清理\"的直接证据。")
        add("")

    longest = (sl or {}).get("longest") or []
    if longest:
        add("-" * 68)
        add("最长的句子（优先拆分）：")
        for s in longest:
            add("  [第%d段 · %d字] %s" % (s["para"], s["len"], s["text"][:120]))
        add("")

    add("=" * 68)
    add("下一步：按 references/rewrite-rules.md 的优先级动手改，")
    add("改完用 references/appeal.md 做自证存证，再送检。")
    add("=" * 68)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="中文学术文本 AIGC 误判风险体检（纯本地启发式，不连接检测平台）")
    ap.add_argument("path", help="文本文件路径（.txt/.md/.docx/.doc）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--min-sent", type=int, default=1, help="长句清单的最小字数阈值")
    args = ap.parse_args()

    if not os.path.exists(args.path):
        raise SystemExit("文件不存在：%s" % args.path)

    text, how = extract_text(args.path)
    report = analyze(text, min_sent=args.min_sent)
    report["extracted_by"] = how
    report["source"] = os.path.basename(args.path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

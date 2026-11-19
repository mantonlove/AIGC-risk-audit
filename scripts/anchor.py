#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定位缺少出处或取值的表述，比较修订前后的表面具体性线索。

本脚本按规则找出可能需要核查的句子，列出原文片段、疑点和修订方向；
也统计数字、术语等线索的密度。这些线索不自动证明事实真实，也不是
AIGC 检测分数或商业平台结果。

历史 A 条件实验（见 references/edit-effects.md）从人类摘要抽取数字和术语，
统一附在 AI 摘要末尾。三个本地开源检测器上的平均分数下降，但逐篇仍有
5/120、12/120、21/120 篇升分；该操作没有验证作者补写真正论据的效果。
因此本脚本用于写作质量核查，不预测改后检测分数。

只根据作者已有材料补充可核查信息；查不到依据时删去或缩小主张。

用法
----
    python3 scripts/anchor.py 论文.txt                    # 列出待锚定位置
    python3 scripts/anchor.py 论文.txt --json > 锚定.json  # 机器可读
    python3 scripts/anchor.py 改前.txt --compare 改后.txt   # 比较表面指标

两类检查分开报：①需要补出处或取值的空洞指代；②分句以谓语动词开头、缺主语的
写法（"…传播基地。推进了西北地区维新运动。"）。第二类只做分句开头的前缀匹配，
不做句法分析，判不了"速度缓慢"这类名词+形容词残句，也判不了主语谓语不一致。
"""

import argparse
import json
import os
import re
import sys

# --------------------------------------------------------------------------- #
#  空洞指代模式：每条都给出**为什么空**与**要补什么**
#
#  范围：只收录可通过补出处、取值或缩小主张来处理的模式。
#  纯文风问题（如连接词多）不在这里；此工具不估计改写的检测效果。
# --------------------------------------------------------------------------- #

PATTERNS = [
    {
        "id": "anonymous_institution",
        "name": "匿名机构",
        "re": re.compile(r"(?:某|有一?[个些位家]|相关|有关)(?:高校|大学|院校|机构|部门|"
                         r"公司|企业|医院|银行|券商|基金管理人|监管部门|研究|学者|专家|"
                         r"业内人士|调查|研究)"),
        "why": "读者无法核查这个主体是谁 —— 无法复核的论据等于没有论据。",
        "fix": "换成真实机构名（含层级，如「某某大学商学院」），或删掉该论据。",
    },
    {
        "id": "vague_quantity",
        "name": "无取值的量",
        "re": re.compile(r"(?:大量|许多|很多|不少|一些|部分|若干|少数|多数|大多数|"
                         r"极少数|少量|众多的?|部分的?)(?![\u4e00-\u9fff]{0,2}(?:数据|文献|研究))"
                         r"[\u4e00-\u9fff]{0,4}"),
        "why": "没有取值就无法判断量级，也无法复算。",
        "fix": "补真实数字（样本量/占比/年份区间），或改成定性表述并说明为何无法量化。",
    },
    {
        "id": "unsourced_claim",
        "name": "无出处的断言",
        "re": re.compile(r"(?:研究表明|研究发现|已有研究|文献(?:表明|指出)|据(?:报道|统计|了解)|"
                         r"调查(?:显示|表明|发现)|数据显示|实践(?:表明|证明)|经验(?:表明|证明))"),
        "guard": "cited_in_sentence",
        "why": "断言了「有研究支持」但没给可查的出处 —— 这是最容易被审稿人抓的一类空论据。",
        "fix": "补「作者(年份)」或「机构+年份+报告名」；查不到就降级为「本文未能找到直接实证」。",
    },
    {
        "id": "no_sample_size",
        "name": "有结论无样本",
        "re": re.compile(r"(?:样本|数据|问卷|访谈|案例|实验)(?:量|数)?(?:为|是)?\s*"
                         r"(?:若干|一定|部分|充足|较大|较小|有限的?)"),
        "why": "样本信息是复算的前提，用形容词代替数字会让结论无法评估。",
        "fix": "写真实样本量与口径（如 n=1,234，2020–2023 年）。",
    },
    {
        "id": "empty_framing",
        "name": "空心框定句",
        "re": re.compile(r"(?:具有重要的?(?:理论|现实|实践)?(?:意义|价值|作用)|"
                         r"提供了?(?:重要|有益)的?(?:参考|借鉴|启示|思路)|"
                         r"具有广阔的应用前景|值得(?:进一步)?(?:深入)?(?:研究|探讨|思考)|"
                         r"随着[^。；]{2,20}的(?:不断)?(?:发展|深入|推进)[，,])"),
        "why": "这类句子不承载信息，删掉不损失内容；留着会稀释具体信息的密度。",
        "fix": "删掉，或替换成该处真正的结论/限定条件。",
    },
    {
        "id": "vague_scope",
        "name": "范围不明的全称",
        # 否定前瞻：紧跟数字（含「27 个」「4,215 家」）说明**有分母**，可证伪，不算空洞。
        # 这条是回归测试抓出来的：没有它，「在全部 27 个样本中…」会被误报。
        "re": re.compile(r"(?:所有|全部|任何|每一个|一切|无一例外)"
                         r"(?!\s*[\d０-９,，]+\s*(?:个|家|只|篇|位|种|项|年|条|次|%|％|‰)?)"
                         r"[\u4e00-\u9fff]{0,6}(?:都|均|皆)?"),
        "why": "全称量词若无分母（命中数/总数），是不可证伪的断言。",
        "fix": "补「N/M」或改成「在本样本中…」。",
    },
    {
        "id": "hedge_without_basis",
        "name": "无依据的缓和",
        "re": re.compile(r"(?:或许|可能|大概|似乎|似乎可能|有一定的|在一定程度上)(?:会|能够|可以|是|存在)?"),
        "why": "缓和词若没有对应的不确定性来源（样本、口径、区间），只是回避判断。",
        "fix": "说明不确定性来自哪里（如「受限于 n=12，区间较宽」），否则删掉缓和词直接陈述。",
    },
]

# --------------------------------------------------------------------------- #
#  主语缺失（"每句话讲清楚谁在做什么"里可测的那一半）
#
#  只覆盖**分句开头**的谓语动词 —— "…传播基地。推进了西北地区维新运动。"这类
#  承接上句时把主语一起省掉的写法。中文合法地允许无主句（祈使、介词短语打头、
#  承接上文），所以守卫比命中更重要：宁可漏报，不要误报。
#
#  不做句法分析，因此**不判**"速度缓慢"这类名词+形容词的残句，也不判主语与
#  谓语数不一致、动词搭配错误。命中只说明这一处值得人工看一眼。
# --------------------------------------------------------------------------- #

# 只收在分句开头基本只能是谓语、且缺主语就明显不通的词。
# 刻意排除「研究/分析/影响/反映/存在/支持/处理/形成/产生/包括/具有」这类
# 名动兼类词 —— 它们出现在分句开头时更常是名词（"研究结果表明…"）。
PRED_VERBS = (
    "提出", "定义", "给出", "构建", "建立", "实现", "采用", "运用", "推进", "促进",
    "提高", "降低", "增强", "得出", "得到", "证明", "发现", "指出", "认为", "表明",
    "显示", "强调", "主张", "阐发", "显发", "阐述", "论述", "论证", "归纳", "梳理",
    "总结", "回顾", "引入", "设计", "开发", "拓展", "深化", "丰富", "完善", "优化",
    "改进", "提升", "加强", "保障", "维护", "支撑", "驱动", "引领", "带动", "制约",
    "限制", "阻碍", "削弱", "损害", "危及", "缓解", "消除", "避免", "防止", "确保",
    "保证", "满足", "适应", "符合", "违背", "突破", "超越", "承担", "履行", "借鉴",
    "沿用", "修正", "更正", "补充", "拆分", "合并", "测度", "刻画", "验证",
)
PRED_VERBS = tuple(sorted(PRED_VERBS, key=len, reverse=True))

# 分句开头可以合法省略主语的副词/连接成分，先剥掉再判断。
LEAD_SKIP = tuple(sorted((
    "并且", "而且", "此外", "另外", "同时", "因此", "从而", "进而", "然后", "最后",
    "首先", "其次", "再次", "然而", "但是", "不过", "总之", "综上", "可见",
    "由此可见", "值得注意", "具体而言", "换言之", "也就是说", "事实上", "实际上",
    "目前", "近年来", "当前", "以往", "但", "而", "并", "也", "还", "又", "更",
    "都", "均", "再", "则", "却", "即", "就", "才", "已", "正", "将", "可", "能",
    "会", "应", "须", "必须", "应当", "需要", "进一步", "故", "于是",
), key=len, reverse=True))

# 分句以这些成分开头时，是状语/介词短语，主语可以在后文或整句层面出现。
PREP_HEAD = tuple(sorted((
    "通过", "随着", "基于", "针对", "根据", "结合", "围绕", "鉴于", "为了", "由于",
    "除了", "经过", "依照", "按照", "依据", "凭借", "借助", "利用", "面向", "关于",
    "对于", "在", "对", "由", "自", "从", "以", "把", "被", "给", "据", "沿", "向",
    "往", "因", "为", "与", "和", "同", "跟",
), key=len, reverse=True))

# 谓语动词后接这些词时，整段其实是名词短语（"设计过程""优化方案"），不是谓语。
NOUN_TAIL = tuple(sorted((
    "过程", "方法", "方案", "结果", "工作", "阶段", "要求", "规范", "标准", "原则",
    "思路", "理念", "目标", "任务", "结构", "参数", "变量", "指标", "模型", "系统",
    "流程", "步骤", "内容", "部分", "问题", "方面", "环节", "机制", "模式", "框架",
    "体系", "特征", "规律", "趋势", "能力", "水平", "程度", "范围", "条件", "依据",
    "基础", "背景", "意义", "价值", "作用", "局限", "不足", "展望", "者", "性",
    "化", "度", "量", "率",
), key=len, reverse=True))

_QUOTE_PAIRS = (("“", "”"), ("「", "」"), ("『", "』"), ("《", "》"),
                ("（", "）"), ("(", ")"), ("\"", "\""), ("'", "'"))
_BOUND = set("，,。！？；;\n")
CLAUSE_MIN = 4


def _mask_quoted(text: str) -> str:
    """把引号/括号内的字符换成占位符，避免在引文内部断句。"""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        paired = False
        for lo, hi in _QUOTE_PAIRS:
            if text.startswith(lo, i):
                j = text.find(hi, i + len(lo))
                if j != -1:
                    for k in range(i + len(lo), j):
                        out[k] = "□"
                    i = j + len(hi)
                    paired = True
                    break
        if not paired:
            i += 1
    return "".join(out)


def _strip_lead(seg: str) -> int:
    """剥掉分句开头的标点与副词/连接成分，返回内容起点。"""
    i, moved = 0, True
    while moved:
        moved = False
        while i < len(seg) and seg[i] in " \t，,。；;：:":
            i += 1
            moved = True
        for w in LEAD_SKIP:
            if seg.startswith(w, i):
                i += len(w)
                moved = True
                break
    return i


def _check_clause(raw: str, seg: str, base: int, prev_prep: bool):
    i = _strip_lead(seg)
    rest = seg[i:]
    if cjk_len(rest) < CLAUSE_MIN:
        return None
    if any(rest.startswith(w) for w in PREP_HEAD):
        return None
    verb = next((w for w in PRED_VERBS if rest.startswith(w)), None)
    if not verb:
        return None
    after = rest[len(verb):]
    if not after or after.startswith("的"):
        return None          # "提出的方法…" 是定语，不是谓语
    if any(after.startswith(t) for t in NOUN_TAIL):
        return None          # "设计过程…" 是名词短语
    return {
        "pattern": "subjectless_clause",
        "name": "分句缺主语",
        "matched": verb,
        "quote": raw.strip()[:80],
        "unit_start": base,
        "offset": base + i,
        "soft": prev_prep,
        "why": ("该分句以谓语动词「%s」开头，前面没有出现施动者；"
                "读者要靠上句猜是谁做的。" % verb),
        "fix": ("若该分句的施动者与上句不同，补回主语（谁提出/谁验证）；"
                "否则并回上句作宾语或定语。摘要体裁里承接上句的省略是常规写法，"
                "不必逐句补。"),
    }


def scan_subjectless(text: str):
    """返回分句缺主语的命中列表。`soft=True` 表示上一分句是状语（通过/基于…），
    属中文可接受的无主句，只提示不计数为主指标。"""
    masked = _mask_quoted(text)
    hits, start, prev_prep = [], 0, False
    for i in range(len(masked) + 1):
        if i < len(masked) and masked[i] not in _BOUND:
            continue
        raw, seg = text[start:i], masked[start:i]
        cur_prep = False
        if cjk_len(seg) >= CLAUSE_MIN:
            k = _strip_lead(seg)
            if k < len(seg):
                cur_prep = any(seg[k:].startswith(w) for w in PREP_HEAD)
            h = _check_clause(raw, seg, start, prev_prep)
            if h:
                hits.append(h)
        boundary = masked[i] if i < len(masked) else "\n"
        prev_prep = cur_prep if boundary in "，," else False
        start = i + 1
    return hits


# 表面具体性线索：命中仅提示可以进一步核查，不证明信息属实。
ANCHOR_MARKERS = [
    re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|个|种|项|倍|年|月|日|万元|亿元|‰)"),
    re.compile(r"[（(]\s*[A-Za-z\u4e00-\u9fff]{2,20}\s*[,，]\s*(?:19|20)\d{2}\s*[)）]"),
    re.compile(r"(?:19|20)\d{2}\s*[—\-–~至]\s*(?:19|20)?\d{2,4}"),
    re.compile(r"\bn\s*=\s*\d+", re.I),
    re.compile(r"第\s*\d+\s*[章节表图]"),
    re.compile(r"(?:附录|附表|表|图)\s*[A-Z]?\d+"),
]


# 粗筛用出处模式；命中不等于已经核实来源。
# 必要性（回归测试抓出）：`北京大学 2023 年对 1,204 名学生的调查显示…` ——
# 出处**在断言词之后**，旧实现只看后面 12 字，于是把合格写法误报为"无出处"。
CITATION = re.compile(
    r"[（(]\s*[A-Za-z\u4e00-\u9fff]{2,24}\s*[,，]\s*(?:19|20)\d{2}[a-z]?\s*[)）]"
    r"|(?:19|20)\d{2}\s*年"
    r"|[A-Za-z][A-Za-z\-]{2,20}\s*(?:et\s+al\.?)?\s*[（(]\s*(?:19|20)\d{2}"
)


def _cited_in_sentence(body: str, m_start: int) -> bool:
    """该断言词**所在句内**是否出现了具体出处。"""
    lo = max(body.rfind(c, 0, m_start) for c in "。！？；\n") + 1
    hi = len(body)
    for c in "。！？；\n":
        k = body.find(c, m_start)
        if k != -1:
            hi = min(hi, k)
    return bool(CITATION.search(body[lo:hi]))


# 常见高校/机构后缀。紧跟其后的实体名说明这是**具体机构**，不是匿名主体。
# 必要性：回归测试发现「北京大学光华管理学院 2023 年的调查显示…」被误判为匿名机构 ——
# 因为模式里的 `研究` 命中了「管理学院…研究」附近的字样。误报会直接毁掉工具可信度。
_SCHOOL = (r"(?:光华|经济|金融|管理|商|法|医|工|理|文|史|哲|教育|新闻|社会学|"
           r"数学|物理|化学|生命|计算机|软件|环境|材料|能源|公共|统计|国际|马克思)")
KNOWN_INST = re.compile(
    # ① 高校名（后可跟至多一个教学/研究单位，再跟「学院/研究院」等）
    r"(?:北京|清华|复旦|上海交通|浙江|南京|武汉|中山|四川|山东|吉林|南开|天津|厦门|"
    r"西安交通|哈尔滨工业|同济|东南|中南|湖南|重庆|兰州|东北|华东师范|华南理工|"
    r"中国科学技术|中国人民|中央财经|上海财经|西南财经|对外经济贸易|中南财经政法)"
    r"[\u4e00-\u9fff]{0,6}(?:大学|学院|研究院|研究所)"
    r"(?:" + _SCHOOL + r"?[\u4e00-\u9fff]{0,4}(?:学院|研究院|研究所))?"
    # ② 机构/公司类
    r"|(?:中证协|证监会|沪深交易所|上海证券交易所|深圳证券交易所|中国人民银行|"
    r"国家统计局|财政部|工信部)"
    # ③ 金融机构与公司（带地域或行业限定词）
    r"|[\u4e00-\u9fff]{2,8}(?:证券|基金管理|基金|银行|保险|信托)(?:股份)?(?:有限公司|公司)?")


def cjk_len(t: str) -> int:
    return len(re.sub(r"\s", "", t))


def split_units(text: str):
    """按自然段切分（空行或换行），保留原序与原位置。"""
    units, pos = [], 0
    for blk in re.split(r"\n\s*\n|\n", text):
        if blk.strip():
            start = text.find(blk, pos)
            units.append({"text": blk.strip(), "start": start})
            pos = start + len(blk)
    return units


def scan(text: str):
    """返回 (命中的空洞指代列表, 段落级统计)。"""
    hits = []
    for u in split_units(text):
        known = [k.span() for k in KNOWN_INST.finditer(u["text"])]
        for p in PATTERNS:
            for m in p["re"].finditer(u["text"]):
                # 落在已知机构名范围内的命中一律丢弃（那是具体主体，不是匿名主体）
                if p["id"] == "anonymous_institution" and any(
                        s0 <= m.start() < e0 for s0, e0 in known):
                    continue
                if p.get("guard") == "cited_in_sentence" and _cited_in_sentence(u["text"], m.start()):
                    continue
                ctx_lo = max(0, m.start() - 30)
                ctx_hi = min(len(u["text"]), m.end() + 30)
                hits.append({
                    "pattern": p["id"], "name": p["name"],
                    "matched": m.group(0),
                    "quote": u["text"][ctx_lo:ctx_hi].strip(),
                    "unit_start": u["start"], "offset": m.start(),
                    "why": p["why"], "fix": p["fix"],
                })
    # 段落级：该段是否有具体性锚点
    units = []
    for i, u in enumerate(split_units(text), 1):
        marks = sum(1 for r in ANCHOR_MARKERS if r.search(u["text"]))
        n_hit = sum(1 for h in hits if h["unit_start"] == u["start"])
        units.append({"idx": i, "text": u["text"], "chars": cjk_len(u["text"]),
                      "anchor_markers": marks, "vague_hits": n_hit,
                      "anchored": marks > 0 and n_hit == 0})
    return hits, units


# 具体性密度的分子：数字 token、拉丁专名、技术词。
# 这些是"读者能拿去核查的东西"，与 abstract 里"合理配置供水规模"那类概括相对。
_NUM = re.compile(r"\d+(?:\.\d+)?")
_LATIN = re.compile(r"[A-Za-z]{2,}")
_TECH = re.compile(r"[\u4e00-\u9fff]{2,8}(?:系统|模型|方法|技术|装置|算法|结构|参数|"
                   r"标准|工艺|规范|系数|效应|检验|估计|指数|比率)")


def _sl_band(rate: float) -> str:
    """按人类摘要的分位数分档，不是"合格/不合格"判定。"""
    if rate <= 10.0:
        return "低（≤p75）"
    if rate <= 18.2:
        return "中（p75–p90）"
    return "高（>p90）"


def count_clauses(text: str) -> int:
    """按与 scan_subjectless 相同的边界数分句（引号内不计）。"""
    masked = _mask_quoted(text)
    n, start = 0, 0
    for i in range(len(masked) + 1):
        if i < len(masked) and masked[i] not in _BOUND:
            continue
        if cjk_len(masked[start:i]) >= CLAUSE_MIN:
            n += 1
        start = i + 1
    return n


def summarize(hits, units, sl=None, clauses=None):
    """汇总。

    ⚠️ **主指标是 `concrete_density`（连续量），不是 `anchor_coverage`（二元量）。**
    这是本项目自查时改的：原本把"该段有没有具体信息且无空洞"做成 0/1 覆盖率，
    但实测它在 AI 文本上与检测器分数几乎不相关（r = −0.28 ~ −0.46），
    而连续的具体性密度在那批 AI 文本上的相关系数为 r = −0.35 ~ −0.40。
    这些是样本内相关，不能解释为补数字会造成检测分数下降。
    二元指标的另一个毛病：一篇 164 字的摘要有 1 个数字就算 1.0，
    没有数字就算 0.0，于是它实际测的是"这段有没有数字"，不是"有多具体"。
    """
    total_chars = sum(u["chars"] for u in units)
    anchored_units = [u for u in units if u["anchored"]]
    blob = "".join(u["text"] for u in units)
    n = max(cjk_len(blob), 1)
    concrete = len(_NUM.findall(blob)) + len(_LATIN.findall(blob)) + len(_TECH.findall(blob))
    return {
        "chars": total_chars,
        "units": len(units),
        "vague_hits": len(hits),
        "by_pattern": {p["id"]: sum(1 for h in hits if h["pattern"] == p["id"])
                       for p in PATTERNS},
        "units_with_vague": sum(1 for u in units if u["vague_hits"] > 0),
        "units_anchored": len(anchored_units),
        # **主指标**：每百字的具体项数
        "concrete_density": round(concrete / n * 100, 3),
        "vague_density": round(len(hits) / n * 100, 3),
        # 参考指标（二元、粗糙，实测相关性弱）
        "anchor_coverage": round(len(anchored_units) / max(len(units), 1), 4),
        # 主语缺失：只把 hard（上一分句不是状语）计入速率，soft 单列。
        # 分母用**分句数**而不是字数 —— 这是一个"每句里有多少句缺主语"的量。
        "subjectless": len(sl or []),
        "subjectless_hard": sum(1 for h in (sl or []) if not h["soft"]),
        "clauses": clauses or 0,
        "subjectless_rate": round(
            sum(1 for h in (sl or []) if not h["soft"]) / max(clauses or 0, 1) * 100, 2),
        # 人类基线（616 篇真实 CNKI 摘要）：p75=10.0%、p90=18.2%
        "subjectless_band": _sl_band(
            sum(1 for h in (sl or []) if not h["soft"]) / max(clauses or 0, 1) * 100),
    }


def main():
    ap = argparse.ArgumentParser(
        description="事实锚定与具体性核查：列出待补出处或取值的位置，并比较修订前后的表面指标",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("path", help="待检查文本（.txt/.md）")
    ap.add_argument("--compare", default=None, help="修订后的文本，用于比较表面具体性指标")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="最多列出多少条（0=全部）")
    args = ap.parse_args()

    if not os.path.exists(args.path):
        print(f"错误：文件不存在：{args.path}", file=sys.stderr)
        return 2
    if os.path.isdir(args.path):
        print(f"错误：{args.path} 是目录，需要一个文本文件", file=sys.stderr)
        return 2
    try:
        text = open(args.path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print(f"错误：无法读取 {args.path}：{e}", file=sys.stderr)
        return 2
    hits, units = scan(text)
    sl = scan_subjectless(text)
    s = summarize(hits, units, sl, count_clauses(text))

    out = {"input": os.path.basename(args.path), "before": s}
    if args.compare:
        if not os.path.isfile(args.compare):
            print(f"错误：--compare 的文件不存在：{args.compare}", file=sys.stderr)
            return 2
        try:
            t2 = open(args.compare, encoding="utf-8", errors="replace").read()
        except OSError as e:
            print(f"错误：无法读取 {args.compare}：{e}", file=sys.stderr)
            return 2
        h2, u2 = scan(t2)
        sl2 = scan_subjectless(t2)
        out["after"] = summarize(h2, u2, sl2, count_clauses(t2))
        out["after"]["input"] = os.path.basename(args.compare)

    if args.json:
        out["hits"] = hits[: args.limit] if args.limit else hits
        out["subjectless_hits"] = sl[: args.limit] if args.limit else sl
        out["units"] = units
        if args.compare:
            # 「改后」的明细也要给出来，否则用户无从核对还剩哪些空洞
            out["hits_after"] = h2[: args.limit] if args.limit else h2
            out["subjectless_hits_after"] = (
                sl2[: args.limit] if args.limit else sl2)
            out["units_after"] = u2
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"文件：{args.path}   {s['chars']} 字   {s['units']} 段")
    print("=" * 78)
    print(f"待锚定位置 {s['vague_hits']} 处，分布在 {s['units_with_vague']}/{s['units']} 段")
    nz = {k: v for k, v in s["by_pattern"].items() if v}
    if nz:
        print("按类型：" + "  ".join(
            f"{next(p['name'] for p in PATTERNS if p['id'] == k)}={v}" for k, v in nz.items()))
    print(f"具体性密度（每百字具体项数，**主指标**）：{s['concrete_density']:.2f}")
    print(f"空洞密度（每百字空洞指代）：{s['vague_density']:.2f}")
    print(f"（参考）段级覆盖率：{s['anchor_coverage']:.1%} —— 二元量，与检测分数相关性弱")
    print(f"分句缺主语：{s['subjectless_hard']}/{s['clauses']} 句 "
          f"= {s['subjectless_rate']:.1f}%  →  {s['subjectless_band']}")
    print("  基线（616 篇真实 CNKI 摘要）：p50=0.0%  p75=10.0%  p90=18.2%；"
          "该指标在人类与 AI 摘要上无差异（每百分句 5.63 vs 5.02），"
          "**不能当作 AIGC 信号**，只用于自查行文。")
    print()
    for i, h in enumerate(sl[: args.limit] if args.limit else sl, 1):
        tag = "（上句是状语，可接受）" if h["soft"] else ""
        print(f"[主{ i }] 分句缺主语{tag}  「{h['quote']}」")
        print(f"    为什么：{h['why']}")
        print(f"    怎么补：{h['fix']}")
        print()
    shown = hits[: args.limit] if args.limit else hits
    for i, h in enumerate(shown, 1):
        print(f"[{i}] {h['name']}  「{h['matched']}」")
        print(f"    原文：…{h['quote']}…")
        print(f"    为什么空：{h['why']}")
        print(f"    怎么补：{h['fix']}")
        print()
    if len(hits) > len(shown):
        print(f"（其余 {len(hits) - len(shown)} 处见 --json 输出）")

    if args.compare:
        a = out["after"]
        print("=" * 78)
        print(f"对比 {args.compare}")
        print(f"  待锚定位置  {s['vague_hits']} → {a['vague_hits']}  "
              f"（{a['vague_hits'] - s['vague_hits']:+d}）")
        print(f"  具体性密度  {s['concrete_density']:.2f} → {a['concrete_density']:.2f}  "
              f"（{a['concrete_density'] - s['concrete_density']:+.2f} 每百字）")
        print(f"  空洞密度    {s['vague_density']:.2f} → {a['vague_density']:.2f}  "
              f"（{a['vague_density'] - s['vague_density']:+.2f} 每百字）")
        print(f"  字数        {s['chars']} → {a['chars']}（{a['chars'] - s['chars']:+d}）")
        print(f"  缺主语率    {s['subjectless_rate']:.1f}% → {a['subjectless_rate']:.1f}%"
              f"（{a['subjectless_rate'] - s['subjectless_rate']:+.1f} 个百分点，"
              f"{a['subjectless_band']}）")
        # 判据：具体性密度提高 **且** 空洞密度不升高。
        # 只看其一会被绕过：把"某高校"换成"有关院校"空洞数不变但也没变具体；
        # 而只说"具体性提高"会放过"补了具体信息但同时新增空洞"的情况。
        improved = (a["concrete_density"] > s["concrete_density"]
                    and a["vague_density"] <= s["vague_density"])
        print(f"\n{'✓ 表面具体性指标提高' if improved else '✗ 表面具体性指标未提高 —— 请按原始材料核查'}")
        print("提醒：本脚本只统计数字、术语等表面线索；线索不证明事实属实，"
              "也不是检测分数或平台结果预测。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aigc-risk-audit 回归测试（不依赖 pytest）。

    python3 tests/regression.py

覆盖两类：
  A. 事实锚定扫描器（anchor.py）——每个模式都要**能命中**，且**不误伤**正常学术表达。
  B. 反模式守卫——脚本绝不能建议/实施 skill 明令禁止的手段。

每条用例都断言"该抓的抓到 + 不该抓的不抓"。只断言"有输出"是空转。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANCHOR = ROOT / "scripts" / "anchor.py"

CASES: list[tuple[str, str, object]] = []


def case(name: str, guards: str):
    def deco(fn):
        CASES.append((name, guards, fn))
        return fn
    return deco


def run_anchor(text: str, compare: str | None = None, extra: list[str] | None = None):
    d = Path(tempfile.mkdtemp(prefix="aigc-reg-"))
    (d / "t.txt").write_text(text, encoding="utf-8")
    cmd = [sys.executable, str(ANCHOR), "t.txt", "--json"]
    if compare is not None:
        (d / "u.txt").write_text(compare, encoding="utf-8")
        cmd += ["--compare", "u.txt"]
    cmd += (extra or [])
    p = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
    try:
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        return p.returncode, {"__raw": p.stdout + p.stderr}


def pats(payload) -> set[str]:
    return {h["pattern"] for h in payload.get("hits", [])}


def n_hits(payload) -> int:
    return payload["before"]["vague_hits"]


# --------------------------------------------------------------------------- #
#  A. 每个模式都要能命中（正向）
# --------------------------------------------------------------------------- #

@case("P-匿名机构 须命中「某高校的调查显示」", "匿名主体无法核查，是最典型的空心论据")
def a1():
    _, p = run_anchor("某高校的调查显示，学生对AI工具的接受度较高。")
    return "anonymous_institution" in pats(p), f"命中={sorted(pats(p))}"


@case("P-无取值量 须命中「大量实验证明」", "没有量级就无法判断，也无法复算")
def a2():
    _, p = run_anchor("大量实验证明，现有检测方法可以有效识别。")
    return "vague_quantity" in pats(p), f"命中={sorted(pats(p))}"


@case("P-无出处断言 须命中裸「研究表明」", "断言了有研究支持却不给可查出处")
def a3():
    _, p = run_anchor("研究表明，AI生成文本具有明显的统计特征。")
    return "unsourced_claim" in pats(p), f"命中={sorted(pats(p))}"


@case("P-有结论无样本 须命中「样本量为若干」", "样本信息是复算前提")
def a4():
    _, p = run_anchor("样本量为若干篇论文，覆盖多个年份。")
    return "no_sample_size" in pats(p), f"命中={sorted(pats(p))}"


@case("P-空心框定 须命中「具有重要的理论意义」", "不承载信息的句子，删掉不损失内容")
def a5():
    _, p = run_anchor("本研究具有重要的理论意义和现实价值。")
    return "empty_framing" in pats(p), f"命中={sorted(pats(p))}"


@case("P-范围不明全称 须命中「所有…都」", "全称量词无分母即为不可证伪")
def a6():
    _, p = run_anchor("所有参与机构都提高了披露频率。")
    return "vague_scope" in pats(p), f"命中={sorted(pats(p))}"


@case("P-无依据缓和 须命中裸「在一定程度上」", "缓和词没有对应不确定性来源只是回避判断")
def a7():
    _, p = run_anchor("该方法在一定程度上能够识别生成内容。")
    return "hedge_without_basis" in pats(p), f"命中={sorted(pats(p))}"


# --------------------------------------------------------------------------- #
#  B. 不得误伤（反向）—— 这几条是本 skill 的"不误报"契约
# --------------------------------------------------------------------------- #

@case("N-有出处的断言不得命中", "「研究表明(张三, 2023)」是合格写法，误报会逼用户关掉工具")
def b1():
    _, p = run_anchor("研究表明（张三，2023），AI文本具有统计特征。")
    return "unsourced_claim" not in pats(p), f"命中={sorted(pats(p))}"


@case("N-带取值的量不得命中", "「1,234 篇」是已锚定的写法")
def b2():
    _, p = run_anchor("共纳入 1,234 篇文献，其中 312 篇为随机对照试验。")
    return n_hits(p) == 0, f"命中数={n_hits(p)} {sorted(pats(p))}"


@case("N-真实机构名不得命中", "「北京大学」是具体主体")
def b3():
    _, p = run_anchor("北京大学光华管理学院 2023 年的调查显示，接受度为 68.4%。")
    return "anonymous_institution" not in pats(p), f"命中={sorted(pats(p))}"


@case("N-带分母的全称不得命中", "「所有 27 个样本」有分母，可证伪")
def b4():
    _, p = run_anchor("在全部 27 个样本中，有 25 个符合该模式。")
    return "vague_scope" not in pats(p), f"命中={sorted(pats(p))}"


@case("N-有依据的不确定性不得命中", "「受限于 n=12」说明了不确定性来源")
def b5():
    _, p = run_anchor("受限于 n=12，本文的区间估计较宽，不作强结论。")
    return "hedge_without_basis" not in pats(p), f"命中={sorted(pats(p))}"


@case("N-干净学术段整段不得命中", "全锚定的段落必须零命中，否则工具在真实文本上不可用")
def b6():
    txt = ("基于 2018—2024 年沪深两市 4,215 家上市公司样本，"
           "本文使用双重差分模型检验改革效应。表 3 显示，"
           "处理组分析师覆盖数下降 12.3%（t=-4.18，p<0.01）。")
    _, p = run_anchor(txt)
    return n_hits(p) == 0, f"命中数={n_hits(p)} {sorted(pats(p))}"


@case("N-出处在断言词**之后**不得命中", "回归抓出的真缺陷：旧实现只看断言词后 12 字")
def b7():
    _, p = run_anchor("北京大学光华管理学院 2023 年对 1,204 名学生的调查显示，接受度为 68.4%。")
    return "unsourced_claim" not in pats(p) and "anonymous_institution" not in pats(p), \
        f"命中={sorted(pats(p))}"


@case("N-本人样本的自查表述不得命中", "「表 3 显示」是本文自己的结果，不是外部断言")
def b8():
    _, p = run_anchor("本文的回归结果见表 3：系数为 −0.34（聚类稳健 SE 0.11，n=4,215）。")
    return n_hits(p) == 0, f"命中={sorted(pats(p))}"


# --------------------------------------------------------------------------- #
#  C. 覆盖率与比较模式
# --------------------------------------------------------------------------- #

@case("C-具体性密度：有具体信息的文本须高于空洞文本",
      "主指标必须真的区分得开，否则它只是个装饰数字。"
      "（原主指标是二元覆盖率，自查发现它与检测分数几乎不相关，已降为参考项）")
def c1():
    _, p = run_anchor("样本为 2019—2024 年 312 只基金（n=312），回归系数 −0.34（SE 0.11）。")
    _, q = run_anchor("随着技术不断发展，某高校的研究表明该方法具有重要意义。")
    ok = p["before"]["concrete_density"] > q["before"]["concrete_density"] > 0
    return ok, f"具体 {p['before']['concrete_density']} vs 空洞 {q['before']['concrete_density']}"


@case("C-具体性密度：无任何具体项的文本须为 0", "反向：没有具体项的文本密度必须为 0")
def c2():
    _, p = run_anchor("某高校的调查显示，接受度较高。")
    return p["before"]["concrete_density"] == 0.0, f"密度={p['before']['concrete_density']}"


@case("C-空洞密度须随空洞数量单调上升", "两个密度都必须是可解释的量，不是凑出来的数")
def c3b():
    _, a = run_anchor("某高校的调查显示，接受度较高。")
    _, b = run_anchor("某高校的调查显示，大量研究表明，在一定程度上具有重要意义。")
    return b["before"]["vague_density"] > a["before"]["vague_density"], \
        f"{a['before']['vague_density']} vs {b['before']['vague_density']}"


@case("C-比较模式须报出具体性提高", "改完之后要能量化「具体性提高了没有」")
def c3():
    before = "某高校的调查显示，学生对AI工具的接受度较高。"
    after = "北京大学 2023 年对 1,204 名学生的调查显示，接受度为 68.4%。"
    _, p = run_anchor(before, compare=after)
    ok = (p["after"]["concrete_density"] > p["before"]["concrete_density"]
          and p["after"]["vague_density"] < p["before"]["vague_density"])
    return ok, (f"具体性 {p['before']['concrete_density']}→{p['after']['concrete_density']}  "
                f"空洞 {p['before']['vague_density']}→{p['after']['vague_density']}")


@case("C-比较模式：只换措辞不得报「提高了」",
      "把「某高校」换成「有关院校」是换词不是锚定 —— 具体性不能上升")
def c4():
    before = "某高校的调查显示，接受度较高。"
    after = "有关院校的调查显示，接受度较高。"
    _, p = run_anchor(before, compare=after)
    return (p["after"]["concrete_density"] <= p["before"]["concrete_density"]), \
        f"具体性 {p['before']['concrete_density']}→{p['after']['concrete_density']}"


@case("C-比较模式：两边同时升高时判据须为「未改善」",
      "判据是「具体性升高 **且** 空洞不升高」。只升高一边不算改善 ——"
      "否则「一边补具体、一边加套话」会被判成改善。这条用同一段文字精确构造。")
def c6():
    before = "北京大学 2023 年对 1,204 名学生调查，接受度为 68.4%。某高校的调查显示，接受度较高。"
    after = ("北京大学 2023 年对 1,204 名学生调查，接受度为 68.4%。"
             "某高校的调查显示，大量研究表明，在一定程度上具有重要意义。")
    _, p = run_anchor(before, compare=after)
    b, a = p["before"], p["after"]
    improved = (a["concrete_density"] > b["concrete_density"]
                and a["vague_density"] <= b["vague_density"])
    return (not improved) and a["vague_density"] > b["vague_density"], \
        f"具体性 {b['concrete_density']}→{a['concrete_density']}  " \
        f"空洞 {b['vague_density']}→{a['vague_density']}"


@case("N-机构名内部含模式词时仍不得命中",
      "「相关」+「研究」在机构名里也成立 —— 守卫必须按**位置**排除，"
      "而不是指望模式不匹配。突变测试发现：去掉位置守卫后原用例照样通过，"
      "说明当时根本没有用例真正触发这条守卫。")
def b9():
    _, p = run_anchor("中国人民大学相关研究院 2024 年的调查显示，样本覆盖 312 家机构。")
    return "anonymous_institution" not in pats(p), f"命中={sorted(pats(p))}"


@case("C-比较模式的 JSON 必须含改后明细",
      "只报改前，用户无法核对改后还剩哪些空洞 —— 突变测试发现这条没被断言")
def c5():
    _, p = run_anchor("某高校的调查显示，接受度较高。",
                      compare="北京大学 2023 年对 1,204 名学生的调查显示，接受度为 68.4%。")
    ok = ("hits_after" in p and "units_after" in p
          and len(p["units_after"]) >= 1 and isinstance(p["hits_after"], list))
    return ok, f"字段={[k for k in p if k.endswith('_after')]}"


@case("C-段级覆盖率仍须正确（它已降为参考项，但报告里还在）",
      "主指标换成连续密度后，覆盖率降为参考；降级不等于可以算错 ——"
      "突变测试发现它一度没有任何用例保护。")
def c7():
    _, good = run_anchor("样本为 2019—2024 年 312 只基金（n=312），回归系数 −0.34。")
    _, bad = run_anchor("某高校的调查显示，接受度较高。")
    return (good["before"]["anchor_coverage"] == 1.0
            and bad["before"]["anchor_coverage"] == 0.0), \
        f"干净={good['before']['anchor_coverage']} 空洞={bad['before']['anchor_coverage']}"


# --------------------------------------------------------------------------- #
#  D. 反模式守卫：工具不得建议禁止手段
# --------------------------------------------------------------------------- #

@case("G-输出不得建议同义词替换/零宽字符/加错别字",
      "这些是 skill 明令禁止的反模式，工具输出里出现即为越界")
def g1():
    d = Path(tempfile.mkdtemp(prefix="aigc-reg-"))
    (d / "t.txt").write_text("某高校的研究表明，大量实验证明该方法具有重要意义。",
                             encoding="utf-8")
    p = subprocess.run([sys.executable, str(ANCHOR), "t.txt"], cwd=d,
                       capture_output=True, text=True)
    blob = p.stdout + p.stderr
    banned = ["零宽", "错别字", "同义词替换", "同义替换", "转图片", "中英互译", "回译"]
    hit = [b for b in banned if b in blob]
    return not hit, f"出现禁用建议={hit}"


@case("G-每条命中都必须带「为什么空」与「怎么补」",
      "只报位置不给理由等于把判断丢回给用户；也没有可执行性")
def g2():
    _, p = run_anchor("某高校的调查显示，大量研究表明具有重要意义。")
    bad = [h for h in p["hits"] if not h.get("why") or not h.get("fix") or not h.get("quote")]
    return not bad, f"缺字段的命中={len(bad)}"


@case("G-json 输出必须自洽（计数与列表一致）",
      "报告里的汇总数与明细数不一致，说明统计口径有 bug")
def g3():
    _, p = run_anchor("某高校的调查显示，大量研究表明具有重要意义。")
    return p["before"]["vague_hits"] == len(p["hits"]), \
        f"汇总{p['before']['vague_hits']} vs 明细{len(p['hits'])}"


# --------------------------------------------------------------------------- #
#  I. 聚合口径（aggregate.py）—— 唯一直接对应平台报告字段的计算
# --------------------------------------------------------------------------- #

def _agg():
    sys.path.insert(0, str(ROOT / "scripts"))
    import aggregate as ag
    return ag


@case("AGG-字数加权须让长段主导", "段落等权会让 20 字过渡段与 200 字论证段同权，"
      "而平台的分子分母都按字数算")
def i1():
    ag = _agg()
    v = ag.aggregate([0.9, 0.1], [200, 20], "char_weighted")
    return abs(v - (0.9 * 200 + 0.1 * 20) / 220) < 1e-9 and v > 0.8, f"char_weighted={v:.4f}"


@case("AGG-三种口径在长短悬殊时须显著不同",
      "如果三者给出同一个数，说明有口径没起作用")
def i2():
    ag = _agg()
    a = {k: ag.aggregate([0.9, 0.1], [200, 20], k) for k in ag.AGG_CHOICES}
    ok = a["max"] == 0.9 and abs(a["mean"] - 0.5) < 1e-9 and a["char_weighted"] > 0.8
    return ok, f"{a}"


@case("AGG-疑似字数占比须与手算一致", "这是与平台 aigcRatio 同构的字段，算错就是误导")
def i3():
    ag = _agg()
    f, t, r = ag.flagged_char_ratio([0.9, 0.4, 0.6], [100, 200, 300], 0.5)
    return (f, t) == (400, 600) and abs(r - 400 / 600) < 1e-9, f"({f},{t},{r:.4f})"


@case("AGG-判定线边界：恰好 0.5 须算疑似", "知网原文是『>=0.5 即为疑似』，边界必须含等号")
def i4():
    ag = _agg()
    f, _, _ = ag.flagged_char_ratio([0.5], [100], 0.5)
    return f == 100, f"0.5 被算作疑似字数={f}"


@case("AGG-全零权重不得除零，须退回等权", "真实输入可能出现全零字数的段")
def i5():
    ag = _agg()
    v = ag.aggregate([0.8, 0.2], [0, 0], "char_weighted")
    return abs(v - 0.5) < 1e-9, f"{v}"


@case("AGG-空输入返回 0.0 而非 NaN", "NaN 会让下游所有比较为假，静默失效")
def i6():
    ag = _agg()
    v = ag.aggregate([], [], "char_weighted")
    return v == 0.0, f"{v!r}"


@case("AGG-长度不匹配须截断而不是错配", "分数与权重错位会让分数落到错误的段上")
def i7():
    ag = _agg()
    # 分数 2 个、权重 1 个 → 只算第一段
    v = ag.aggregate([0.9, 0.1], [50], "char_weighted")
    return abs(v - 0.9) < 1e-9, f"{v}"


@case("AGG-未知口径须报错，不得静默取默认", "静默默认会让你以为用的是平台口径")
def i8():
    ag = _agg()
    try:
        ag.aggregate([0.5], [1], "median")
        return False, "未报错"
    except ValueError as e:
        return "未知聚合口径" in str(e), str(e)[:60]


@case("AGG-加一个短而干净的段不得**升高**字数加权分（不变量）",
      "这是加权口径的定义性质：新增长度占比为 w 的干净段，"
      "分数必须朝 0 移动，不可能反向。若这条失败说明权重用反了。")
def i9():
    ag = _agg()
    base = ag.aggregate([0.9], [200], "char_weighted")
    added = ag.aggregate([0.9, 0.0], [200, 50], "char_weighted")
    return added < base, f"{base:.4f} → {added:.4f}"


@case("AGG-权重方向：长段必须比短段权重大（精确数值）",
      "突变测试发现原用例选的两段权重比恰好让方向不可分。"
      "这里用 1:4 的权重比，把加权值算到小数位 —— 权重用反时结果会明显不同。")
def i10():
    ag = _agg()
    v = ag.aggregate([1.0, 0.0], [1, 4], "char_weighted")
    return abs(v - 0.2) < 1e-9, f"期望 0.2，实得 {v:.6f}"


@case("AGG-疑似字数占比也须按正确权重方向", "同一个权重用反会让占比高估")
def i11():
    ag = _agg()
    f, t, r = ag.flagged_char_ratio([1.0, 0.0], [1, 4], 0.5)
    return (f, t) == (1, 5) and abs(r - 0.2) < 1e-9, f"({f},{t},{r:.4f})"


# --------------------------------------------------------------------------- #
#  H. CLI 契约：用户最先碰到的东西
# --------------------------------------------------------------------------- #

def _run(script: str, args: list[str], cwd: Path | None = None, stdin: str | None = None):
    d = cwd or Path(tempfile.mkdtemp(prefix="aigc-cli-"))
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                       cwd=d, capture_output=True, text=True, input=stdin)
    return p.returncode, p.stdout + p.stderr


@case("CLI-anchor --help 可用且说明用途", "连 --help 都挂掉的脚本没人会用")
def h1():
    code, out = _run("anchor.py", ["--help"])
    return code == 0 and "事实锚定" in out, f"exit={code}"


@case("CLI-anchor 缺文件须给出可用错误（不 traceback）",
      "输入错误必须与崩溃区分开")
def h2():
    code, out = _run("anchor.py", ["不存在的文件.txt"])
    return code != 0 and "Traceback" not in out, f"exit={code} out={out[:80]!r}"


@case("CLI-anchor 不得在输出里承诺降低平台 AI 率",
      "这是 skill 的核心红线：不承诺任何平台达标")
def h3():
    d = Path(tempfile.mkdtemp(prefix="aigc-cli-"))
    (d / "t.txt").write_text("某高校的调查显示，大量研究表明具有重要意义。", encoding="utf-8")
    code, out = _run("anchor.py", ["t.txt"], cwd=d)
    banned = ["保证过检", "确保通过", "降到 10% 以下", "必过知网", "包过"]
    hit = [b for b in banned if b in out]
    return not hit, f"出现承诺={hit}"


@case("CLI-score_text 缺 torch 时须给出可执行的指引，而不是 traceback",
      "用户在系统 python3 上直接跑脚本是常态，必须引导到正确的解释器")
def h4():
    code, out = _run("score_text.py", ["--help"])
    return code == 0 and "score_text" in out, f"exit={code} out={out[:100]!r}"


@case("CLI-reduce_aigc --help 须包含实测边界说明",
      "用户最可能误用的就是「降分」这件事，帮助文本里必须写清边界")
def h5():
    code, out = _run("reduce_aigc.py", ["--help"])
    ok = code == 0 and ("不是" in out and ("知网" in out or "商业平台" in out))
    return ok, f"exit={code}"


@case("CLI-trigger_hook 空 stdin 不得崩溃（退出码 0）",
      "钩子异常会打断用户提问，硬约束是静默退出")
def h6():
    code, out = _run("aigc_trigger_hook.py", [], stdin="")
    return code == 0, f"exit={code} out={out[:120]!r}"


@case("CLI-trigger_hook 无关提问不得注入上下文",
      "误触发比漏触发更烦人：用户问查重收费不该被塞降 AIGC 上下文")
def h7():
    payload = json.dumps({"prompt": "知网查重怎么收费？"})
    code, out = _run("aigc_trigger_hook.py", [], stdin=payload)
    return code == 0 and "AIGC" not in out, f"exit={code} out={out[:120]!r}"


@case("CLI-reduce_aigc --help 须点明「LLM 改写实测不降分」并指向 anchor.py",
      "引擎的机制与实测结论相冲突，帮助文本里不写清就是误导用户花算力")
def h8():
    code, out = _run("reduce_aigc.py", ["--help"])
    ok = code == 0 and "不降低检出率" in out and "anchor.py" in out
    return ok, f"exit={code} 含冲突声明={'不降低检出率' in out} 指向 anchor={'anchor.py' in out}"


@case("CLI-score_text --help 须说明三种聚合口径与平台字段的关系",
      "char_weighted 是唯一对齐平台 aigcRatio 的口径，用户必须知道默认是它")
def h9():
    code, out = _run("score_text.py", ["--help"])
    ok = code == 0 and "char_weighted" in out and "字数加权" in out
    return ok, f"exit={code}"


# --------------------------------------------------------------------------- #
#  J. 自证存证（evidence.py）—— 目标里的「自证存证」那一块
# --------------------------------------------------------------------------- #

def _mk_evidence(files: dict[str, str], subdirs: tuple[str, ...] = ()):
    d = Path(tempfile.mkdtemp(prefix="aigc-evd-"))
    for sd in subdirs:
        (d / sd).mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        fp = d / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    return d


def _run_evidence(d: Path, *extra: str):
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "evidence.py"), ".", *extra],
                       cwd=d, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


FULL_PACK = {
    "提纲-v1.md": "# 提纲\n1. 引言",
    "论文-初稿-v2.md": "# 初稿\n内容",
    "导师反馈-20260610.md": "导师：第三章证据链不完整",
    "资料/访谈01记录.md": "受访者 A：改革后费率下降",
    "原始数据.csv": "a,b\n1,2",
    "送检报告-20260620.md": "平台：知网 结果：22%",
}


@case("EVD-五类齐全时须报齐全", "这是用户唯一能看到的「我准备得够不够」的判据")
def j1():
    d = _mk_evidence(FULL_PACK)
    code, out = _run_evidence(d, "--json")
    r = json.loads(out)
    return code == 0 and not r["missing"], f"missing={r['missing']}"


@case("EVD-缺一类须点名并说明为什么需要", "只说「不齐」没用，要说缺哪类、为什么重要")
def j2():
    d = _mk_evidence({k: v for k, v in FULL_PACK.items() if "送检" not in k})
    code, out = _run_evidence(d, "--json")
    r = json.loads(out)
    ok = "送检记录" in r["missing"] and r["categories"]["submission"]["why"]
    return ok, f"missing={r['missing']}"


@case("EVD-空文件须报为完整性问题", "0 字节文件不能作为证据，但容易被忽略")
def j3():
    d = _mk_evidence(dict(FULL_PACK, **{"空文件.md": ""}))
    code, out = _run_evidence(d, "--json")
    r = json.loads(out)
    return any(x["kind"] == "空文件" for x in r["problems"]), f"problems={r['problems']}"


@case("EVD-同时符合多类的文件不得被静默归为单一类",
      "旧实现只取第一个匹配 —— 另一类的清点会静默漏掉，用户以为那类证据缺失")
def j4():
    d = _mk_evidence(dict(FULL_PACK, **{"导师反馈-v2-送检记录.md": "反馈 + 送检"}))
    code, out = _run_evidence(d, "--json")
    r = json.loads(out)
    amb = [x for x in r["ambiguous"] if "导师反馈-v2-送检记录.md" in x["path"]]
    return (len(amb) == 1 and len(amb[0]["categories"]) >= 2
            and r["categories"]["communication"]["count"] >= 2), \
        f"ambiguous={r['ambiguous']}"


@case("EVD-须生成含 SHA-256 的存证清单", "哈希是用来证明「我提交的就是这一版」的")
def j5():
    d = _mk_evidence(FULL_PACK)
    _run_evidence(d)
    mf = d / "作者证明包" / "MANIFEST.md"
    mj = d / "作者证明包" / "manifest.json"
    if not (mf.exists() and mj.exists()):
        return False, "未生成清单"
    r = json.loads(mj.read_text(encoding="utf-8"))
    hashed = [f for f in r["files"] if f.get("sha256")]
    md = mf.read_text(encoding="utf-8")
    # **不能只断言"SHA-256"这个词出现过** —— 把哈希列清空后表头还在，
    # 突变测试就是这么发现旧断言是空转的（EVD-存证清单不再写 SHA-256 未被抓到）。
    # 要断言的是：清单里真的出现了每个文件的 sha256 前 16 位。
    missing = [f["path"] for f in hashed if f["sha256"][:16] not in md]
    return (len(hashed) == len(r["files"]) and not missing), \
        f"含哈希文件 {len(hashed)}/{len(r['files'])}；清单缺失哈希的 {missing[:3]}"


@case("EVD-目录不存在须退出 2 且不 traceback", "输入错误与崩溃要区分")
def j6():
    d = Path(tempfile.mkdtemp(prefix="aigc-evd-"))
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "evidence.py"), "不存在的目录"],
                       cwd=d, capture_output=True, text=True)
    return p.returncode == 2 and "Traceback" not in p.stderr, f"exit={p.returncode}"


@case("EVD-输出不得评价文本是否 AI 生成或预测申诉结果",
      "本脚本的职责是清点证据，越界表态会给出虚假保证")
def j7():
    d = _mk_evidence(FULL_PACK)
    code, out = _run_evidence(d)
    banned = ["一定能申诉成功", "保证通过", "必然撤销", "检测结果会降低"]
    hit = [b for b in banned if b in out]
    return not hit, f"越界表述={hit}"


@case("EVD-送检记录类存在时不得建议反复送检",
      "反复送检本身是行为痕迹，skill 明令禁止")
def j8():
    d = _mk_evidence(FULL_PACK)
    code, out = _run_evidence(d)
    return "再送检一次" not in out and "多送检" not in out, "含反复送检建议"


@case("DOC-description 不得声称「把检出分数压下来」",
      "这是本 skill 最容易被误读的一句话，而实测结论恰恰相反："
      "LLM 改写不降低检出率，只有补信息在均值上有效且有限。"
      "description 是**模型路由的依据**，过度声称会直接把用户引向无效动作。")
def k1():
    fm = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    banned = ["把文本的 AIGC 检出分数压下来", "保证过检", "确保通过检测",
              "稳定过检", "必过知网"]
    hit = [b for b in banned if b in fm]
    ok = not hit and "不承诺" in fm
    return ok, f"越界表述={hit} 含不承诺={'不承诺' in fm}"


@case("DOC-边界声明须同时出现在 SKILL.md 与实测文档里",
      "边界只在代码注释里写一次，用户是看不到的")
def k2():
    sk = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    ee = (ROOT / "references" / "edit-effects.md").read_text(encoding="utf-8")
    return ("不承诺" in sk and "没有任何校准关系" in ee
            and "不等价" in ee), "边界表述缺失"


def main() -> int:
    fails = []
    print(f"aigc-risk-audit 回归 —— {len(CASES)} 条用例")
    print("=" * 92)
    for name, guards, fn in CASES:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"      {detail}")
            print(f"      防的是：{guards}")
            fails.append(name)
    print("=" * 92)
    print(f"{len(CASES) - len(fails)}/{len(CASES)} 通过")
    if fails:
        print("\n失败：")
        for f in fails:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aigc-risk-audit 的突变测试：把每一处判定改坏，看回归套件是否报警。

**只读源文件**，在临时沙箱副本上做突变，结束时校验源文件 sha256 未变。

纪律（本项目在别的 skill 上踩过的坑，全部继承）：
  1. 变异后先 `py_compile` —— 语法错误会让全部用例 FAIL，被误当成"抓到了"，
     而实际是工具没能启动。
  2. 断言锚点**恰好出现 1 次** —— 0 次是锚点失效，>1 次是改错了地方。
  3. `run_suite` 带超时 —— 否则等于用"跑不完"假装"没发现问题"。

用法：python3 tests/mutation.py
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

SKILL = pathlib.Path(__file__).resolve().parent.parent
SUITE_TIMEOUT = 300

# 每个突变针对的文件（相对 skill 根）。汇总时按文件分组统计。
TARGETS = {"anchor": "scripts/anchor.py",
           "aggregate": "scripts/aggregate.py",
           "evidence": "scripts/evidence.py"}

MUTATIONS: list[tuple[str, str, str]] = [
    # ---- 模式失效（每个模式都要有守卫）----
    ("匿名机构模式失效",
     'r"(?:某|有一?[个些位家]|相关|有关)(?:高校|大学|院校|机构|部门|"',
     'r"NEVERMATCH(?:某|有一?[个些位家]|相关|有关)(?:高校|大学|院校|机构|部门|"'),
    ("无取值量模式失效",
     'r"(?:大量|许多|很多|不少|一些|部分|若干|少数|多数|大多数|"',
     'r"NEVERMATCH(?:大量|许多|很多|不少|一些|部分|若干|少数|多数|大多数|"'),
    ("无出处断言模式失效",
     'r"(?:研究表明|研究发现|已有研究|文献(?:表明|指出)|据(?:报道|统计|了解)|"',
     'r"NEVERMATCH(?:研究表明|研究发现|已有研究|文献(?:表明|指出)|据(?:报道|统计|了解)|"'),
    ("无样本模式失效",
     'r"(?:样本|数据|问卷|访谈|案例|实验)(?:量|数)?(?:为|是)?\\s*"',
     'r"NEVERMATCH(?:样本|数据|问卷|访谈|案例|实验)(?:量|数)?(?:为|是)?\\s*"'),
    ("空心框定模式失效",
     'r"(?:具有重要的?(?:理论|现实|实践)?(?:意义|价值|作用)|"',
     'r"NEVERMATCH(?:具有重要的?(?:理论|现实|实践)?(?:意义|价值|作用)|"'),
    ("范围不明全称模式失效",
     'r"(?:所有|全部|任何|每一个|一切|无一例外)"',
     'r"NEVERMATCH(?:所有|全部|任何|每一个|一切|无一例外)"'),
    ("无依据缓和模式失效",
     'r"(?:或许|可能|大概|似乎|似乎可能|有一定的|在一定程度上)(?:会|能够|可以|是|存在)?"',
     'r"NEVERMATCH(?:或许|可能|大概|似乎|似乎可能|有一定的|在一定程度上)(?:会|能够|可以|是|存在)?"'),

    # ---- 守卫失效：这些是回归测试抓出来的真缺陷，必须有守卫 ----
    ("全称量的分母否定前瞻失效（有分母也被误报）",
     'r"(?!\\s*[\\d０-９,，]+\\s*(?:个|家|只|篇|位|种|项|年|条|次|%|％|‰)?)"',
     'r""'),
    ("已知机构名排除失效（具体机构被误报为匿名）",
     'if p["id"] == "anonymous_institution" and any(\n                        s0 <= m.start() < e0 for s0, e0 in known):\n                    continue',
     'if False:\n                    continue'),
    ("同句出处豁免失效（出处在后也被误报）",
     'if p.get("guard") == "cited_in_sentence" and _cited_in_sentence(u["text"], m.start()):\n                    continue',
     'if False:\n                    continue'),

    # ---- 覆盖率与比较 ----
    ("锚定覆盖率恒为 1（把空洞文本也算已锚定）",
     '"anchored": marks > 0 and n_hit == 0})',
     '"anchored": True})'),
    ("比较模式不再输出改后明细",
     '            out["hits_after"] = h2[: args.limit] if args.limit else h2',
     '            pass'),
    ("JSON 汇总数与明细不一致（vague_hits 少报）",
     '"vague_hits": len(hits),',
     '"vague_hits": 0,'),

    # ---- 聚合口径（aggregate.py）：唯一直接对应平台报告字段的计算 ----
    ("AGG-字数加权退化为段落等权",
     '    return float(sum(s * wi for s, wi in zip(sc, w)) / tw)',
     '    return float(sum(sc) / len(sc))'),
    ("AGG-权重被用反（短段反而权重大）",
     '    w = [max(float(x), 0.0) for x in weights]',
     '    w = [1.0 / max(float(x), 1.0) for x in weights]'),
    ("AGG-疑似字数占比：边界改为严格大于",
     '    flagged = sum(wi for s, wi in zip(sc, w) if s >= threshold)',
     '    flagged = sum(wi for s, wi in zip(sc, w) if s > threshold)'),
    ("AGG-全零权重改为直接除零（不退回等权）",
     '    if tw <= 0:\n        # 全部段字数都是 0：退回等权，而不是除零。\n        return float(sum(sc) / len(sc)) if sc else 0.0',
     '    if False:\n        return 0.0'),
    ("AGG-未知聚合口径静默取 mean（不报错）",
     '    if agg not in AGG_CHOICES:\n        raise ValueError(f"未知聚合口径 {agg!r}，可选 {AGG_CHOICES}")',
     '    if False:\n        raise ValueError("x")'),
    ("AGG-空输入返回 NaN 而非 0.0",
     '    if not scores:\n        return 0.0',
     '    if not scores:\n        return float("nan")'),

    # ---- 自证存证（evidence.py）----
    ("EVD-空文件不再报为完整性问题",
     '        if size == 0:\n            problems.append({"kind": "空文件", "path": rel,',
     '        if False:\n            problems.append({"kind": "空文件", "path": rel,'),
    ("EVD-多标签退化为只取第一个匹配",
     '    return [c["id"] for c in CATEGORIES\n            if ext in c["exts"] and re.search(c["globs"], base, re.I)]',
     '    hit = [c["id"] for c in CATEGORIES\n           if ext in c["exts"] and re.search(c["globs"], base, re.I)]\n    return hit[:1]'),
    ("EVD-存证清单不再写 SHA-256",
     '        h = (f["sha256"] or "")[:16] or "（过大未计算）"',
     '        h = ""'),
    ("EVD-缺失类别不再点名",
     '        "missing": [c["name"] for c in CATEGORIES if not by_cat[c["id"]]],',
     '        "missing": [],'),
    ("EVD-目录不存在时不再退出 2",
     '    if not os.path.isdir(args.root):\n        print(f"错误：目录不存在：{args.root}", file=sys.stderr)\n        return 2',
     '    if False:\n        return 2'),
    ("EVD---json 与 --out 同时给出时不再报错",
     '    if args.json and args.out:',
     '    if False:'),
]


def run_suite(cwd: pathlib.Path) -> int:
    try:
        r = subprocess.run([sys.executable, "tests/regression.py"],
                           cwd=cwd, capture_output=True, text=True,
                           timeout=SUITE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return -1
    return sum(1 for ln in r.stdout.splitlines() if ln.startswith("FAIL"))


# 每条突变默认作用于 anchor.py；以 "AGG:" 前缀标注的条目作用于 aggregate.py。
TARGET_FOR: dict[str, str] = {}


def _target_of(label: str) -> str:
    """按标签前缀分派目标文件。

    前缀不是装饰 —— 它决定这条突变改的是**哪个文件**。
    忘了注册新前缀的后果是：锚点在错误的文件里找不到（报"锚点 0 处"），
    而 runner 会把它记成"未覆盖"，看起来像缺用例，实际是分派错了。
    """
    if label.startswith("AGG-"):
        return "aggregate"
    if label.startswith("EVD-"):
        return "evidence"
    return "anchor"


def main() -> int:
    srcs = {k: SKILL / v for k, v in TARGETS.items()}
    good = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in srcs.items()}
    work = pathlib.Path(tempfile.mkdtemp(prefix="aigc-mut-"))
    sand = work / "skill"
    shutil.copytree(SKILL, sand, ignore=shutil.ignore_patterns("__pycache__", ".git"))

    try:
        base = run_suite(sand)
        print(f"沙箱基线：{base} 条失败（应为 0）")
        print("=" * 92)
        missed, invalid = [], []
        for label, old, new in MUTATIONS:
            tgt = TARGETS[_target_of(label)]
            sand_src = sand / tgt
            content = sand_src.read_text(encoding="utf-8")
            n = content.count(old)
            if n != 1:
                print(f"  [锚点 {n} 处] {label}")
                missed.append(f"{label}（锚点 {n} 处）")
                continue
            sand_src.write_text(content.replace(old, new, 1), encoding="utf-8")
            if subprocess.run([sys.executable, "-m", "py_compile", str(sand_src)],
                              capture_output=True).returncode != 0:
                sand_src.write_text(content, encoding="utf-8")
                print(f"  [无效突变·语法错误] {label}")
                invalid.append(label)
                continue
            k = run_suite(sand)
            sand_src.write_text(content, encoding="utf-8")
            if k == 0:
                missed.append(label)
            mark = "抓到" if k > 0 else ("抓到(超时)" if k < 0 else "!! 未抓到")
            print(f"  {mark:<11} {label}：{k} 条失败")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for k, p in srcs.items():
        if hashlib.sha256(p.read_bytes()).hexdigest() != good[k]:
            print(f"\n!! 严重：源文件在突变测试期间被改动（{p}）")
            return 2

    total = len(MUTATIONS)
    caught = total - len(missed)
    print("=" * 92)
    print("源文件未被改动（" + "  ".join(f"{k}={v[:12]}…" for k, v in good.items()) + "）")
    print(f"覆盖统计：{caught}/{total} 处突变被用例保护"
          f"（{caught * 100 // total if total else 0}%），未覆盖 {len(missed)} 处")
    if invalid:
        print(f"另有 {len(invalid)} 处突变**无效**（语法错误，不等同于被保护）")
    if missed:
        print(f"\n未被任何用例保护：{len(missed)} 处")
        for m in missed:
            print(f"  - {m}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现「三类编辑对检出分数的影响」实验（配对设计，无需 LLM）。

    python3 tests/measure_edits.py --bench ~/CC/projects/dsh开发/aigc-bench \\
        --python ~/.venvs/aigc/bin/python --limit 120 --out out/edit_effects.json

为什么需要这个脚本
------------------
skill 里的每一条改写优先级的**方向**都必须能追溯到测量，而不是经验。
本脚本测量的是：

    在同一篇论文上（人类摘要 vs 其 AI 版本，同题配对），
    施加三类规则化编辑后，三个开源中文检测器的**文档级分数变化**。

设计要点（都是为了让结论可辩护）
--------------------------------
1. **配对**：每个 base 同时有人类版与 AI 版，编辑只施加在 AI 版上，比较**配对差**。
2. **字数对齐**：三类编辑补入的字数接近，避免用"长度差"解释分数差。
3. **不改事实**：事实锚定只把**人类版里已有的**具体信息搬过去，
   不生成任何新信息。这是为了测"信息密度"这一维，而不是测"编数据"。
4. **主指标是检出率**（固定 FPR 下的二分类），不是平均分 —— 平台是阈值判定。
5. **报告符号一致性**：同一编辑在三个检测器上方向是否一致。
   这一步决定了"能不能把这条编辑当成策略推广"。

输出：out/edit_effects.json（原始分数）+ 终端报告。
"""
from __future__ import annotations

import argparse, json, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BENCH = os.path.expanduser("~/CC/projects/dsh开发/aigc-bench")
DEFAULT_PY = os.path.expanduser("~/.venvs/aigc/bin/python")

PROBE = r'''
import argparse, json, os, re, sys, time
import numpy as np, torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
B = sys.argv[1]; OUT = sys.argv[2]; LIMIT = int(sys.argv[3])
MODELS = {
 "d_hello": f"{B}/data/models/Hello-SimpleAI__chatgpt-detector-roberta-chinese",
 "d_cmj":   f"{B}/data/models/mokawa3018__cmj-chinese-aigc-text-detector",
 "d_zhv3":  f"{B}/data/models/yuchuantian__AIGC_detector_zhv3"}
AI_KEYS = ("ai","chatgpt","gpt","machine","generated","fake","llm")
CONNECTORS = ("首先","其次","再次","最后","此外","另外","因此","从而","综上",
              "值得注意","需要指出","总体而言","总的来说","不仅可以","而且")
NUM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:个|种|项|%|％|倍|年|月|日|米|吨|万元|亿元)?")
TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,8}(?:系统|模型|方法|技术|装置|算法|结构|参数|标准|工艺|规范)")
def ai_index(cfg):
    for i, lab in cfg.id2label.items():
        if any(k in str(lab).lower() for k in AI_KEYS): return int(i)
    return 1
def chars(t): return len(re.sub(r"\s", "", t))
def edit_fact(ai, hu):
    nums=[m.group(0).strip() for m in NUM_RE.finditer(hu)]
    terms=[t for t in dict.fromkeys(TERM_RE.findall(hu))]
    nn=[n for n in nums if n and n not in ai]; nt=[t for t in terms if t not in ai]
    bits=[]
    if nt: bits.append("、".join(nt[:4]))
    if nn: bits.append("、".join(nn[:4]))
    if not bits: return ai
    return ai.rstrip()+"文中所涉具体对象与取值包括："+"；".join(bits)+"。"
def edit_meta(ai, hu):
    return ai.rstrip()+"需要说明的是，上述机制本文未能逐一验证，结论受样本与口径限制。"
def edit_rhythm(ai, hu):
    t=ai
    for c in CONNECTORS: t=t.replace(c+"，","").replace(c+"、","")
    return "".join(s.strip() for s in re.split(r"(?<=[。！？；])", t) if s.strip())
@torch.no_grad()
def score(det, docs, bs=16):
    tok,mdl,ai=det["tok"],det["model"],det["ai"]
    flat,own=[],[]
    for di,d in enumerate(docs):
        got=False
        for blk in d.split("\n"):
            blk=blk.strip()
            if len(blk)>=15 and not blk.startswith("#"): flat.append(blk); own.append(di); got=True
        if not got: flat.append(d.strip() or "　"); own.append(di)
    tot=np.zeros(len(docs)); cnt=np.zeros(len(docs))
    for i in range(0,len(flat),bs):
        enc=tok(flat[i:i+bs],return_tensors="pt",truncation=True,max_length=512,padding=True)
        pr=torch.softmax(mdl(**enc).logits,-1)[:,ai].numpy()
        for j,p in enumerate(pr):
            di=own[i+j]; tot[di]+=p; cnt[di]+=1
    return np.where(cnt>0, tot/np.maximum(cnt,1), np.nan)
humans={r["doc_id"]:r["text"] for r in (json.loads(l) for l in open(f"{B}/data/exp/human.jsonl",encoding="utf-8")) if r.get("source")=="H_abs"}
corpus=[json.loads(l) for l in open(f"{B}/data/exp/corpus.jsonl",encoding="utf-8")]
pairs=[]
for r in corpus:
    if r.get("condition")=="M00" and r["base_id"].startswith("B_abs"):
        hid=r["base_id"].replace("B_abs_","H_abs_")
        if hid in humans: pairs.append((hid,humans[hid],r["text"]))
if LIMIT: pairs=pairs[:LIMIT]
print(f"配对 {len(pairs)} 对", flush=True)
C={"H":[], "AI":[], "A_fact":[], "B_meta":[], "C_rhythm":[]}
for hid,ht,at in pairs:
    fa, fm, fr = edit_fact(at,ht), edit_meta(at,ht), edit_rhythm(at,ht)
    cap=len(at)+120
    C["H"].append(ht); C["AI"].append(at)
    C["A_fact"].append(fa[:cap]); C["B_meta"].append(fm[:cap]); C["C_rhythm"].append(fr[:cap])
print("字数(去空白)均值: "+"  ".join(f"{k}={np.mean([chars(x) for x in v]):.0f}" for k,v in C.items()), flush=True)
res={"n_pairs":len(pairs),"chars":{k:[chars(x) for x in v] for k,v in C.items()},"scores":{}}
for dn,mp in MODELS.items():
    tok=AutoTokenizer.from_pretrained(mp); mdl=AutoModelForSequenceClassification.from_pretrained(mp).eval()
    det={"tok":tok,"model":mdl,"ai":ai_index(mdl.config)}
    res["scores"][dn]={}
    for cn,docs in C.items():
        s=score(det,docs); res["scores"][dn][cn]=[float(x) for x in s]
        print(f"  {dn:<8} {cn:<9} mean={np.nanmean(s):.4f}", flush=True)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(res, open(OUT,"w"), ensure_ascii=False, indent=1)
print("写到", OUT)
'''


def main() -> int:
    ap = argparse.ArgumentParser(description="复现编辑效应实验")
    ap.add_argument("--bench", default=DEFAULT_BENCH)
    ap.add_argument("--python", default=DEFAULT_PY, help="带 torch 的解释器")
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--out", default=str(HERE.parent / "out" / "edit_effects.json"))
    a = ap.parse_args()

    if not Path(a.bench).is_dir():
        print(f"错误：回测工程不存在 {a.bench}\n"
              "      该实验需要本地检测器权重与语料（含人类摘要 + 同题 AI 版本）。",
              file=sys.stderr)
        return 2
    if not Path(a.python).exists():
        print(f"错误：找不到带 torch 的解释器 {a.python}", file=sys.stderr)
        return 2

    probe = Path(a.out).parent / "_probe_tmp.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(PROBE, encoding="utf-8")
    try:
        r = subprocess.run([a.python, str(probe), a.bench, a.out, str(a.limit)])
    finally:
        probe.unlink(missing_ok=True)
    if r.returncode != 0:
        return r.returncode

    # 汇总
    import numpy as np
    d = json.load(open(a.out, encoding="utf-8"))
    S = d["scores"]
    rng = np.random.default_rng(11)

    def boot(x, B=4000):
        idx = rng.integers(0, len(x), size=(B, len(x)))
        return np.percentile(x[idx].mean(axis=1), [2.5, 97.5])

    print("\n" + "=" * 78)
    print(f"配对 n={d['n_pairs']}")
    print(f"{'检测器':<9}{'编辑':<10}{'Δ均值':>9}  {'95% CI':<22}{'Δ<0 比例':>10}")
    for dn in S:
        base = np.array(S[dn]["AI"])
        for c in ("A_fact", "B_meta", "C_rhythm"):
            dd = np.array(S[dn][c]) - base
            lo, hi = boot(dd)
            print(f"{dn:<9}{c:<10}{dd.mean():>+9.4f}  [{lo:+.4f}, {hi:+.4f}]{'':>4}{(dd<0).mean():>9.1%}")
    print("\n5% FPR 口径（阈值 = 人类组 p95）的检出率：")
    for dn in S:
        hu = np.array(S[dn]["H"]); thr = float(np.percentile(hu, 95))
        row = [f"{c}={(np.array(S[dn][c])>=thr).mean():.0%}"
               for c in ("H", "AI", "A_fact", "B_meta", "C_rhythm")]
        print(f"  {dn:<9} thr={thr:.4f}  " + "  ".join(row))
    print("\n三检测器符号一致率（决定能否把该编辑当策略推广）：")
    ks = list(S)
    for c in ("A_fact", "B_meta", "C_rhythm"):
        agree = sum(1 for k in range(d["n_pairs"])
                    if len({np.sign(np.array(S[dn][c])[k] - np.array(S[dn]["AI"])[k])
                            for dn in ks if abs(np.array(S[dn][c])[k] - np.array(S[dn]["AI"])[k]) > 1e-9}) == 1)
        print(f"  {c:<10} {agree}/{d['n_pairs']} = {agree/d['n_pairs']:.0%}")
    print("=" * 78)
    print("⚠️ 这是**开源检测器**上的代理指标，不是知网/维普/PaperPass 的结果。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

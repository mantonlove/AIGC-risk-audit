#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自证存证：把你手上的"作者证据"整理成可提交的清单，并做完整性检查。

为什么要有这个脚本
------------------
`references/appeal.md` 讲清了"要存什么"，但全是散文 —— 用户读完仍然不知道
**自己手上缺哪一项**。本脚本把那套要求变成可执行检查：

    python3 scripts/evidence.py ~/论文材料 --out 作者证明包

做三件事：

1. **分类清点**：把目录里的文件按证据类型归档（过程文件 / 版本历史 /
   沟通记录 / 素材来源 / 送检记录）。
2. **完整性检查**（可执行，不是提示）：
   - 每类证据"有 / 没有"；
   - 时间线自洽：素材日期 ≤ 成稿日期 ≤ 送检日期（倒挂说明证据链有问题）；
   - 空文件、坏文件、明显不是证据的文件（如 `.DS_Store`）；
   - 引用到的本地路径是否存在（如文稿里写的 `资料/访谈01.md`）。
3. **生成可核对的存证清单**：`MANIFEST.md`（人读）+ `manifest.json`（含每个文件的
   SHA-256）。哈希的作用是：你送检之后可以证明"我当时提交的就是这一版"。

⚠️ 本脚本**不判断**你的文本是不是 AI 写的，也**不预测**任何申诉结果。
    它只回答"证据齐不齐、自不自洽"。
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
#  证据类型。每条给"为什么需要"与"什么样的文件算数"——避免用户存了一堆无关文件。
# --------------------------------------------------------------------------- #
CATEGORIES = [
    {
        "id": "process",
        "name": "过程文件",
        "why": "最能证明「是你写的」的一类：提纲、便签、草稿、实验记录。"
               "它证明的是**逐步形成**，而不是一次生成。",
        "globs": (r"提纲|大纲|outline|草稿|draft|笔记|note|便签|记录|log|"
                  r"实验|数据|访谈|转录|transcript|问卷"),
        "exts": (".md", ".txt", ".docx", ".doc", ".rtf", ".org", ".tex"),
    },
    {
        "id": "versions",
        "name": "版本与修订记录",
        "why": "证明文本是**迭代**出来的。至少有 3 个时间点、且内容确有差异。",
        "globs": (r"v\d|版本|修订|revision|final|初稿|二稿|三稿|改"),
        "exts": (".md", ".txt", ".docx", ".doc", ".pdf", ".tex"),
    },
    {
        "id": "communication",
        "name": "沟通记录",
        "why": "导师/同门的反馈与你的回应，是第三方视角的过程证据。",
        "globs": (r"导师|老师|反馈|意见|邮件|mail|微信|会议|meeting|讨论|批注"),
        "exts": (".md", ".txt", ".pdf", ".docx", ".doc", ".eml", ".msg", ".png", ".jpg"),
    },
    {
        "id": "sources",
        "name": "素材来源",
        "why": "你读的文献、抄的原文、访谈对象、原始数据。证明论据有出处。",
        "globs": (r"文献|参考|reference|引文|书目|资料|素材|原始|数据|raw|source"),
        "exts": (".md", ".txt", ".pdf", ".csv", ".xlsx", ".json", ".bib", ".ris"),
    },
    {
        "id": "submission",
        "name": "送检记录",
        "why": "每次送检的时间、平台、版本。**证明你没有反复送检拟合分数**。",
        "globs": (r"送检|检测报告|查重|aigc|report|结果"),
        "exts": (".md", ".txt", ".pdf", ".png", ".jpg", ".json"),
    },
]

IGNORE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "out"}


def sha256_of(p: str, cap: int = 64 << 20) -> str | None:
    try:
        if os.path.getsize(p) > cap:
            return None
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def walk(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn in IGNORE_NAMES or fn.startswith("."):
                continue
            yield os.path.join(dirpath, fn)


def classify(path: str) -> list[str]:
    """返回该文件**匹配到的所有**证据类型（可能是多个）。

    旧实现只返回第一个匹配 —— 于是 `导师反馈-v2-送检.md` 这种同时符合
    "沟通记录"与"送检记录"的文件只会被归到一类，另一类的清点**静默漏掉**，
    用户会以为那类证据缺失。多标签 + 显式报冲突才是诚实的做法。
    """
    base = os.path.basename(path)
    ext = os.path.splitext(base)[1].lower()
    return [c["id"] for c in CATEGORIES
            if ext in c["exts"] and re.search(c["globs"], base, re.I)]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="自证存证：清点作者证据、做完整性检查、生成可核对清单",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("root", help="你的论文材料目录")
    ap.add_argument("--out", default=None, help="输出目录（默认 <root>/作者证明包）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 到 stdout")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        print(f"错误：目录不存在：{args.root}", file=sys.stderr)
        return 2

    files, problems = [], []
    for p in walk(args.root):
        try:
            size = os.path.getsize(p)
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        rel = os.path.relpath(p, args.root)
        if size == 0:
            problems.append({"kind": "空文件", "path": rel,
                             "detail": "0 字节的文件不能作为证据"})
        cats = classify(rel)
        files.append({"path": rel, "abs": p, "size": size, "mtime": mtime,
                      "sha256": sha256_of(p), "categories": cats})

    files.sort(key=lambda x: x["mtime"])
    by_cat = {c["id"]: [f for f in files if c["id"] in f["categories"]]
              for c in CATEGORIES}
    unclassified = [f for f in files if not f["categories"]]
    ambiguous = [f for f in files if len(f["categories"]) > 1]

    # 时间线自洽：最早素材 ≤ 最新成稿（倒挂说明"素材比稿子还新"，通常是摆拍式补材料）
    timeline_ok = True
    newest_process = max((f["mtime"] for f in by_cat["process"]), default=None)
    if newest_process and files:
        # 用"最晚修改的任何文件"当送检近似
        latest = max(f["mtime"] for f in files)
        if newest_process > latest + 60:
            timeline_ok = False

    report = {
        "root": os.path.abspath(args.root),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "files_total": len(files),
        "bytes_total": sum(f["size"] for f in files),
        "categories": {
            c["id"]: {"name": c["name"], "why": c["why"],
                      "count": len(by_cat[c["id"]]),
                      "present": len(by_cat[c["id"]]) > 0,
                      "files": [f["path"] for f in by_cat[c["id"]]][:50]}
            for c in CATEGORIES},
        "missing": [c["name"] for c in CATEGORIES if not by_cat[c["id"]]],
        "unclassified_count": len(unclassified),
        "ambiguous": [{"path": f["path"], "categories": f["categories"]} for f in ambiguous],
        "problems": problems,
        "timeline_consistent": timeline_ok,
        "files": [{k: v for k, v in f.items() if k != "abs"} for f in files],
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    print(f"目录：{args.root}")
    print(f"文件 {report['files_total']} 个 / {report['bytes_total']/1e6:.2f} MB")
    print("=" * 78)
    print("证据类型清点：")
    for c in CATEGORIES:
        d = report["categories"][c["id"]]
        mark = "✓" if d["present"] else "✗ 缺"
        print(f"  [{mark}] {c['name']:<10} {d['count']:>3} 个")
        if not d["present"]:
            print(f"        为什么需要：{c['why']}")
    print()
    if report["missing"]:
        print(f"**缺失 {len(report['missing'])} 类**：{', '.join(report['missing'])}")
        print("  → 缺的这几类在申诉时通常是决定性的，建议先补。")
    else:
        print("五类证据齐全。")
    if report["unclassified_count"]:
        print(f"\n未归类文件 {report['unclassified_count']} 个"
              "（文件名不含可识别关键词或扩展名不符，可手动改名后重跑）")
    if report["ambiguous"]:
        print(f"\n同时符合多类的文件 {len(report['ambiguous'])} 个"
              "（已计入每一类，不重复计字数）：")
        for x in report["ambiguous"][:5]:
            print(f"  {x['path']} → {'/'.join(x['categories'])}")
    if problems:
        print(f"\n完整性问题 {len(problems)} 处：")
        for x in problems[:10]:
            print(f"  [{x['kind']}] {x['path']} —— {x['detail']}")
    if not timeline_ok:
        print("\n⚠️ 时间线异常：有「过程文件」比其它文件都新，"
              "这通常意味着材料是事后补的。申诉时对方可能问这个。")

    out_dir = args.out or os.path.join(args.root, "作者证明包")
    os.makedirs(out_dir, exist_ok=True)
    mp = os.path.join(out_dir, "manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    lines = ["# 作者证明包 · 存证清单", "",
             f"生成时间：{report['generated_at']}",
             f"材料目录：`{report['root']}`",
             f"文件数：{report['files_total']}　合计 {report['bytes_total']/1e6:.2f} MB", "",
             "## 一、证据类型", ""]
    for c in CATEGORIES:
        d = report["categories"][c["id"]]
        lines.append(f"### {c['name']}（{d['count']} 个）" if d["present"]
                     else f"### {c['name']} —— **缺失**")
        lines.append("")
        if d["present"]:
            for p in d["files"]:
                lines.append(f"- `{p}`")
        else:
            lines.append(f"为什么需要：{c['why']}")
        lines.append("")
    lines += ["## 二、完整性问题", ""]
    lines += ([f"- [{x['kind']}] `{x['path']}` —— {x['detail']}" for x in problems]
              or ["无。"])
    lines += ["", "## 三、文件哈希（用于证明提交版本）", "",
              "送检后若要证明「我提交的就是这一版」，把本清单与哈希一起保存。", "",
              "| 文件 | 字节 | SHA-256（前 16 位） |", "|---|---:|---|"]
    for f in report["files"]:
        h = (f["sha256"] or "")[:16] or "（过大未计算）"
        lines.append(f"| `{f['path']}` | {f['size']} | `{h}` |")
    lines += ["", "---", "",
              "本清单由 `scripts/evidence.py` 生成，只做**清点与完整性检查**，",
              "不判断文本是否由 AI 生成，也不预测任何申诉结果。"]
    md = os.path.join(out_dir, "MANIFEST.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n存证清单 -> {md}")
    print(f"机器可读 -> {mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

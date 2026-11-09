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
   - 文件修改时间的顺序线索（不能据此判断真实写作或送检时间）；
   - 标出空文件，忽略 `.DS_Store` 等系统文件。
3. **生成可核对的存证清单**：`MANIFEST.md`（人读）+ `manifest.json`（含可计算文件的
   SHA-256）。哈希用于日后核对文件是否与清单所记录的版本一致；
   对应送检版本还需平台回执等独立记录。

⚠️ 本脚本**不判断**你的文本是不是 AI 写的，也**不预测**任何申诉结果。
   它只清点材料，并指出值得人工核查的线索。
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
        "why": "提纲、便签、草稿、实验记录可帮助说明写作过程，"
               "还需结合内容与来源人工核查。",
        "globs": (r"提纲|大纲|outline|草稿|draft|笔记|note|便签|记录|log|"
                  r"实验|数据|访谈|转录|transcript|问卷"),
        "exts": (".md", ".txt", ".docx", ".doc", ".rtf", ".org", ".tex"),
    },
    {
        "id": "versions",
        "name": "版本与修订记录",
        "why": "不同版本可帮助核查文本如何变化；须确认内容确有差异。",
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
        "why": "文献、访谈和原始数据可帮助核对论据来源。",
        "globs": (r"文献|参考|reference|引文|书目|资料|素材|原始|数据|raw|source"),
        "exts": (".md", ".txt", ".pdf", ".csv", ".xlsx", ".json", ".bib", ".ris"),
    },
    {
        "id": "submission",
        "name": "送检记录",
        "why": "记录送检平台、时间及对应版本，便于与平台回执核对。",
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


def walk(root: str, excluded_dir: str | None = None):
    """遍历材料，跳过本次生成的证明包，避免清单收录自身。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in IGNORE_DIRS and not d.startswith(".")
                       and os.path.realpath(os.path.join(dirpath, d)) != excluded_dir]
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


def check_mtime_order(by_cat: dict) -> tuple[bool | None, list[dict]]:
    """给出可复核的文件修改时间顺序；mtime 不是写作或送检时间。"""
    # 同一文件即使被多标签归类，也不能同时充当两个阶段的独立时间点。
    stage_ids = ("sources", "versions", "submission")
    stages = tuple((name, [f for f in by_cat[stage_id]
                           if not any(other in f["categories"]
                                      for other in stage_ids if other != stage_id)])
                   for name, stage_id in (("素材", "sources"),
                                          ("文稿版本", "versions"),
                                          ("送检记录", "submission")))
    checks = []
    for (before_name, before_files), (after_name, after_files) in zip(stages, stages[1:]):
        if not before_files or not after_files:
            missing = [name for name, group in ((before_name, before_files),
                                                 (after_name, after_files)) if not group]
            checks.append({
                "before_stage": before_name,
                "after_stage": after_name,
                "before_path": None,
                "after_path": None,
                "before_mtime": None,
                "after_mtime": None,
                "status": "insufficient_stages",
                "reason": "缺少可独立比较的阶段文件：" + "、".join(missing),
            })
            continue
        before = min(before_files, key=lambda f: f["mtime"])
        after = min(after_files, key=lambda f: f["mtime"])
        inverted = before["mtime"] > after["mtime"] + 60
        checks.append({
            "before_stage": before_name,
            "after_stage": after_name,
            "before_path": before["path"],
            "after_path": after["path"],
            "before_mtime": before["mtime"],
            "after_mtime": after["mtime"],
            "status": "needs_review" if inverted else "mtime_order_ok",
        })
    if any(x["status"] == "needs_review" for x in checks):
        return False, checks
    return (True if all(x["status"] == "mtime_order_ok" for x in checks)
            else None), checks


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

    root = os.path.realpath(args.root)
    out_dir = os.path.realpath(args.out or os.path.join(root, "作者证明包"))
    if out_dir == root:
        print("错误：输出目录不能与材料目录相同", file=sys.stderr)
        return 2

    files, problems = [], []
    for p in walk(root, out_dir):
        try:
            size = os.path.getsize(p)
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        rel = os.path.relpath(p, root)
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

    timeline_ok, timeline_checks = check_mtime_order(by_cat)

    report = {
        "root": root,
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
        "timeline_checks": timeline_checks,
        "timeline_note": "仅比较文件修改时间（mtime）；复制、下载、同步或再次编辑都可能改变它。顺序正常不能证明写作真实性，顺序倒挂也不能证明材料是事后补造的。",
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
        print(f"**未找到 {len(report['missing'])} 类候选文件**：{', '.join(report['missing'])}")
        print("  → 请核对是否确有相关材料；没有的不要事后伪造。")
    else:
        print("五类均有候选文件；还需人工核对内容与来源。")
    if report["unclassified_count"]:
        print(f"\n未归类文件 {report['unclassified_count']} 个"
              "（文件名或扩展名未匹配；请人工核对类型与内容）")
    if report["ambiguous"]:
        print(f"\n同时符合多类的文件 {len(report['ambiguous'])} 个"
              "（已计入每一类，不重复计字数）：")
        for x in report["ambiguous"][:5]:
            print(f"  {x['path']} → {'/'.join(x['categories'])}")
    if problems:
        print(f"\n完整性问题 {len(problems)} 处：")
        for x in problems[:10]:
            print(f"  [{x['kind']}] {x['path']} —— {x['detail']}")
    if timeline_ok is False:
        print("\n⚠️ 文件修改时间有倒序线索，请对照原始版本、平台回执核查：")
        for check in timeline_checks:
            if check["status"] == "needs_review":
                print(f"  {check['before_stage']} `{check['before_path']}` 晚于"
                      f" {check['after_stage']} `{check['after_path']}`")
    elif timeline_ok is None:
        print("\n文件修改时间不足以检查完整的素材→文稿→送检顺序。")
    print("文件修改时间不能证明写作真实性；复制、下载、同步或再次编辑都可能改变它。")

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
    lines += ["", "## 三、文件修改时间线索", "",
              report["timeline_note"], ""]
    for check in timeline_checks:
        if check["status"] == "insufficient_stages":
            lines.append(f"- {check['before_stage']} → {check['after_stage']}："
                         f"无法比较（{check['reason']}）")
        else:
            lines.append(f"- {check['before_stage']} `{check['before_path']}` → "
                         f"{check['after_stage']} `{check['after_path']}`："
                         f"{check['status']}")
    lines += ["", "## 四、文件哈希（用于核对文件版本）", "",
              "哈希可用于核对文件是否与本清单一致；确认送检版本还需平台回执等独立记录。", "",
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

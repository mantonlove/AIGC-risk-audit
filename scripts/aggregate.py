#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档级聚合口径（纯 stdlib，可独立测试）。

为什么单独成模块
----------------
聚合口径是**唯一直接对应平台报告字段**的计算：
知网一手字段是 `疑似 AIGC 占全文比 = 疑似 AI 生成文字字数 / 文本总字数`
（见 `references/platforms.md` §1.5）——**按字数加权**。
把它埋在 `score_text.py` 的 detector 循环里，就只能靠端到端跑模型来测，
而模型调用又慢又难在回归里造边界。抽出来之后每个口径都能被单独断言。

⚠️ 边界：平台的"片段"是**自动划分**的，与自然段不同（知网自述）。
本模块把自然段当片段，**不等价**，只能作代理。
"""

import sys

AGG_CHOICES = ("max", "mean", "char_weighted")


def _normalize(scores, weights):
    """把 (分数, 权重) 对齐并按最短截断；权重取非负。

    抽成函数是为了**只留一处**权重处理逻辑 —— 突变测试发现
    `aggregate` 与 `flagged_char_ratio` 里各有一份相同的权重代码，
    于是"权重用反"这个突变有 2 个锚点，runner 直接拒测
    （锚点数必须恰好为 1，否则改的不是以为的那一处）。
    重复的保护逻辑本身就是缺陷：改一处、漏一处，行为会不一致。
    """
    n = min(len(scores), len(weights))
    w = [max(float(x), 0.0) for x in weights[:n]]
    return scores[:n], w


def aggregate(scores, weights=None, agg: str = "char_weighted") -> float:
    """把段落分数聚合成一个文档级数。

    - `max`           最可疑段落（保守；不受段落划分影响）
    - `mean`          段落等权平均（诊断用；一个 20 字过渡段与 200 字论证段同权）
    - `char_weighted` **按字数加权**（与平台 aigcRatio 的口径同构）

    空输入返回 0.0（不是 NaN）：调用方要能直接比较与累加。
    长度与权重长度不一致时按最短截断，避免静默错配。
    """
    if agg not in AGG_CHOICES:
        raise ValueError(f"未知聚合口径 {agg!r}，可选 {AGG_CHOICES}")
    if not scores:
        return 0.0
    if agg == "max":
        return float(max(scores))
    if agg == "mean":
        return float(sum(scores) / len(scores))
    if weights is None:
        raise ValueError("char_weighted 需要 weights（每段的字数）")
    sc, w = _normalize(scores, weights)
    tw = sum(w)
    if tw <= 0:
        # 全部段字数都是 0：退回等权，而不是除零。
        return float(sum(sc) / len(sc)) if sc else 0.0
    return float(sum(s * wi for s, wi in zip(sc, w)) / tw)


def flagged_char_ratio(scores, weights, threshold: float = 0.5) -> tuple[int, int, float]:
    """平台口径的"疑似字数占比"。

    返回 (疑似字数, 总字数, 占比)。判定线默认 0.5 —— 那是知网对**片段**的线
    （"AIGC 值>=0.5，即为疑似 AI 生成"），**不是**文档级达标线
    （后者由学校定，观测到 30% / 40%）。
    """
    sc, w = _normalize(scores, weights)
    total = sum(w)
    flagged = sum(wi for s, wi in zip(sc, w) if s >= threshold)
    return int(flagged), int(total), (flagged / total if total > 0 else 0.0)


def main() -> int:
    """自检：把口径的性质直接演示出来（也给用户一个可复制的算例）。"""
    # 一个 200 字的论证段 0.9，一个 20 字的过渡段 0.1
    scores, weights = [0.9, 0.1], [200, 20]
    print(f"段分数 {scores}，段字数 {weights}")
    for a in AGG_CHOICES:
        print(f"  {a:<14} {aggregate(scores, weights, a):.4f}")
    f, t, r = flagged_char_ratio(scores, weights, 0.5)
    print(f"  疑似字数占比   {r:.1%}（{f}/{t} 字）")
    print()
    print("注意 mean 与 char_weighted 在这个例子上差别很大："
          "段落等权把 20 字的过渡段抬到了与 200 字论证段同权。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

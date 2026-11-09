#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档级聚合口径（纯 stdlib，可独立测试）。

为什么单独成模块
----------------
本模块只实现本地段落分数的数学汇总。所用自然段/非空行划分、汉字权重和
本地模型输出均不能还原知网、学习通或 PaperPass 报告字段。
把它埋在 `score_text.py` 的 detector 循环里，就只能靠端到端跑模型来测，
而模型调用又慢又难在回归里造边界。抽出来之后每个口径都能被单独断言。

⚠️ 边界：片段和权重由调用方传入；结果只反映该调用方采用的本地口径。
"""

import sys

AGG_CHOICES = ("max", "mean", "char_weighted")


def _normalize(scores, weights):
    """核对 (分数, 权重) 一一对应；权重取非负。

    抽成函数是为了**只留一处**权重处理逻辑 —— 突变测试发现
    `aggregate` 与 `flagged_char_ratio` 里各有一份相同的权重代码，
    于是"权重用反"这个突变有 2 个锚点，runner 直接拒测
    （锚点数必须恰好为 1，否则改的不是以为的那一处）。
    重复的保护逻辑本身就是缺陷：改一处、漏一处，行为会不一致。
    """
    if len(scores) != len(weights):
        raise ValueError("分数与权重长度不一致，拒绝静默丢弃片段")
    w = [max(float(x), 0.0) for x in weights]
    return scores, w


def aggregate(scores, weights=None, agg: str = "char_weighted") -> float:
    """把段落分数聚合成一个文档级数。

    - `max`           最可疑段落（保守；不受段落划分影响）
    - `mean`          段落等权平均（诊断用；一个 20 字过渡段与 200 字论证段同权）
    - `char_weighted` **按传入权重加权**（通常使用本地汉字数）

    空输入返回 0.0（不是 NaN）：调用方要能直接比较与累加。
    长度与权重长度不一致时报错，避免静默漏掉片段。
    所有权重为 0 时退回段落等权；这只是计算兜底，不是有意义的字数加权结果。
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
    """按调用方权重计算达到本地阈值的片段占比。

    返回 (本地标记权重, 总权重, 占比)。默认 0.5 只是本地演示标记线，
    不代表任何商业平台或学校的判定标准。
    总权重为 0 时返回 (0, 0, 0.0)；第三项仅是避免除零的哨兵值。
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

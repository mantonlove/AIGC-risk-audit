# 本地编辑实验：已观测结果与可复现范围

**实验日期**：2026-09-17。**分析单位**：同题摘要配对，120 对。**测量对象**：三个本地开源中文检测器的文档分数。此处没有知网、学习通、PaperPass 或维普的直接测量。

## 数据与具体干预

每对文本含一篇标为人类撰写的学术摘要（`H_abs_XXXX`）及 Qwen2.5-1.5B 基于同题生成的 AI 摘要（`B_abs_XXXX__M00`）。人类摘要取自 `suolyer/cnki_summary`，但本项目没有逐篇核验作者身份。检测器是 `d_hello`、`d_cmj`、`d_zhv3`；分数按自然段等权平均，再以每篇摘要为单位比较。每篇约 200 字，因此这里的结果不能代表整篇论文。

| 条件 | 代码实际做的事 | 重要边界 |
|---|---|---|
| A `A_fact` | 从对应人类摘要用正则抽取数字和术语，把缺失项列成一句话，**附在 AI 摘要末尾**。 | 没有检查片段是否能在 AI 文本当前语境中成立，也没有模拟作者把真实依据写回论证。 |
| B `B_meta` | 末尾追加固定的自我设限句。 | 句子是否符合每篇研究的实际限制未核查。 |
| C `C_rhythm` | 删除部分连接词。 | 历史代码的“拆长句”分支把原句切开又原样拼回，实际没有改变句子边界；不能把结果归因于拆句。 |
| D `D_append_ctrl` | 末尾追加另一句通用陈述，作为追加文本的对照。 | 它与 A 只近似匹配长度，措辞和含义仍不同；不能严格分解“信息”与“长度”各自的因果贡献。 |
| E `E_combined` | 依次施加 A 与 B。 | 更长，也混合了两种句式与内容变化。 |

这些是规则化文本操作。A 不等于作者补充真实证据，B 不等于真实的研究局限，C 不等于有效的句式调整。原始文件只存逐条件分数和字数，没有每对文本的不可变标识、文本版本哈希或完整生成日志，故当前复核是**已存分数的复算**，不是完整实验重建。

## 原始分数复算

以 AI 原样组为基线，Δ = 编辑组分数 − AI 原样分数。下表来自 `python3 tests/measure_edits.py --summarize-saved`，直接读取 120 篇逐篇分数；“升分”表示 Δ>0 的篇数。

| 检测器 | AI 原样均值 | A：Δ / 升分 | B：Δ / 升分 | C：Δ / 升分 | D：Δ / 升分 | E：Δ / 升分 |
|---|---:|---:|---:|---:|---:|---:|
| `d_hello` | 0.5642 | −0.1911 / 5 | −0.2632 / 2 | −0.0289 / 6 | −0.0924 / 22 | −0.3291 / 5 |
| `d_cmj` | 0.8704 | −0.0167 / 12 | +0.0693 / 112 | −0.0068 / 11 | −0.0158 / 8 | +0.0651 / 80 |
| `d_zhv3` | 0.9122 | −0.0072 / 21 | +0.0461 / 74 | +0.0039 / 19 | +0.0003 / 42 | +0.0506 / 63 |

A 的三个**均值**均下降，但每个检测器上仍有单篇升分。B 与 E 在 `d_hello` 上降分，在另两个模型上升分。D 自身也让 `d_hello` 的平均分下降，因此 A 的下降不能全部归于被抽取的术语或数字；现有设计也不足以断言“一半效应来自追加，一半来自具体信息”。C 的结果只支持对该连接词删除操作的描述。

这些分数可用来研究**当前本地模型对此批文本的反应**。没有任何一项能证明真实作者补写论证会降低检测分数；也不能据此选择对商业平台有效的改写策略。

## 阈值分析为何不作主结论

历史文档曾用同一批人类摘要的第 95 百分位定阈值，又在这批摘要上报告“5% FPR”；这是样本内构造值。后来还列出 60/60 人类拆分的留出结果，但当前仓库没有保存拆分索引或随机种子，无法从已存结果重建相同表格。因此这里保留可逐篇复算的连续分数，不把历史留出检出率、McNemar p 值或多重比较结果作为当前可复现的主要证据。60 篇留出样本上观察到的 2%～12% 波动，也不能单独证明“5% 校准不可能”。

与本实验不同，`references/measured-baseline.md` 的人类基线使用了另存的 dev/heldout 标签；那份文档回答的是**真实人类文档的误判率**，不能拿来替代这里的编辑效应。

## 文件与命令

- A/B/C 初次分数：`/Users/manton/CC/skills/aigc-risk-audit/out/edit_effects.json`。
- D 对照与共有条件：`/Users/manton/CC/projects/dsh开发/aigc-bench/out2/edit_probe_ctrl.json`。
- E 组合及 A–E 全部条件：`/Users/manton/CC/projects/dsh开发/aigc-bench/out2/edit_probe_combined.json`。对照文件与组合文件共有条件的 120 条分数逐项一致。
- 规则化实验代码：`tests/measure_edits.py` 重跑 A/B/C；`/Users/manton/CC/projects/dsh开发/aigc-bench/harness/edit_probe.py` 包含 A–E 当前实现。要从模型重新打分，需要本地语料、权重和相应解释器；只读复算不需要模型。
- 另一组 LLM 改写分数：`/Users/manton/CC/projects/dsh开发/aigc-bench/out2/full_score.json`。其改写条件与本页 A–E 操作不同，也不是 `scripts/reduce_aigc.py` 当前流程的前瞻验证。

本页表格复算时，三份输入文件的 SHA-256 分别为：

| 文件 | SHA-256 |
|---|---|
| `out/edit_effects.json` | `66b03672a368973f8d917d5b7428d52e8ca7d079db9c10fe276d66ba354e79a6` |
| `out2/edit_probe_ctrl.json` | `a2119f056a8e0a29ca81bf6e0453e852cd3b3e4585de296ec88da264942b90e2` |
| `out2/edit_probe_combined.json` | `46dca48a2de711f28833efe66ee222118684cfdca139710957d073de9f73fdd5` |

用 `shasum -a 256 <文件路径>` 核对本地副本。哈希只固定本次复算所用版本，不能补回未保存的原始文本标识与生成日志；文件哈希不同则应重新复算并更新结论。

```bash
cd /Users/manton/CC/skills/aigc-risk-audit
python3 tests/measure_edits.py --summarize-saved
```

重新运行 A/B/C（会调用本地模型，不生成 D/E）：

```bash
python3 tests/measure_edits.py \
  --bench /Users/manton/CC/projects/dsh开发/aigc-bench \
  --python /Users/manton/.venvs/aigc/bin/python \
  --limit 120 --out out/edit_effects_rerun.json
```

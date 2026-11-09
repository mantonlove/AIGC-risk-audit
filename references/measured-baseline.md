# 本地开源检测器的人类文本基线

**测量日期**：2026-09-13。**历史结果**：`/Users/manton/CC/projects/dsh开发/aigc-bench/out/FINAL_MEASURED.json`。以下“误判率”是**一组人类文档中被二元判为 AI 的比例**，不是某篇论文中被标记的字数比例，也不是商业平台报告的“AI 率”。

## 语料与阈值

基线使用三个本地开源中文模型：`d_hello`（Hello-SimpleAI）、`d_cmj`（mokawa3018）、`d_zhv3`（yuchuantian）。对 258 条 dev 人类文本选择名义 5% 假阳性率的阈值，再在未参与该步骤的 heldout 文本上观察检出比例。

| 组别 | 总数及拆分 | 来源与边界 |
|---|---|---|
| H_abs | 240；dev 120、heldout 120 | `suolyer/cnki_summary` 中标记为人类撰写的学术摘要；本项目未逐篇核验作者身份。 |
| H_qa | 240；dev 138、heldout 102 | HC3-Chinese 中标记为人类撰写的问答。 |
| G_gpt | 240 | HC3 中的旧版 ChatGPT 回答；`d_hello` 的模型卡显示 HC3 训练数据重叠，故不用于估计泛化能力。 |

单篇文档的分数在当时按段落等权平均。下表是历史 JSON 中 `detection_rate_at_calib_thr` 字段；文档比例的分母分别为 120 和 102。

| 本地模型 | 阈值 | H_abs heldout 文档误判率 | H_qa heldout 文档误判率 |
|---|---:|---:|---:|
| `d_hello` | 0.0127 | 16/120 = 13.33% | 11/102 = 10.78% |
| `d_cmj` | 0.6738 | 10/120 = 8.33% | 2/102 = 1.96% |
| `d_zhv3` | 0.9901 | 8/120 = 6.67% | 0/102 = 0% |

这些观察值表明：在特定语料和阈值下，标为人类的摘要也可能被本地模型检出。dev 阈值和 heldout 误判率不相同，有限样本波动与语料差异都可能影响结果；仅凭这些数值不能断言“5% 校准不可能”。固定 0.5 阈值下，`d_zhv3` 对 H_abs heldout 的观察检出率为 31.67%，进一步说明阈值选择会改变判定。

**不能把 13.33% 与某一篇论文的 10% 界值比较。** 前者的分子是 16 篇文档、分母是 120 篇文档；后者如果是“疑似字数占比”，分子和分母都来自同一篇论文的文本长度。两个百分比代表不同事件。

## 与后续编辑实验的关系

历史 `harness/finalize.py` 在 2026-09-13 收尾时只发现 2 个完整改写配对块，因此该脚本不估计编辑效应。后来另生成了 120 对摘要的规则化 A/B/C/D/E 实验；具体操作与已存分数见 `references/edit-effects.md`。这些实验没有验证作者补写真实论据的效果，也没有验证 `scripts/reduce_aigc.py` 的当前流程或商业平台结果。

若要用**当前代码**重新测量基线，在回测工程中执行：

```bash
cd /Users/manton/CC/projects/dsh开发/aigc-bench
/Users/manton/.venvs/aigc/bin/python harness/finalize.py \
  --out out/FINAL_MEASURED_current.json
```

该命令需要本地语料和模型权重。`harness/detectors.py` 已从旧的 510-token
截断改为覆盖长文的分窗评分，所以**当前重跑采用新评分口径，不保证复现上表的旧值，
不能直接用新旧差异解释模型效果变化**。命令将结果写到新文件，保留
`out/FINAL_MEASURED.json` 历史副本。`finalize.py` 现已按
`calibrate_threshold()` 的 `(threshold, status)` 返回值处理校准失败。

## 文风统计量的边界

`scripts/style_profile.py` 提供句长变异、字符 3-gram 重复等表面统计量。阈值是按学术摘要语料设置的；脚本输出不能判定作者身份，也不能预测任何检测器的分数。它在问答、课程论文或其他文体上使用时，应重新标定并在独立样本上验证。

旧版本文列过多组 `style_profile.py` 触发率，数值与当前脚本注释中的验证表并不一致，且仓库没有保存对应的逐篇指标与拆分索引。因此本页不再把这些触发率作为可复算的效能主张；需要定量引用时，应先保留原始指标、版本和独立验证集。

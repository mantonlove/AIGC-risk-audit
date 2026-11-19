# AIGC risk audit · 中文论文 AIGC 检测争议与写作审查

**简体中文** ｜ [English](README.en.md) ｜ ![tests](https://github.com/mantonlove/AIGC-risk-audit/actions/workflows/tests.yml/badge.svg)

一套**审计**工具，不是"降 AI 率"改写器。

它做四件事：判断一处高分为何可疑、指出文里哪些论断缺依据、把作者手上的过程材料整理成可提交的清单、以及在只有公开资料时说明某个平台的字段到底是怎么算的。

**它不预测知网、维普、PaperPass、学习通的百分比，也不承诺达标。** 本地分数只用来决定"哪些段落值得人工看一眼"。

## 快速开始（不需要任何模型）

只装 Python 标准库即可运行诊断、核查、存证、文风统计与规则改写：

```bash
git clone https://github.com/mantonlove/AIGC-risk-audit.git
cd AIGC-risk-audit

python3 scripts/diagnose.py 论文.txt        # 十项写作风险体检
python3 scripts/anchor.py 论文.txt          # 空洞指代、缺依据论断、分句缺主语率
python3 scripts/evidence.py 材料目录         # 五类作者证据清点 + MANIFEST + SHA-256
python3 scripts/style_profile.py 论文.txt   # 句长 CV、3-gram 重复、连接词密度等
python3 scripts/reduce_rules.py 论文.txt --no-score --out 候选稿.txt
python3 tests/regression.py                 # 70/70
```

`reduce_rules.py --no-score` 按 split→删连接词→词表压缩的顺序各施加一次，改全部正文段落，**不择优、不回退**——它不知道改完是好是坏，必须作者逐段通读。

## 需要本地模型的两个入口

`score_text.py` 与 `reduce_rules.py` 的打分模式需要三个开源检测器权重（约 1.2 GB）与本机推理环境：

| 检测器 | 仓库 |
|---|---|
| `d_hello` | Hello-SimpleAI/chatgpt-detector-roberta-chinese |
| `d_cmj` | mokawa3018/cmj-chinese-aigc-text-detector |
| `d_zhv3` | yuchuantian/AIGC_detector_zhv3 |

把它们放到 `data/models/<owner>__<repo>/`，或用 `AIGC_BENCH_HOME` 指向回测工程目录。模型只在被调用时于进程内加载，不常驻。

## 能力

| 入口 | 作用 | 需要模型 |
|---|---|---|
| `diagnose.py` | 句长节奏、段落节奏、连接词模板化、空泛指代、万能开头、商业套话、作者在场标记、具体性锚点、对称并列、句首重复 | 否 |
| `anchor.py` | 8 类空洞指代 + 分句缺主语率（按人类摘要分位分档） | 否 |
| `evidence.py` | 过程文件／版本／沟通／素材来源／送检记录五类清点 + 哈希清单 | 否 |
| `style_profile.py` | 句长 CV、3-gram 重复、连接词密度、破折号冒号密度、词汇多样性、三段式 | 否 |
| `reduce_rules.py` | 确定性算子改写；`--no-score` 无需模型 | 可选 |
| `score_text.py` | 三个开源检测器的段落级分数、字数加权聚合 | 是 |
| `reduce_aigc.py` | LLM 改写（实测无稳定降分，保留作对照） | 是 |

## 已经实测掉的"技巧"

`references/direction-control.md` 与 `references/operator-effects.md` 记录了几组对照实验，结论对任何"降 AIGC"的说法都先过一关：

| 做法 | 实测 |
|---|---|
| 让大模型按七条"去 AI 味"提示词重写 | 分数**升 +0.27**，87% 的篇变差；与"随便换个说法"无显著差别 |
| 反向指令对照 | 不自对称 —— 指令方向不是检测器共有的轴 |
| 扩词表 | 剂量饱和在 4.8%，扩表不是杠杆（156 对与 291 对结果几乎相同） |
| 反向词表（改得更书面） | 同样降分，斜率同量级 |
| 随机字符替换 | 降分是正向词表的两倍 —— 降分主要来自"改坏了" |
| 长短句交替 | 抬高句长 CV 无可测效应；把句长**拉齐**反而降分 |
| 删标题改排版 | 能改报告数字，属度量假象（PaperPass 现网对照） |

唯一有正效应的是**作者补真实细节**，但那属于写作，不是算子。

## 边界

- 本地三个检测器未经任何商业平台校准，`--threshold 0.5` 只是本地显示用默认值。
- 真实人类摘要也会被判高：`d_zhv3` 在 2,560 篇真实 CNKI 摘要上有 **23.8%** ≥ 0.5；留出集文档级误判率 6.7%–13.3%。
- 分数下降不等于文本更像人写；`anchor.py` 的缺主语率经 616 篇标定，人类 5.63 与 AI 5.02（每百分句）**无差异**，只能用于自查行文。
- 专利只证明技术方案被披露，不等于现行产品采用了它。
- 不给零宽字符、隐藏文本、图片化正文、故意错字、同义词堆砌、回译等对抗建议。

## 目录

```
SKILL.md                 入口与执行流程
references/              平台机制、实测基线、算子效应、方向对照、申诉路径
scripts/                 12 个脚本，多数纯标准库
tests/                   regression.py 70 条；mutation.py 突变测试
evals/                   触发与提示词用例
.github/workflows/       每次 push 跑回归（3.11–3.13，Ubuntu + macOS）
```

## 许可

MIT，见 [LICENSE](LICENSE)。引用其中的实测数字时请连同 `references/` 里的口径与边界一起引用。

# AIGC risk audit · auditing AI-detection disputes in Chinese academic writing

[简体中文](README.md) ｜ **English** ｜ ![tests](https://github.com/mantonlove/AIGC-risk-audit/actions/workflows/tests.yml/badge.svg)

An **audit** toolkit, not a "lower your AI score" rewriter.

It does four things: explain why a high score is suspicious in the first place, point out which claims in the text have no support, turn the author's own process material into a submittable inventory, and — when only public material exists — explain how a given platform's reported numbers are actually computed.

**It does not predict CNKI / VIP / PaperPass / Chaoxing percentages, and does not promise any target.** Local scores exist only to decide which paragraphs are worth a human read.

## Quick start (no models required)

Diagnosis, anchoring, evidence inventory, style statistics and rule-based rewriting all run on the Python standard library alone:

```bash
git clone https://github.com/mantonlove/AIGC-risk-audit.git
cd AIGC-risk-audit

python3 scripts/diagnose.py paper.txt          # ten writing-risk checks
python3 scripts/anchor.py paper.txt            # vague reference, unsupported claims, subjectless clauses
python3 scripts/evidence.py materials/         # five evidence classes + MANIFEST + SHA-256
python3 scripts/style_profile.py paper.txt     # sentence-length CV, 3-gram repetition, connective density
python3 scripts/reduce_rules.py paper.txt --no-score --out draft.txt
python3 tests/regression.py                    # 70/70
```

`reduce_rules.py --no-score` applies split → drop connectives → lexicon compression once each, across every body paragraph, with **no scoring, no selection, no reversion** — it has no idea whether the result got better or worse, so the author must read every paragraph.

## The two entry points that need local models

`score_text.py` and the scoring mode of `reduce_rules.py` need three open detector checkpoints (~1.2 GB) plus a local inference environment:

| Detector | Repository |
|---|---|
| `d_hello` | Hello-SimpleAI/chatgpt-detector-roberta-chinese |
| `d_cmj` | mokawa3018/cmj-chinese-aigc-text-detector |
| `d_zhv3` | yuchuantian/AIGC_detector_zhv3 |

Place them under `data/models/<owner>__<repo>/`, or point `AIGC_BENCH_HOME` at a benchmark checkout. Models load inside the process only while a command runs; nothing stays resident.

## Capabilities

| Entry point | What it does | Models |
|---|---|---|
| `diagnose.py` | sentence rhythm, paragraph rhythm, templated connectives, vague reference, boilerplate openers, corporate jargon, authorial markers, concreteness anchors, symmetric parallelism, repeated sentence openers | no |
| `anchor.py` | eight classes of hollow reference + subjectless-clause rate, banded against human quantiles | no |
| `evidence.py` | inventory of process notes / versions / correspondence / sources / submission records, with hashes | no |
| `style_profile.py` | sentence-length CV, 3-gram repetition, connective density, dash-and-colon density, lexical diversity, rule-of-three | no |
| `reduce_rules.py` | deterministic operator rewriting; `--no-score` needs no models | optional |
| `score_text.py` | paragraph-level scores from the three open detectors, character-weighted aggregate | yes |
| `reduce_aigc.py` | LLM rewriting (no stable reduction in measurements; kept as a control) | yes |

## Techniques already ruled out by measurement

`references/direction-control.md` and `references/operator-effects.md` document several controlled comparisons. Any claim about "reducing AIGC" has to pass them first:

| Technique | Measured result |
|---|---|
| Have an LLM rewrite under seven "de-AI-ify" instructions | score **rises by +0.27**; 87% of documents get worse; no significant difference from "just paraphrase" |
| Reverse-instruction control | not antisymmetric — instruction direction is not an axis the detectors share |
| Expanding the lexicon | dose saturates at 4.8%; 156 pairs and 291 pairs give nearly identical results |
| Reverse lexicon (make it more formal) | reduces scores just as well, same slope per unit dose |
| Random character substitution | reduces scores twice as fast as the real lexicon — the drop comes from "damaging the text" |
| Alternating long and short sentences | raising sentence-length CV has no measurable effect; flattening it actually lowers scores |
| Deleting headings or changing layout | changes the reported number, a measurement artifact (PaperPass live comparison) |

The only positive effect comes from **the author adding real detail** — that is writing, not an operator.

## Boundaries

- The three local detectors are not calibrated against any commercial platform. `--threshold 0.5` is a local display default only.
- Real human abstracts also get flagged: `d_zhv3` marks **23.8%** of 2,560 genuine CNKI abstracts at ≥ 0.5; held-out document-level false-positive rates are 6.7%–13.3%.
- A lower score does not mean the text reads more human. Across 616 documents the subjectless-clause rate is 5.63 per hundred clauses for humans and 5.02 for AI — **no difference** — so it is a style self-check, nothing more.
- A patent proves a technique was disclosed; it does not prove the shipping product uses it.
- No advice on zero-width characters, hidden text, image-only body text, deliberate typos, synonym stuffing, or round-trip translation.

## Layout

```
SKILL.md                 entry points and workflow
references/              platform mechanics, measured baselines, operator effects, direction controls, appeals
scripts/                 12 scripts, mostly standard library only
tests/                   regression.py (70 cases); mutation.py mutation tests
evals/                   trigger and prompt cases
.github/workflows/       regression on every push (3.11–3.13, Ubuntu + macOS)
```

## License

MIT, see [LICENSE](LICENSE). If you cite the measured numbers, cite them together with the scope and limitations recorded under `references/`.

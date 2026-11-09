#!/usr/bin/env python3
"""核心管线回归：不加载模型权重，不调用商业平台。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

import _bench  # noqa: E402
import aggregate  # noqa: E402
import reduce_aigc  # noqa: E402
import score_text  # noqa: E402

# 外部回测工程和 torch/transformers 权重依赖不随 skill 打包。
# 本机应运行全部用例；迁移环境缺依赖时明确标记 skip，不能导入崩溃或伪称通过。
EXTERNAL_DEPENDENCY_ERROR = None
try:
    configured = os.environ.get("AIGC_BENCH_HOME")
    BENCH = (Path(configured) if configured else Path(_bench.find_bench())) / "harness"
    if not BENCH.is_dir():
        raise ImportError(f"回测工程不存在：{BENCH}")
    sys.path.insert(0, str(BENCH))
    import corpus  # noqa: E402
    import detectors  # noqa: E402
    import rewriter  # noqa: E402
except (ImportError, SystemExit) as exc:
    EXTERNAL_DEPENDENCY_ERROR = str(exc)


class FakeTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, **kwargs):
        ids = [ord(ch) for ch in text]
        if kwargs.get("add_special_tokens", True):
            ids = [101, *ids, 102]
        return {"input_ids": ids}

    def build_inputs_with_special_tokens(self, ids):
        return [101, *ids, 102]

    def pad(self, data, padding=True, return_tensors="pt"):
        import torch
        rows = data["input_ids"]
        width = max(len(row) for row in rows)
        return {"input_ids": torch.tensor([row + [0] * (width - len(row)) for row in rows]),
                "attention_mask": torch.tensor([[1] * len(row) + [0] * (width - len(row))
                                                for row in rows])}


class FakeModel:
    def __init__(self):
        self.windows = []

    def __call__(self, **enc):
        import torch
        self.windows.extend(enc["input_ids"].tolist())
        tail = (enc["input_ids"] == ord("z")).any(dim=1).float()
        return type("Result", (), {"logits": torch.stack((5 * (1 - tail), 5 * tail), dim=1)})()


class PipelineHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if EXTERNAL_DEPENDENCY_ERROR:
            raise unittest.SkipTest("需要外部 aigc-bench/harness 与推理依赖："
                                    + EXTERNAL_DEPENDENCY_ERROR)

    def test_offset_replacement_preserves_every_unselected_character(self):
        body = "基于2023年样本开展分析，作者逐项核对每一份记录，并说明观察到的范围与限制。"
        text = "# 标题\r\n\r\n  " + body + "  \r\n| 表格 | 这一行包含很多很多汉字但不能自动改写 |\r\n公式 y=2\n结论。\n"
        spans = corpus.line_spans(text)
        self.assertEqual(len(spans), 5)
        eligible = corpus.editable_line_spans(text)
        self.assertEqual([raw.strip() for _, _, raw in eligible], [body])
        rw = rewriter.Rewriter.__new__(rewriter.Rewriter)
        rw.sb = type("Board", (), {"score": lambda self, value, agg="max": {"test": 0.9}})()
        rw.reduce_paragraph = lambda p, target, max_iters, verbose, agg: (
            p.replace("作者", "研究者"), 0.2, [])
        result = rw.reduce(text, verbose=False)
        self.assertEqual(result["text"], text.replace("作者", "研究者", 1))
        self.assertEqual(sum(record.get("changed", False) for record in result["paragraphs"]), 1)

    def test_long_heading_table_formula_and_code_remain_uneditable(self):
        long = "这是一整行超过三十个汉字的结构说明文字，应当在自动候选阶段完整保留原有内容。"
        text = ("# " + long + "\n| " + long + " | 2023 |\n"
                "$$y=2023+2024$$ " + long + "\n"
                "```python\n# " + long + "\n```\n" + long + "\n")
        self.assertEqual([raw.strip() for _, _, raw in corpus.editable_line_spans(text)], [long])

    def test_fact_gate_rejects_changed_number_citation_comparison_and_negation(self):
        source = ("基于2023年纳入的120篇文献[4]，张三（2023）报告了12.3%的变化；"
                  "本文计算得到p<0.01，但结果不显著，样本均值下降。")
        self.assertTrue(rewriter.validate_candidate(source, source)[0])
        corruptions = [
            source.replace("2023年", "2024年"),
            source.replace("120篇", "121篇"),
            source.replace("12.3%", "13.3%"),
            source.replace("[4]", "[5]"),
            source.replace("张三", "李四"),
            source.replace("p<0.01", "p>0.01"),
            source.replace("不显著", "显著"),
            source.replace("下降", "上升"),
            source.replace("结果不显著", "结果错误"),
        ]
        for altered in corruptions:
            with self.subTest(altered=altered):
                self.assertFalse(rewriter.validate_candidate(source, altered)[0])
        swapped = ("实验组120人、对照组80人均于2023年完成随访，"
                   "作者据此报告两组的变化方向和研究局限。")
        self.assertFalse(rewriter.validate_candidate(
            swapped, swapped.replace("实验组120人、对照组80人", "实验组80人、对照组120人"))[0])

    def test_invalid_candidate_is_rejected_before_scoring_even_when_score_would_drop(self):
        source = "根据2023年纳入的120篇资料，本研究逐项核查记录并报告主要结果与适用范围。"
        bad = source.replace("120篇", "121篇")

        class Board:
            def __init__(self):
                self.calls = []

            def worst(self, value, agg="max"):
                self.calls.append((value, agg))
                return (0.01 if value == bad else 0.9), {}

        rw = rewriter.Rewriter.__new__(rewriter.Rewriter)
        rw.sb = Board()
        rw.candidates = lambda text, seed: {"corrupt": bad}
        out, score, history = rw.reduce_paragraph(source, 0.2, max_iters=1, agg="mean")
        self.assertEqual(out, source)
        self.assertEqual(score, 0.9)
        self.assertEqual(rw.sb.calls, [(source, "mean")])
        self.assertTrue(any("rejected" in step for step in history))

    def test_no_fact_anchor_fails_closed_without_generating_candidates(self):
        source = "这一段只是概括性的背景介绍，缺少可核查的数量、引文或具体实验依据。"
        rw = rewriter.Rewriter.__new__(rewriter.Rewriter)
        rw.sb = type("Board", (), {"worst": lambda self, value, agg="max": (0.9, {})})()
        rw.candidates = lambda text, seed: self.fail("不应生成无法核验的候选")
        out, _, history = rw.reduce_paragraph(source, 0.2)
        self.assertEqual(out, source)
        self.assertEqual(history[-1]["action"], "skip")

    def test_detector_scores_tail_tokens_and_keeps_input_order(self):
        det = detectors.Detector.__new__(detectors.Detector)
        det.tokenizer = FakeTokenizer()
        det.model = FakeModel()
        det.device = "cpu"
        det.ai_index = 1
        det.max_tokens = 10
        det.stride = 2
        scores = det.score_batch(["a" * 20 + "z" * 10, "a" * 12], batch_size=2)
        self.assertGreater(scores[0], 0.25)  # 旧版静默截断后接近 0
        self.assertLess(scores[1], 0.02)
        self.assertTrue(any(ord("z") in window for window in det.model.windows))
        import math
        low = 1 / (1 + math.exp(5))
        high = 1 - low
        self.assertAlmostEqual(scores[0], (20 * low + 10 * high) / 30, places=5)

    def test_scoreboard_mean_includes_short_nonempty_lines(self):
        class Detector:
            def score_paragraphs(self, paragraphs):
                self.seen = paragraphs
                return [0.1, 0.9]

        det = Detector()
        board = rewriter.ScoreBoard.__new__(rewriter.ScoreBoard)
        board.dets = {"fake": det}
        value = board.score("标题\n这是一段包含更多汉字的正文。", agg="mean")
        self.assertEqual(det.seen[0], "标题")
        self.assertEqual(value, {"fake": 0.5})
        self.assertEqual(board.score("标题\n这是一段包含更多汉字的正文。", agg="max"),
                         {"fake": 0.9})

    def test_requested_detector_failure_does_not_fall_back_to_one_model(self):
        class Detector:
            def __init__(self, name, path, device=None):
                if name == "missing":
                    raise OSError("模型文件损坏")

        with patch.object(detectors, "Detector", Detector):
            with self.assertRaisesRegex(SystemExit, "missing.*OSError"):
                rewriter.ScoreBoard("", ["working", "missing"])

    def test_score_cli_rejects_partial_detector_path_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "in.txt"
            source.write_text("标题", encoding="utf-8")
            with (patch.object(score_text._bench, "find_bench", return_value=d),
                  patch.object(score_text._bench, "ensure_venv"),
                  patch.object(score_text._bench, "ensure_importable"),
                  patch.object(score_text._bench, "detector_paths", return_value=["only-one"]),
                  patch.object(sys, "argv", ["score_text.py", str(source),
                                           "--detectors", "first", "second"])):
                with self.assertRaisesRegex(SystemExit, "路径不齐"):
                    score_text.main()

    def test_path_resolver_rejects_one_missing_model_even_if_another_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "data" / "models" / "working").mkdir(parents=True)
            with self.assertRaisesRegex(SystemExit, "missing"):
                reduce_aigc._bench.detector_paths(d, ["working", "missing"])

    def test_weighted_aggregation_rejects_missing_score_or_weight(self):
        with self.assertRaisesRegex(ValueError, "长度不一致"):
            aggregate.aggregate([0.9, 0.1], [100], "char_weighted")
        with self.assertRaisesRegex(ValueError, "长度不一致"):
            aggregate.flagged_char_ratio([0.9, 0.1], [100], 0.5)

    def test_score_text_cli_reports_all_lines_and_honest_chinese_weights(self):
        body = "基于具体记录逐项核查得到的结论，需要作者进一步说明样本与方法的边界。"
        text = "标题\n" + body + "\nx = y + 2\n"

        class Detector:
            def __init__(self, *args):
                pass

            def score_paragraphs(self, paragraphs):
                self.seen = paragraphs
                self_test.assertEqual(paragraphs, ["标题", body, "x = y + 2"])
                return [0.9, 0.1, 0.9]

        self_test = self
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "in.txt"
            source.write_text(text, encoding="utf-8")
            output = io.StringIO()
            with (patch.object(score_text._bench, "find_bench", return_value=d),
                  patch.object(score_text._bench, "ensure_venv"),
                  patch.object(score_text._bench, "ensure_importable"),
                  patch.object(score_text._bench, "detector_paths", return_value=["fake"]),
                  patch.object(detectors, "Detector", Detector),
                  patch.object(sys, "argv", ["score_text.py", str(source),
                                           "--detectors", "fake", "--json"]),
                  contextlib.redirect_stdout(output)):
                self.assertEqual(score_text.main(), 0)
        data = json.loads(output.getvalue())
        row = data["detectors"]["fake"]
        self.assertEqual(data["n_para"], 3)
        self.assertEqual(row["paragraph_chars"], [2, corpus.n_chars(body), 0])
        self.assertEqual(row["flagged_chars"], 2)  # 公式只有 ASCII，不虚增汉字权重
        self.assertEqual(row["flagged_char_ratio"], round(2 / (2 + corpus.n_chars(body)), 4))
        self.assertEqual(data["scoring_version"], "token-window-v2/all-nonblank-lines")

    def test_reduce_cli_passes_mean_to_initial_and_final_scoring(self):
        text = "根据2023年纳入的120篇资料，本研究逐项核查记录并报告主要结果与适用范围。\n"
        calls = []

        class Board:
            def __init__(self, *args):
                pass

            def score(self, value, agg="max"):
                calls.append(("score", agg))
                return {"fake": 0.4 if agg == "mean" else 0.9}

            def worst(self, value, agg="max"):
                calls.append(("worst", agg))
                return 0.4, {}

        class Writer:
            def __init__(self, *args):
                pass

            def reduce_paragraph(self, value, target, max_iters, verbose, agg):
                calls.append(("rewrite", agg))
                return value, 0.4, []

        with tempfile.TemporaryDirectory() as d:
            source, out, report = (Path(d) / n for n in ("in.txt", "out.txt", "report.json"))
            source.write_text(text, encoding="utf-8")
            with (patch.object(reduce_aigc._bench, "find_bench", return_value=d),
                  patch.object(reduce_aigc._bench, "ensure_venv"),
                  patch.object(reduce_aigc._bench, "ensure_importable"),
                  patch.object(reduce_aigc._bench, "detector_paths", return_value=["fake"]),
                  patch.object(reduce_aigc._bench, "generator_path", return_value="fake-model"),
                  patch.object(rewriter, "ScoreBoard", Board),
                  patch.object(rewriter, "Rewriter", Writer),
                  patch.object(sys, "argv", ["reduce_aigc.py", str(source), "--out", str(out),
                                           "--report", str(report), "--agg", "mean", "--quiet",
                                           "--detectors", "fake"]),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(reduce_aigc.main(), 0)
            self.assertEqual(out.read_text(encoding="utf-8"), text)
            self.assertTrue(calls)
            self.assertEqual({agg for _, agg in calls}, {"mean"})
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["score_before"], 0.4)
            self.assertEqual(data["score_after"], 0.4)

    def test_real_rewriter_path_keeps_offsets_and_report_aggregation(self):
        body = "基于2023年纳入的120篇资料，本研究逐项核查记录并报告主要结果与适用范围。"
        candidate = body.replace("逐项核查", "逐一核查")
        original = "# 摘要\r\n\r\n" + body + "\r\n"

        class Detector:
            def __init__(self, name, path, device=None):
                self.name = name

            def score_paragraphs(self, paragraphs):
                return [0.9 if "逐项核查" in para else 0.1 for para in paragraphs]

        class BatchLLM:
            def __init__(self, path):
                self.path = path

            def batch_chat(self, pairs, max_new_tokens=420, seed=None):
                return [candidate] * len(pairs)

        with tempfile.TemporaryDirectory() as directory:
            source, output, report = (Path(directory) / name for name in
                                      ("in.txt", "out.txt", "report.json"))
            source.write_text(original, encoding="utf-8", newline="")
            with (patch.object(reduce_aigc._bench, "find_bench", return_value=directory),
                  patch.object(reduce_aigc._bench, "ensure_venv"),
                  patch.object(reduce_aigc._bench, "ensure_importable"),
                  patch.object(reduce_aigc._bench, "detector_paths", return_value=["fake"]),
                  patch.object(reduce_aigc._bench, "generator_path", return_value="fake-llm"),
                  patch.object(detectors, "Detector", Detector),
                  patch("batch_llm.BatchLLM", BatchLLM),
                  patch.object(sys, "argv", ["reduce_aigc.py", str(source), "--out", str(output),
                                           "--report", str(report), "--agg", "mean",
                                           "--max-iters", "1", "--quiet", "--detectors", "fake"]),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(reduce_aigc.main(), 0)
            self.assertEqual(output.read_text(encoding="utf-8", newline=""),
                             original.replace(body, candidate, 1))
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["score_before"], 0.5)
            self.assertEqual(data["score_after"], 0.1)
            self.assertEqual(data["agg"], "mean")
            self.assertEqual([item["changed"] for item in data["paragraphs"]],
                             [False, True])

    def test_reduce_cli_early_exit_writes_default_original_and_report(self):
        text = "标题\n公式 x=2\n"

        class Board:
            def __init__(self, *args):
                pass

            def score(self, value, agg="max"):
                return {"fake": 0.1}

        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "in.txt"
            report = Path(d) / "report.json"
            source.write_text(text, encoding="utf-8")
            with (patch.object(reduce_aigc._bench, "find_bench", return_value=d),
                  patch.object(reduce_aigc._bench, "ensure_venv"),
                  patch.object(reduce_aigc._bench, "ensure_importable"),
                  patch.object(reduce_aigc._bench, "detector_paths", return_value=["fake"]),
                  patch.object(rewriter, "ScoreBoard", Board),
                  patch.object(sys, "argv", ["reduce_aigc.py", str(source),
                                           "--detectors", "fake", "--report", str(report)]),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(reduce_aigc.main(), 0)
            self.assertEqual((Path(d) / "in_候选核查.txt").read_text(encoding="utf-8"), text)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["score_before"], data["score_after"])
            self.assertIn("原文保留", data["note"])


if __name__ == "__main__":
    unittest.main()

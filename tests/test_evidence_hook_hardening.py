"""回归检查：存证清单不收录自身，触发器只响应学术 AIGC 需求。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
EVIDENCE = SKILL / "scripts" / "evidence.py"
HOOK = SKILL / "scripts" / "aigc_trigger_hook.py"


class EvidenceHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aigc-evidence-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, content="材料", mtime=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def run_evidence(self, *extra):
        return subprocess.run(
            [sys.executable, str(EVIDENCE), str(self.root), *map(str, extra)],
            capture_output=True, text=True, check=False)

    def test_default_output_stays_out_of_repeated_manifest(self):
        self.put("论文-v1.md")
        self.put("原始数据.csv", "a,b\n1,2")
        first = self.run_evidence()
        self.assertEqual(first.returncode, 0, first.stderr)
        manifest = self.root / "作者证明包" / "manifest.json"
        before = json.loads(manifest.read_text(encoding="utf-8"))

        second = self.run_evidence()
        self.assertEqual(second.returncode, 0, second.stderr)
        after = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(before["files"], after["files"])
        self.assertEqual(after["files_total"], 2)
        self.assertFalse(any("作者证明包" in f["path"] for f in after["files"]))

    def test_nested_custom_output_stays_out_of_repeated_manifest(self):
        self.put("论文-v1.md")
        output = self.root / "存证" / "导出"
        for _ in range(2):
            result = self.run_evidence("--out", output)
            self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([f["path"] for f in report["files"]], ["论文-v1.md"])

    def test_mtime_inversion_is_a_review_clue_not_authorship_verdict(self):
        base = 1_700_000_000
        self.put("原始数据.csv", mtime=base + 200)
        self.put("论文-v1.md", mtime=base)
        self.put("送检报告.md", mtime=base + 300)
        result = self.run_evidence("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIs(report["timeline_consistent"], False)
        self.assertEqual(report["timeline_checks"][0]["status"], "needs_review")
        self.assertIn("不能证明", report["timeline_note"])

    def test_ordered_mtime_is_not_presented_as_proof(self):
        base = 1_700_000_000
        self.put("原始数据.csv", mtime=base)
        self.put("论文-v1.md", mtime=base + 200)
        self.put("送检报告.md", mtime=base + 400)
        result = self.run_evidence("--json")
        report = json.loads(result.stdout)
        self.assertIs(report["timeline_consistent"], True)
        self.assertIn("顺序正常不能证明写作真实性", report["timeline_note"])

    def test_missing_stages_are_unknown(self):
        self.put("提纲.md")
        result = self.run_evidence("--json")
        report = json.loads(result.stdout)
        self.assertIsNone(report["timeline_consistent"])
        self.assertEqual([item["status"] for item in report["timeline_checks"]],
                         ["insufficient_stages", "insufficient_stages"])

    def test_one_multilabel_file_cannot_supply_all_timeline_stages(self):
        self.put("原始数据-v1-送检.md")
        result = self.run_evidence("--json")
        report = json.loads(result.stdout)
        self.assertIsNone(report["timeline_consistent"])
        self.assertTrue(all(item["status"] == "insufficient_stages"
                            for item in report["timeline_checks"]))

    def test_partial_positive_order_is_unknown_with_explicit_missing_stage(self):
        base = 1_700_000_000
        self.put("论文-v1.md", mtime=base)
        self.put("送检报告.md", mtime=base + 100)
        result = self.run_evidence("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["timeline_consistent"])
        self.assertEqual([item["status"] for item in report["timeline_checks"]],
                         ["insufficient_stages", "mtime_order_ok"])

    def test_partial_inversion_is_review_clue_without_crash(self):
        base = 1_700_000_000
        self.put("论文-v1.md", mtime=base + 200)
        self.put("送检报告.md", mtime=base)
        result = self.run_evidence("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIs(report["timeline_consistent"], False)
        self.assertEqual([item["status"] for item in report["timeline_checks"]],
                         ["insufficient_stages", "needs_review"])

    def test_multilabel_source_does_not_count_as_independent_timeline_stage(self):
        base = 1_700_000_000
        self.put("原始数据-v1.md", mtime=base)
        self.put("论文-v2.md", mtime=base + 100)
        self.put("送检报告.md", mtime=base + 200)
        result = self.run_evidence("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIsNone(report["timeline_consistent"])
        self.assertEqual([item["status"] for item in report["timeline_checks"]],
                         ["insufficient_stages", "mtime_order_ok"])

    def test_output_cannot_equal_material_root(self):
        self.put("论文-v1.md")
        result = self.run_evidence("--out", self.root)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "manifest.json").exists())


class HookHardeningTests(unittest.TestCase):
    def run_hook(self, prompt):
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"prompt": prompt, "turn_id": "test"}),
            capture_output=True, text=True, check=False)

    def test_unrelated_appeals_do_not_trigger(self):
        for prompt in (
            "复核", "我要申诉", "这个结果误判了",
            "社保申诉材料怎么准备？", "交通事故责任认定误判，如何复核？",
            "知网查重的重复率误判，如何申诉？",
            "医学影像 AI 检测器误判了，怎么复核？",
            "本科论文答辩被误判，如何申诉？",
            "医院 AI 辅助诊断误判，怎么复核？",
            "AIGC 投资报告怎么申诉？",
        ):
            with self.subTest(prompt=prompt):
                result = self.run_hook(prompt)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_academic_aigc_appeals_and_direct_requests_trigger(self):
        for prompt in (
            "我的毕业论文被判为 AI 生成，想申请复核",
            "知网 AIGC 检测结果误判了，怎么申诉？",
            "学习通的论文 AI 率过高，需要自查",
            "降 AIGC 率有什么办法？",
        ):
            with self.subTest(prompt=prompt):
                result = self.run_hook(prompt)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("aigc-risk-audit", result.stdout)


if __name__ == "__main__":
    unittest.main()

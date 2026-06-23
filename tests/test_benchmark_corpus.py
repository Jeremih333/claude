from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline.benchmarking import (
    aggregate_session_metrics,
    assess_data_sufficiency,
    build_baseline_report,
    build_candidate_manifest,
    build_candidate_summary,
    infer_failure_reasons,
    summarize_failure_clusters,
)
from pipeline.versioning import build_pipeline_identity, config_hash


TOOLKIT_SCRIPT = Path(r"C:\Users\User\Desktop\toolkit\benchmark_corpus.py")


class BenchmarkCorpusTests(unittest.TestCase):
    def test_pipeline_identity_hash_is_stable_for_runtime_paths(self):
        cfg_a = {"output_root": "C:/temp/a", "ui_language": "ru", "max_shorts": 3}
        cfg_b = {"output_root": "D:/temp/b", "ui_language": "ru", "max_shorts": 3}
        self.assertEqual(config_hash(cfg_a), config_hash(cfg_b))
        identity = build_pipeline_identity(cfg_a)
        self.assertIn("pipeline_version", identity)
        self.assertIn("config_hash", identity)
        self.assertIn("git_commit", identity)

    def test_build_candidate_manifest_includes_identity_and_summary(self):
        meta = {
            "pipeline_version": "0.8.4",
            "config_hash": "ab129c",
            "git_commit": "deadbeef1234",
            "series_name": "Breaking Bad",
            "episode_name": "S02E03",
            "final_duration": 34.2,
            "story_summary": {
                "conversation_id": "conv_story",
                "story_arc_shape": "hook_setup_escalation_payoff",
                "hook": "He opens with a threat.",
                "setup": "The room goes quiet.",
                "escalation": "They argue about the plan.",
                "payoff": "He tells the truth.",
                "story_completion_score": 0.88,
                "context_completeness_score": 0.71,
                "hook_type": "threat_tension",
                "payoff_type": "reveal",
            },
            "first_second_hook_score": 0.81,
            "retention_soft_score": 0.72,
            "subtitle_quality_score": 0.64,
            "speaker_centered_rate": 0.89,
            "speaker_confidence_score": 0.84,
            "visual_conversation_score": 0.77,
            "speaker_switches": 4,
            "reframe_fallback_count": 1,
            "pacing_score": 0.81,
            "trimmed_silence_seconds": 2.4,
            "silence_trim_events": [{"start": 1.2, "end": 2.1, "silence_type": "dead_air"}],
            "quality_governor_decision": "accept",
        }
        manifest = build_candidate_manifest(
            "cand_00014",
            meta,
            paths={"preview": "preview.mp4"},
            human_labels=["good", "publishable"],
            failure_reason=[],
            created_at="2026-06-03T21:14:00",
        )

        self.assertEqual(manifest["candidate_id"], "cand_00014")
        self.assertEqual(manifest["pipeline_version"], "0.8.4")
        self.assertEqual(manifest["config_hash"], "ab129c")
        self.assertEqual(manifest["git_commit"], "deadbeef1234")
        self.assertEqual(manifest["human_labels"], ["good", "publishable"])
        self.assertTrue(manifest["summary"]["publishable"])
        self.assertAlmostEqual(manifest["summary"]["hook_score"], 0.81, places=4)
        self.assertEqual(manifest["paths"]["preview"], "preview.mp4")
        self.assertIn("story_window_plan", manifest)
        self.assertIn("story_window_segments", manifest)
        self.assertIn("story_thread_id", manifest)
        self.assertIn("story_coherence_score", manifest)
        self.assertIn("coherence_merge_reason", manifest)
        self.assertIn("coherence_rejection_reason", manifest)
        self.assertIn("story_summary", manifest)
        self.assertIn("story_chain", manifest)
        self.assertIn("story_fragments", manifest)
        self.assertIn("clarity_score", manifest)
        self.assertIn("window_expansion_meta", manifest)
        self.assertIn("pacing_score", manifest)
        self.assertIn("trimmed_silence_seconds", manifest)
        self.assertIn("silence_trim_events", manifest)
        self.assertIn("speaker_switches", manifest)
        self.assertIn("reframe_fallback_count", manifest)
        self.assertIn("speaker_confidence_score", manifest)
        self.assertIn("visual_conversation_score", manifest)
        self.assertAlmostEqual(manifest["pacing_score"], 0.81, places=4)

    def test_failure_reason_inference_and_aggregate_metrics(self):
        meta = {
            "final_duration": 58.0,
            "retention_soft_score": 0.44,
            "hook_score": 0.36,
            "visible_stakes_score": 0.31,
            "speaker_centered_rate": 0.18,
            "subtitle_quality_score": 0.41,
            "quality_governor_decision": "reject_story",
            "story_completeness_score": 0.39,
            "story_context_score": 0.28,
            "packaging_quality_score": 0.42,
            "title_quality_score": 0.49,
        }

        reasons = infer_failure_reasons(meta)
        self.assertIn("late_hook", reasons)
        self.assertIn("weak_hook", reasons)
        self.assertIn("subtitle_overload", reasons)
        self.assertIn("missing_context", reasons)
        self.assertIn("weak_payoff", reasons)
        self.assertIn("bad_title", reasons)
        self.assertIn("bad_pacing", reasons)

        summary = aggregate_session_metrics(
            [
                {
                    "summary": build_candidate_summary(
                        {
                            "quality_governor_decision": "accept",
                            "retention_soft_score": 0.8,
                            "subtitle_quality_score": 0.75,
                            "speaker_centered_rate": 0.9,
                            "first_second_hook_score": 0.81,
                        }
                    ),
                    "failure_reason": [],
                    "runtime_seconds": 1.2,
                },
                {
                    "summary": build_candidate_summary(meta),
                    "failure_reason": reasons,
                    "runtime_seconds": 2.2,
                    "ranking_fallback_used": True,
                },
            ]
        )
        self.assertEqual(summary["count"], 2)
        self.assertGreater(summary["publishable_rate"], 0.0)
        self.assertGreater(summary["fallback_frequency"], 0.0)
        self.assertIn("late_hook", summary["root_cause_tags"])

    def test_baseline_report_and_data_gate_are_deterministic(self):
        records = [
            {
                "human_labels": ["good", "publishable"],
                "failure_reason": [],
                "summary": {
                    "publishable": True,
                    "hook_score": 0.81,
                    "retention_soft_score": 0.72,
                    "subtitle_quality": 0.64,
                    "face_focus_rate": 0.89,
                },
                "runtime_seconds": 1.5,
            },
            {
                "human_labels": ["bad", "confusing"],
                "failure_reason": ["late_hook", "subtitle_overload", "bad_title"],
                "summary": {
                    "publishable": False,
                    "hook_score": 0.31,
                    "retention_soft_score": 0.42,
                    "subtitle_quality": 0.41,
                    "face_focus_rate": 0.27,
                },
                "runtime_seconds": 2.5,
            },
        ]
        report = build_baseline_report(records, sessions=[{"created_at": "2026-06-03T21:00:00", "aggregate": {"publishable_candidates": 1, "runtime_seconds": 1.0}}, {"created_at": "2026-06-03T22:00:00", "aggregate": {"publishable_candidates": 0, "runtime_seconds": 2.0}}, {"created_at": "2026-06-03T23:00:00", "aggregate": {"publishable_candidates": 1, "runtime_seconds": 3.0}}])
        self.assertEqual(report["count"], 2)
        self.assertIn("label_rates", report)
        self.assertIn("failure_clusters", report)
        self.assertIn("accepted_outputs_by_arc_shape", report)
        self.assertIn("data_sufficiency", report)
        self.assertEqual(report["data_sufficiency"]["status"], "weak")
        self.assertIn("average_story_completion_score", report["metrics"])
        self.assertIn("average_context_completeness_score", report["metrics"])
        self.assertIn("story_completion_score", report["score_distributions"])
        self.assertIn("context_completeness_score", report["score_distributions"])
        self.assertTrue(report["trend_analysis"]["available"])
        self.assertIn("recent_minus_early", report["trend_analysis"])

        clusters = summarize_failure_clusters(records)
        self.assertIn("late_hook", clusters["root_cause_counts"])
        self.assertTrue(clusters["failure_cooccurrence"])
        self.assertEqual(assess_data_sufficiency(50)["status"], "weak")
        self.assertEqual(assess_data_sufficiency(150)["status"], "minimum")
        self.assertEqual(assess_data_sufficiency(300)["status"], "recommended")
        self.assertEqual(assess_data_sufficiency(600)["status"], "strong")

    def test_cli_audit_and_compare_work_on_manifest_files(self):
        if not TOOLKIT_SCRIPT.exists():
            self.skipTest("benchmark_corpus.py not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            before_dir = root / "before"
            after_dir = root / "after"
            before_dir.mkdir()
            after_dir.mkdir()

            before_manifest = {
                "candidate_id": "cand_00001",
                "created_at": "2026-06-03T21:14:00",
                "pipeline_version": "0.8.4",
                "config_hash": "hash-a",
                "git_commit": "git-a",
                "series_name": "Show",
                "episode_name": "E01",
                "duration": 30.0,
                "human_labels": ["good", "publishable"],
                "failure_reason": [],
                "paths": {},
                "summary": {
                    "hook_score": 0.60,
                    "retention_soft_score": 0.61,
                    "subtitle_quality": 0.70,
                    "face_focus_rate": 0.80,
                    "publishable": True,
                },
            }
            after_manifest = {
                "candidate_id": "cand_00002",
                "created_at": "2026-06-03T21:15:00",
                "pipeline_version": "0.8.5",
                "config_hash": "hash-b",
                "git_commit": "git-b",
                "series_name": "Show",
                "episode_name": "E02",
                "duration": 31.0,
                "human_labels": ["bad"],
                "failure_reason": ["late_hook", "bad_title"],
                "paths": {},
                "summary": {
                    "hook_score": 0.40,
                    "retention_soft_score": 0.50,
                    "subtitle_quality": 0.55,
                    "face_focus_rate": 0.60,
                    "publishable": False,
                },
            }
            (before_dir / "candidate_manifest.json").write_text(json.dumps(before_manifest), encoding="utf-8")
            (after_dir / "candidate_manifest.json").write_text(json.dumps(after_manifest), encoding="utf-8")

            compare_out = root / "before_after_report.json"
            compare_proc = subprocess.run(
                [
                    sys.executable,
                    str(TOOLKIT_SCRIPT),
                    "compare",
                    str(before_dir / "candidate_manifest.json"),
                    str(after_dir / "candidate_manifest.json"),
                    "--output",
                    str(compare_out),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(compare_proc.returncode, 0, msg=compare_proc.stderr)
            self.assertTrue(compare_out.exists())
            payload = json.loads(compare_out.read_text(encoding="utf-8"))
            self.assertIn("comparison", payload)
            self.assertIn("small_sample_size", payload["comparison"]["warnings"])

            corpus_root = root / "benchmark_corpus"
            (corpus_root / "candidates" / "cand_00001").mkdir(parents=True)
            (corpus_root / "golden_set" / "cand_00002").mkdir(parents=True)
            (corpus_root / "candidates" / "cand_00001" / "candidate_manifest.json").write_text(
                json.dumps(before_manifest),
                encoding="utf-8",
            )
            (corpus_root / "golden_set" / "cand_00002" / "candidate_manifest.json").write_text(
                json.dumps(after_manifest),
                encoding="utf-8",
            )
            (corpus_root / "benchmark_index.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "created_at": "2026-06-03T21:20:00",
                        "updated_at": "2026-06-03T21:20:00",
                        "candidates": [
                            {
                                "candidate_id": "cand_00001",
                                "path": "candidates/cand_00001/candidate_manifest.json",
                                "human_labels": before_manifest["human_labels"],
                                "failure_reason": before_manifest["failure_reason"],
                            }
                        ],
                        "golden_set": [
                            {
                                "candidate_id": "cand_00002",
                                "path": "golden_set/cand_00002/candidate_manifest.json",
                                "human_labels": after_manifest["human_labels"],
                                "failure_reason": after_manifest["failure_reason"],
                            }
                        ],
                        "sessions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            audit_proc = subprocess.run(
                [sys.executable, str(TOOLKIT_SCRIPT), "audit", "--corpus", str(corpus_root)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(audit_proc.returncode, 0, msg=audit_proc.stderr)
            audit_payload = json.loads(audit_proc.stdout)
            self.assertEqual(audit_payload["count"], 2)
            self.assertEqual(audit_payload["candidate_count"], 1)
            self.assertEqual(audit_payload["golden_count"], 1)

            baseline_proc = subprocess.run(
                [sys.executable, str(TOOLKIT_SCRIPT), "baseline", "--corpus", str(corpus_root)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(baseline_proc.returncode, 0, msg=baseline_proc.stderr)
            baseline_payload = json.loads(baseline_proc.stdout)
            self.assertIn("metrics", baseline_payload)
            self.assertIn("data_sufficiency", baseline_payload)

            gate_proc = subprocess.run(
                [sys.executable, str(TOOLKIT_SCRIPT), "gate", "--corpus", str(corpus_root)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(gate_proc.returncode, 2)
            gate_payload = json.loads(gate_proc.stdout)
            self.assertEqual(gate_payload["gate"]["status"], "weak")


if __name__ == "__main__":
    unittest.main()

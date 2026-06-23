from __future__ import annotations

import unittest

from pipeline.highlight import _story_debug_segments, _summarize_reject_paths
from pipeline.montage.active_speaker_editor import summarize_reframe_debug


class RuntimeMontageReportTests(unittest.TestCase):
    def test_story_debug_segments_produces_story_parts(self):
        story_debug = _story_debug_segments(
            {
                "summary": {"summary_text": "План срывается прямо сейчас", "keywords": ["план", "срыв"]},
                "segments": [
                    {"text": "Смотри, он уже здесь"},
                    {"text": "Теперь надо объяснить"},
                    {"text": "И всё идёт не так"},
                    {"text": "Вот чем это закончилось"},
                ],
            },
            {"conversation_id": "conv_test", "segments": [{"start": 0.0, "end": 1.0}]},
        )

        self.assertEqual(story_debug["conversation_id"], "conv_test")
        self.assertFalse(story_debug["story_deficient"])
        self.assertTrue(story_debug["hook"])
        self.assertTrue(story_debug["setup"])
        self.assertTrue(story_debug["escalation"])
        self.assertTrue(story_debug["payoff"])

    def test_story_debug_segments_flags_missing_parts(self):
        story_debug = _story_debug_segments({"segments": [{"text": "Только один кусок"}]}, {})
        self.assertTrue(story_debug["story_deficient"])

    def test_reject_path_summary_merges_report_and_warnings(self):
        report = {
            "rejected_candidates": [
                {"reason": "insufficient_context", "candidate": {"start": 1.0, "end": 2.0, "source": "episode_1"}},
                {"reason": "insufficient_context", "candidate": {"start": 3.0, "end": 4.0, "source": "episode_2"}},
                {"reason": "subtitle_timeout", "candidate": {"start": 5.0, "end": 6.0, "source": "episode_3"}},
            ],
            "warnings": [
                "Candidate 1 rejected: low_story_quality",
                "Candidate 2 downgraded: low_story_quality",
                "Candidate 3 rejected: starts_mid_phrase",
            ],
        }

        summary = _summarize_reject_paths(report)

        self.assertGreaterEqual(summary["reason_counts"].get("insufficient_context", 0), 2)
        self.assertGreaterEqual(summary["reason_counts"].get("subtitle_timeout", 0), 1)
        self.assertTrue(any(item["reason"] == "low_story_quality" for item in summary["paths"]))
        self.assertTrue(any(item["reason"] == "insufficient_context" for item in summary["paths"]))

    def test_reframe_summary_exposes_listener_reactions(self):
        summary = summarize_reframe_debug(
            {
                "speaker_switches": 3,
                "speaker_to_listener_switches": 2,
                "listener_fallback_windows": 1,
                "speaker_confidence_score": 0.83,
                "visual_conversation_score": 0.71,
            }
        )

        self.assertEqual(summary["speaker_switches"], 3)
        self.assertEqual(summary["speaker_to_listener_switches"], 2)
        self.assertEqual(summary["listener_reaction_count"], 2)


if __name__ == "__main__":
    unittest.main()

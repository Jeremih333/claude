from __future__ import annotations

import unittest

import numpy as np

from pipeline.config import normalize_config
from pipeline.face_crop import _speaker_confidence_score, _visual_conversation_score
from pipeline.highlight import Pipeline, _build_pause_timeline, _pause_timeline_stats, _pacing_score_from_pause_timeline


class DialogueFirstDefaultsTests(unittest.TestCase):
    def test_normalize_config_sets_dialogue_first_defaults(self):
        cfg = normalize_config({})

        self.assertEqual(cfg["max_shorts"], 50)
        self.assertEqual(cfg["max_short_seconds"], 60)
        self.assertEqual(cfg["target_story_min_seconds"], 35)
        self.assertEqual(cfg["target_story_seconds"], 45)
        self.assertEqual(cfg["min_publishable_seconds"], 35)
        self.assertEqual(cfg["candidate_window_seconds"], 35)
        self.assertEqual(cfg["candidate_step_seconds"], 10)
        self.assertGreaterEqual(cfg["keep_dialogue_gap_seconds"], 1.0)
        self.assertEqual(cfg["story_mode"], "standard")
        self.assertEqual(cfg["story_selection_mode"], "dialogue_first")
        self.assertFalse(cfg["story_stitching_enabled"])
        self.assertFalse(cfg["story_extension_bonus_enabled"])
        self.assertFalse(cfg["story_continue_after_silence"])
        self.assertFalse(cfg["story_archetype_detection"])
        self.assertFalse(cfg["publishable_story_override_enabled"])
        self.assertFalse(cfg["reframe_scene_interest_fallback"])
        self.assertFalse(cfg["reframe_listener_face_fallback"])
        self.assertTrue(cfg["dialogue_two_shot_preferred"])
        self.assertFalse(cfg["face_preserving_fallback_enabled"])
        self.assertFalse(cfg["payoff_after_pause_bonus_enabled"])
        self.assertGreaterEqual(cfg["story_merge_gap_seconds"], 1.0)
        self.assertGreaterEqual(cfg["story_pause_cut_threshold_seconds"], 1.0)
        self.assertGreaterEqual(cfg["story_pause_keep_max_seconds"], cfg["story_pause_cut_threshold_seconds"])
        self.assertEqual(cfg["active_speaker_scan_profile"], "light")
        self.assertTrue(cfg["ranking_visual_precheck_enabled"])
        self.assertFalse(cfg["review_fast_mode_enabled"])
        self.assertEqual(cfg["review_fast_output_cap"], 8)
        self.assertEqual(cfg["review_fast_story_candidate_cap"], 24)

    def test_legacy_episode_light_profile_normalizes_to_light(self):
        cfg = normalize_config({"active_speaker_scan_profile": "episode_light"})

        self.assertEqual(cfg["active_speaker_scan_profile"], "light")

    def test_dialogue_flow_gate_allows_single_block_dialogue(self):
        pipeline = Pipeline({})

        self.assertTrue(
            pipeline._dialogue_flow_is_sufficient(
                {
                    "turns": [(0.0, 1.0), (2.0, 3.0)],
                    "speech_density": 0.28,
                    "silence_ratio": 0.35,
                    "audio_energy": 0.16,
                }
            )
        )
        self.assertTrue(
            pipeline._dialogue_flow_is_sufficient(
                {
                    "turns": [(0.0, 1.0)],
                    "speech_density": 0.27,
                    "silence_ratio": 0.62,
                    "audio_energy": 0.15,
                }
            )
        )
        self.assertTrue(
            pipeline._dialogue_flow_is_sufficient(
                {
                    "turns": [(0.0, 1.0), (1.5, 2.2), (3.0, 4.0)],
                    "speech_density": 0.11,
                    "silence_ratio": 0.82,
                    "audio_energy": 0.08,
                }
            )
        )
        self.assertTrue(
            pipeline._dialogue_flow_is_sufficient(
                {
                    "turns": [(0.0, 1.0)],
                    "speech_density": 0.05,
                    "silence_ratio": 0.96,
                    "audio_energy": 0.03,
                }
            )
        )
        self.assertEqual(
            pipeline._dialogue_flow_admission(
                {
                    "turns": [(0.0, 1.0)],
                    "speech_density": 0.05,
                    "silence_ratio": 0.96,
                    "audio_energy": 0.03,
                }
            )["reason"],
            "single_turn_dialogue_soft",
        )

    def test_single_turn_audio_window_still_builds_candidate(self):
        pipeline = Pipeline({})
        summary = {
            "turns": [(0.0, 38.0)],
            "speech_density": 0.31,
            "silence_ratio": 0.12,
            "audio_energy": 0.22,
        }

        built = pipeline._build_story_candidates_from_turns_linear(0.0, 45.0, "scene_cluster", summary)

        self.assertTrue(built)
        self.assertGreaterEqual(built[0]["estimated_turns"], 1)

    def test_pause_timeline_cuts_gap_over_one_second(self):
        cfg = normalize_config({})
        voiced = [(0.0, 0.2), (3.6, 3.8)]
        pcm = np.zeros(int(5.0 * 16000), dtype=np.int16)

        timeline = _build_pause_timeline(voiced, pcm, 16000, cfg, total_duration=5.0)

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["decision"], "cut")
        self.assertGreaterEqual(timeline[0]["duration"], 1.0)
        self.assertEqual(timeline[0]["silence_type"], "dead_air")
        stats = _pause_timeline_stats(timeline)
        self.assertGreater(stats["trimmed_silence_seconds"], 0.0)
        self.assertEqual(len(stats["silence_trim_events"]), 1)
        pacing = _pacing_score_from_pause_timeline(timeline, original_duration=5.0, output_duration=2.0)
        self.assertGreaterEqual(pacing, 0.0)
        self.assertLessEqual(pacing, 1.0)

    def test_active_speaker_and_visual_conversation_scores_are_bounded(self):
        strong = _speaker_confidence_score(
            {
                "subtitle_turn_alignment_score": 0.82,
                "speaker_confidence": 0.78,
                "speaker_turn_strength": 0.61,
                "mouth_motion_proxy": 0.74,
                "lock_confidence": 0.71,
                "subject_confidence": 0.68,
                "dialogue_likelihood": 0.66,
                "listener_confidence": 0.25,
            }
        )
        weak = _speaker_confidence_score(
            {
                "subtitle_turn_alignment_score": 0.24,
                "speaker_confidence": 0.22,
                "speaker_turn_strength": 0.10,
                "mouth_motion_proxy": 0.08,
                "lock_confidence": 0.18,
                "subject_confidence": 0.16,
                "dialogue_likelihood": 0.14,
                "listener_confidence": 0.12,
            }
        )
        self.assertGreater(strong, weak)
        self.assertGreaterEqual(strong, 0.0)
        self.assertLessEqual(strong, 1.0)

        visual_good = _visual_conversation_score(
            4,
            12,
            speaker_centered_rate=0.78,
            dialogue_center_windows=5,
            listener_fallback_windows=2,
            subject_person_fallback_windows=1,
            center_fallback_used=False,
            face_preserving_fallback_used=False,
        )
        visual_bad = _visual_conversation_score(
            0,
            12,
            speaker_centered_rate=0.12,
            dialogue_center_windows=0,
            listener_fallback_windows=0,
            subject_person_fallback_windows=0,
            center_fallback_used=True,
            face_preserving_fallback_used=True,
        )
        self.assertGreater(visual_good, visual_bad)
        self.assertGreaterEqual(visual_good, 0.0)
        self.assertLessEqual(visual_good, 1.0)


if __name__ == "__main__":
    unittest.main()

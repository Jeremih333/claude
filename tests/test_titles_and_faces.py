from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from pipeline.active_speaker import _pick_primary_face
from pipeline.highlight import Pipeline
from pipeline.titling import _pick_hashtags_contextual, generate_context_title


class TitleGenerationTests(unittest.TestCase):
    def test_context_hint_drives_title_and_hashtags(self):
        subtitle_info = {
            "language": "en",
            "summary": {
                "summary_text": "",
                "keywords": [],
                "mood": "conversation",
            },
        }
        meta = {
            "story_summary": {
                "conversation_id": "conv_story",
                "story_arc_shape": "hook_setup_escalation_payoff",
                "hook": "Why are you here?",
                "setup": "He confronts her at the door.",
                "escalation": "The argument gets louder.",
                "payoff": "She finally tells the truth.",
                "summary_text": "Why are you here? He confronts her at the door. The argument gets louder. She finally tells the truth.",
                "title_seed": "Why are you here? He confronts her at the door.",
                "hook_type": "question",
                "payoff_type": "reveal",
                "topic_terms": ["truth", "door"],
                "characters": ["He", "She"],
            },
            "title_context_hint": "He realizes the truth after the phone call",
            "premise_summary": "He realizes the truth after the phone call",
            "selected_opening_reason": "dialogue conflict reveals the truth",
            "story_unit_type": "dialogue_cluster",
            "score_breakdown": {
                "story_clarity_score": 0.68,
                "hook_score": 0.64,
                "closure_score": 0.61,
                "recommendation_readiness_score": 0.66,
            },
            "visible_stakes_score": 0.64,
            "first_frame_clarity_score": 0.60,
            "sound_off_premise_score": 0.62,
            "dialogue_dependency_penalty": 0.22,
            "hook_strength": 0.64,
            "payoff_strength": 0.61,
            "recommendation_readiness_score": 0.66,
            "subtitle_seed_rejected_for_title": True,
        }
        cfg = {
            "title_language": "en",
            "title_style": "context_clean",
            "title_max_length": 72,
            "title_include_hashtags": True,
            "title_max_hashtags": 2,
            "title_include_emoji": False,
            "packaging_profile": "clean_neutral",
        }

        payload = generate_context_title(subtitle_info, meta, cfg)

        self.assertIn("why are you here", payload["title"].lower())
        self.assertNotEqual(payload["title"], "Strong story moment")
        self.assertGreaterEqual(len(payload["hashtags"]), 2)
        self.assertTrue(any(tag.startswith("#") for tag in payload["hashtags"]))

    def test_technical_pipeline_title_is_replaced(self):
        payload = generate_context_title(
            {
                "language": "en",
                "summary": {
                    "summary_text": "",
                    "keywords": ["door"],
                    "mood": "tension",
                },
            },
            {
                "title_context_hint": "fallback window; visible stakes are immediately readable",
                "story_unit_type": "fallback_window",
                "dialogue_exchange_score": 1.0,
                "score_breakdown": {
                    "story_clarity_score": 0.80,
                    "hook_score": 0.80,
                    "closure_score": 0.80,
                    "recommendation_readiness_score": 0.80,
                },
                "subtitle_seed_rejected_for_title": True,
            },
            {
                "title_language": "en",
                "title_include_hashtags": True,
                "title_max_hashtags": 2,
                "title_include_emoji": False,
            },
        )

        self.assertNotIn("fallback window", payload["title"].lower())
        self.assertIn("#dialogue", payload["hashtags"])
        self.assertTrue(all(not re.search(r"[\u0400-\u04FF]", tag) for tag in payload["hashtags"]))
        self.assertNotIn("#shorts", payload["hashtags"])

    def test_context_hint_overrides_generic_dialogue_seed(self):
        subtitle_info = {
            "language": "en",
            "summary": {
                "summary_text": "I don't know what to say",
                "keywords": ["unknown", "say"],
                "mood": "conversation",
            },
        }
        meta = {
            "title_context_hint": "He realizes the truth after the phone call",
            "premise_summary": "He realizes the truth after the phone call",
            "selected_opening_reason": "dialogue conflict reveals the truth",
            "story_unit_type": "dialogue_cluster",
            "score_breakdown": {
                "story_clarity_score": 0.54,
                "hook_score": 0.52,
                "closure_score": 0.49,
                "recommendation_readiness_score": 0.55,
            },
            "visible_stakes_score": 0.58,
            "first_frame_clarity_score": 0.56,
            "sound_off_premise_score": 0.57,
            "dialogue_dependency_penalty": 0.30,
            "hook_strength": 0.52,
            "payoff_strength": 0.49,
            "recommendation_readiness_score": 0.55,
            "subtitle_seed_rejected_for_title": False,
        }
        cfg = {
            "title_language": "en",
            "title_style": "context_clean",
            "title_max_length": 72,
            "title_include_hashtags": True,
            "title_max_hashtags": 2,
            "title_include_emoji": False,
            "packaging_profile": "clean_neutral",
        }

        payload = generate_context_title(subtitle_info, meta, cfg)

        self.assertIn("truth", payload["title"].lower())
        self.assertNotIn("i don't know", payload["title"].lower())
        self.assertIn("#dialogue", payload["hashtags"])

    def test_contextual_hashtag_helper_prefers_dialogue_tag(self):
        hashtags = _pick_hashtags_contextual(["truth", "phone"], "conversation", 2, context_hint="phone call truth")
        self.assertEqual(len(hashtags), 2)
        self.assertIn("#dialogue", hashtags)

    def test_dialogue_cluster_keeps_dialogue_tag_even_with_tension_mood(self):
        payload = generate_context_title(
            {
                "language": "en",
                "summary": {
                    "summary_text": "",
                    "keywords": ["door", "truth"],
                    "mood": "tension",
                },
            },
            {
                "title_context_hint": "The argument at the door reveals the truth",
                "story_unit_type": "dialogue_cluster",
                "dialogue_exchange_score": 1.0,
                "score_breakdown": {
                    "story_clarity_score": 0.70,
                    "hook_score": 0.64,
                    "closure_score": 0.60,
                    "recommendation_readiness_score": 0.66,
                },
                "subtitle_seed_rejected_for_title": True,
            },
            {
                "title_language": "en",
                "title_include_hashtags": True,
                "title_max_hashtags": 2,
                "title_include_emoji": False,
                "packaging_profile": "clean_neutral",
            },
        )

        self.assertIn("#dialogue", payload["hashtags"])

    def test_russian_story_packaging_uses_contextual_title_and_hashtags(self):
        payload = generate_context_title(
            {
                "language": "ru",
                "summary": {
                    "summary_text": "",
                    "keywords": ["звонок", "правда"],
                    "mood": "conversation",
                },
            },
            {
                "title_context_hint": "Он пришёл выяснить правду после звонка",
                "premise_summary": "Он пришёл выяснить правду после звонка",
                "selected_opening_reason": "dialogue conflict reveals the truth",
                "story_unit_type": "dialogue_cluster",
                "story_arc_shape": "hook_setup_escalation_payoff",
                "hook_type": "accusation_denial",
                "payoff_type": "reveal",
                "story_completion_score": 0.84,
                "context_completeness_score": 0.72,
                "score_breakdown": {
                    "story_clarity_score": 0.76,
                    "hook_score": 0.74,
                    "closure_score": 0.71,
                    "recommendation_readiness_score": 0.70,
                },
                "visible_stakes_score": 0.74,
                "first_frame_clarity_score": 0.68,
                "sound_off_premise_score": 0.69,
                "dialogue_dependency_penalty": 0.18,
                "hook_strength": 0.74,
                "payoff_strength": 0.71,
                "recommendation_readiness_score": 0.70,
                "subtitle_seed_rejected_for_title": True,
            },
            {
                "title_language": "ru",
                "title_style": "context_clean",
                "title_max_length": 72,
                "title_include_hashtags": True,
                "title_max_hashtags": 2,
                "title_include_emoji": False,
                "packaging_profile": "ru_serial_drama",
            },
        )

        self.assertTrue(re.search(r"[\u0400-\u04FF]", payload["title"]))
        self.assertNotIn("The conversation", payload["title"])
        self.assertIn("#сериал", payload["hashtags"])
        self.assertIn("#shorts", payload["hashtags"])
        self.assertTrue(any(tag in payload["hashtags"] for tag in ("#конфликт", "#развязка", "#реакция", "#интрига")))


class RankingFallbackTests(unittest.TestCase):
    def test_fallback_window_with_dialogue_turns_is_labeled_as_dialogue_cluster(self):
        pipeline = Pipeline({})
        candidate = pipeline._fallback_window_candidate(
            10.0,
            50.0,
            "scene_cluster",
            {
                "speech_density": 0.31,
                "silence_ratio": 0.24,
                "audio_energy": 0.22,
                "turns": [(0.0, 1.0), (1.3, 2.2), (2.9, 3.6)],
            },
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["story_unit_type"], "dialogue_cluster")
        self.assertEqual(candidate["score_breakdown"]["story_unit_type"], "dialogue_cluster")
        self.assertEqual(candidate["source"], "scene_cluster")

    def test_selection_starvation_bucket_groups_insufficient_context_as_boundary(self):
        pipeline = Pipeline({})
        self.assertEqual(pipeline._selection_starvation_bucket("insufficient_context"), "boundary_starvation")
        self.assertEqual(pipeline._selection_starvation_bucket("low_dialogue_flow"), "vad_starvation")

    def test_visual_precheck_populates_face_evidence_before_fallback(self):
        pipeline = Pipeline({"active_speaker_scan_profile": "episode_light"})
        candidate = {
            "start": 10.0,
            "end": 30.0,
            "score_breakdown": {
                "speech_density": 0.42,
                "silence_ratio": 0.20,
                "audio_energy": 0.30,
                "hook_score": 0.62,
                "story_context_score": 0.42,
                "curiosity_gap_score": 0.64,
                "payoff_strength": 0.58,
                "subtitle_quality_score": 0.58,
            },
        }

        with patch(
            "pipeline.highlight.sample_face_focus_stats",
            return_value={
                "face_presence": 0.80,
                "person_presence": 0.20,
                "subject_presence": 0.80,
                "avg_face_size": 0.04,
                "avg_person_size": 0.08,
            },
        ):
            enriched = pipeline._ranking_visual_precheck("episode.mp4", candidate)

        breakdown = enriched["score_breakdown"]
        self.assertGreaterEqual(float(breakdown["face_evidence_score"]), 0.65)
        self.assertGreaterEqual(float(breakdown["visual_subject_score"]), 0.60)
        self.assertLess(float(breakdown["empty_frame_risk"]), 0.20)
        self.assertEqual(breakdown["subject_detector_pass"], "light")

    def test_timeout_fallback_penalizes_no_face_scene(self):
        pipeline = Pipeline({})
        base_candidate = {
            "hook_gap": 0.15,
            "tail_gap": 0.35,
            "speech_coverage": 0.42,
            "estimated_turns": 2,
            "story_unit_type": "dialogue_cluster",
            "score_breakdown": {
                "speech_density": 0.38,
                "silence_ratio": 0.34,
                "audio_energy": 0.31,
                "story_context_score": 0.18,
                "subtitle_quality_score": 0.58,
                "motion": 0.12,
                "brightness": 0.18,
            },
        }
        no_face_score, no_face_breakdown = pipeline._score_story_candidate_timeout_fallback(base_candidate)
        face_candidate = {
            **base_candidate,
            "score_breakdown": {
                **base_candidate["score_breakdown"],
                "face_presence": 0.22,
                "person_presence": 0.12,
                "subject_presence": 0.18,
                "avg_face_size": 0.04,
                "avg_person_size": 0.08,
            },
        }
        face_score, face_breakdown = pipeline._score_story_candidate_timeout_fallback(face_candidate)

        self.assertLess(no_face_score, face_score)
        self.assertLess(float(no_face_breakdown["face_evidence_score"]), float(face_breakdown["face_evidence_score"]))

    def test_final_crop_probe_converts_source_face_to_reframe_evidence(self):
        pipeline = Pipeline({"final_crop_visual_probe_enabled": True})
        candidate = {
            "score_breakdown": {
                "face_presence": 0.90,
                "subject_presence": 0.90,
                "visual_premise_strength": 0.70,
                "story_interest_score": 0.62,
                "story_completeness_score": 0.60,
                "watchability_score": 0.64,
                "recommendation_readiness_score": 0.60,
                "packaging_quality_score": 0.58,
                "visual_subject_score": 0.70,
                "reframe_feasibility_score": 0.65,
            },
            "selection_visual_soft_gate": True,
        }
        subtitle_info = {
            "confidence": 0.80,
            "signals": {
                "subtitle_quality_score": 0.70,
                "subtitle_text_sanity_score": 0.70,
                "story_boundary_confidence": 0.70,
                "dialogue_flow_score": 0.80,
            },
        }

        with patch("pipeline.highlight.os.path.exists", return_value=True), patch(
            "pipeline.highlight.probe_video",
            return_value=(True, 24.0),
        ), patch(
            "pipeline.highlight.sample_face_focus_stats",
            return_value={
                "face_presence": 0.75,
                "person_presence": 0.0,
                "subject_presence": 0.75,
                "avg_face_size": 0.05,
                "subject_detector_pass": "light",
            },
        ):
            debug = pipeline._probe_final_crop_visual("crop.mp4", candidate, {"center_safe_fallback_used": True})

        self.assertGreaterEqual(int(debug["evidence_visible_faces_peak"]), 1)
        self.assertGreater(float(debug["speaker_centered_rate"]), 0.0)
        self.assertEqual(pipeline._quality_governor_decision(candidate, subtitle_info, debug), "accept")


class ReviewPassRecoveryTests(unittest.TestCase):
    def test_review_pass_is_disabled_by_default(self):
        pipeline = Pipeline(
            {
                "review_pass_enabled": True,
                "review_pass_min_outputs": 10,
                "review_pass_output_cap": 20,
                "review_pass_face_floor": 0.10,
                "review_pass_min_speech_density": 0.14,
                "review_pass_chain_gap_seconds": 72.0,
                "review_pass_max_chain_windows": 4,
                "review_pass_max_stitched_seconds": 60,
            }
        )
        ranked = [
            {
                "start": 0.0,
                "end": 18.0,
                "source": "scene_cluster",
                "score": 0.42,
                "story_clarity_score": 0.36,
                "speech_coverage": 0.38,
                "estimated_turns": 2,
                "score_breakdown": {
                    "speech_density": 0.34,
                    "silence_ratio": 0.26,
                    "audio_energy": 0.28,
                    "face_presence": 0.78,
                    "person_presence": 0.34,
                    "subject_presence": 0.72,
                    "story_clarity_score": 0.36,
                    "story_context_score": 0.22,
                    "visual_subject_score": 0.68,
                    "reframe_feasibility_score": 0.61,
                    "empty_frame_risk": 0.16,
                    "hook_score": 0.48,
                    "closure_score": 0.44,
                },
            },
            {
                "start": 18.8,
                "end": 36.5,
                "source": "scene_cluster",
                "score": 0.39,
                "story_clarity_score": 0.34,
                "speech_coverage": 0.36,
                "estimated_turns": 2,
                "score_breakdown": {
                    "speech_density": 0.33,
                    "silence_ratio": 0.29,
                    "audio_energy": 0.27,
                    "face_presence": 0.82,
                    "person_presence": 0.36,
                    "subject_presence": 0.76,
                    "story_clarity_score": 0.34,
                    "story_context_score": 0.24,
                    "visual_subject_score": 0.66,
                    "reframe_feasibility_score": 0.60,
                    "empty_frame_risk": 0.18,
                    "hook_score": 0.50,
                    "closure_score": 0.46,
                },
            },
            {
                "start": 37.0,
                "end": 54.8,
                "source": "scene_cluster",
                "score": 0.41,
                "story_clarity_score": 0.35,
                "speech_coverage": 0.37,
                "estimated_turns": 2,
                "score_breakdown": {
                    "speech_density": 0.35,
                    "silence_ratio": 0.27,
                    "audio_energy": 0.29,
                    "face_presence": 0.80,
                    "person_presence": 0.33,
                    "subject_presence": 0.74,
                    "story_clarity_score": 0.35,
                    "story_context_score": 0.25,
                    "visual_subject_score": 0.67,
                    "reframe_feasibility_score": 0.62,
                    "empty_frame_risk": 0.17,
                    "hook_score": 0.49,
                    "closure_score": 0.47,
                },
            },
        ]

        review_candidates = pipeline._build_review_pass_candidates(ranked, [], progress_callback=None)

        self.assertEqual(review_candidates, [])
        self.assertFalse(pipeline.cfg["review_pass_enabled"])

    def test_review_pass_ignores_no_face_candidates(self):
        pipeline = Pipeline(
            {
                "review_pass_enabled": True,
                "review_pass_min_outputs": 10,
                "review_pass_output_cap": 20,
            }
        )
        ranked = [
            {
                "start": 0.0,
                "end": 24.0,
                "source": "scene_cluster",
                "score": 0.18,
                "story_clarity_score": 0.20,
                "speech_coverage": 0.24,
                "estimated_turns": 1,
                "score_breakdown": {
                    "speech_density": 0.20,
                    "silence_ratio": 0.50,
                    "audio_energy": 0.18,
                    "face_presence": 0.0,
                    "person_presence": 0.0,
                    "subject_presence": 0.0,
                    "story_clarity_score": 0.20,
                    "story_context_score": 0.12,
                    "visual_subject_score": 0.10,
                    "reframe_feasibility_score": 0.12,
                    "empty_frame_risk": 0.92,
                    "hook_score": 0.18,
                    "closure_score": 0.14,
                },
            }
        ]

        review_candidates = pipeline._build_review_pass_candidates(ranked, [], progress_callback=None)

        self.assertEqual(review_candidates, [])

    def test_final_crop_probe_detects_mid_clip_drift(self):
        pipeline = Pipeline({"final_crop_visual_probe_enabled": True})
        candidate = {
            "score_breakdown": {
                "face_presence": 0.88,
                "subject_presence": 0.82,
                "visual_premise_strength": 0.72,
                "story_interest_score": 0.64,
                "story_completeness_score": 0.62,
                "watchability_score": 0.65,
                "recommendation_readiness_score": 0.61,
                "packaging_quality_score": 0.60,
                "visual_subject_score": 0.70,
                "reframe_feasibility_score": 0.66,
            },
            "selection_visual_soft_gate": True,
        }
        subtitle_info = {
            "confidence": 0.80,
            "signals": {
                "subtitle_quality_score": 0.70,
                "subtitle_text_sanity_score": 0.70,
                "story_boundary_confidence": 0.70,
                "dialogue_flow_score": 0.80,
            },
        }

        sample_results = [
            {
                "face_presence": 0.72,
                "person_presence": 0.10,
                "subject_presence": 0.72,
                "avg_face_size": 0.05,
                "subject_detector_pass": "light",
            },
            {
                "face_presence": 0.00,
                "person_presence": 0.00,
                "subject_presence": 0.00,
                "avg_face_size": 0.00,
                "subject_detector_pass": "light",
            },
            {
                "face_presence": 0.68,
                "person_presence": 0.08,
                "subject_presence": 0.68,
                "avg_face_size": 0.05,
                "subject_detector_pass": "light",
            },
        ]

        with patch("pipeline.highlight.os.path.exists", return_value=True), patch(
            "pipeline.highlight.probe_video",
            return_value=(True, 24.0),
        ), patch(
            "pipeline.highlight.sample_face_focus_stats",
            side_effect=sample_results,
        ):
            debug = pipeline._probe_final_crop_visual("crop.mp4", candidate, {"face_preserving_fallback_used": True})

        self.assertLess(float(debug["final_crop_face_presence_min"]), 0.08)
        self.assertGreater(float(debug["final_crop_face_presence_avg"]), 0.0)
        self.assertEqual(pipeline._quality_governor_decision(candidate, subtitle_info, debug), "accept")


class ActiveSpeakerTests(unittest.TestCase):
    def test_pick_primary_face_switches_to_stronger_speaker(self):
        faces = [
            {
                "track_id": 1,
                "detected": True,
                "speaking_score": 0.35,
                "listener_score": 0.81,
                "mouth_motion_proxy": 0.03,
                "box_w": 0.34,
                "box_h": 0.28,
            },
            {
                "track_id": 2,
                "detected": True,
                "speaking_score": 0.42,
                "listener_score": 0.22,
                "mouth_motion_proxy": 0.24,
                "box_w": 0.22,
                "box_h": 0.20,
            },
        ]

        primary = _pick_primary_face(faces, previous_primary_track_id=1)

        self.assertIsNotNone(primary)
        self.assertEqual(int(primary["track_id"]), 2)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from pipeline.highlight import Pipeline


class QualityGovernorTests(unittest.TestCase):
    def test_review_defaults_are_safe_for_all_candidate_paths(self):
        pipeline = Pipeline({})
        self.assertEqual(pipeline._candidate_review_defaults(True), (False, "test_mode_visual_only"))
        self.assertEqual(pipeline._candidate_review_defaults(False), (False, "strong_publishable"))

    def test_story_failure_flags_are_debug_only(self):
        pipeline = Pipeline({})
        candidate = {
            "publishable_story_override": False,
            "rejected_for_missing_payoff": True,
            "rejected_for_topic_jump": False,
            "rejected_for_confusing_story": False,
            "score_breakdown": {
                "story_interest_score": 0.72,
                "story_completeness_score": 0.68,
                "watchability_score": 0.74,
                "recommendation_readiness_score": 0.70,
                "packaging_quality_score": 0.68,
                "visual_premise_strength": 0.72,
                "sound_off_hook_score": 0.70,
                "first_second_hook_score": 0.68,
                "premise_signal_score": 0.71,
                "dialogue_dependency_penalty": 0.12,
                "visual_subject_score": 0.68,
                "reframe_feasibility_score": 0.66,
                "face_presence": 0.72,
                "person_presence": 0.30,
                "subject_presence": 0.58,
            },
        }
        subtitle_info = {
            "confidence": 0.88,
            "signals": {
                "subtitle_text_sanity_score": 0.88,
                "subtitle_quality_score": 0.84,
                "story_boundary_confidence": 0.82,
                "dialogue_flow_score": 0.80,
                "closure_score": 0.76,
                "interestingness_score": 0.74,
            },
        }
        reframe_debug = {
            "subject_visibility_ratio": 0.78,
            "speaker_centered_rate": 0.52,
            "speaker_face_centered_windows": 4,
            "dialogue_center_windows": 3,
            "listener_fallback_windows": 1,
            "subject_person_fallback_windows": 0,
            "evidence_visible_faces_peak": 2,
            "evidence_visible_persons_peak": 1,
            "speaker_center_offset_avg": 0.06,
            "speaker_center_offset_p95": 0.08,
            "face_edge_clip_rate": 0.06,
            "final_crop_face_presence": 0.28,
            "final_crop_person_presence": 0.18,
            "final_crop_subject_presence": 0.24,
        }

        self.assertEqual(pipeline._quality_governor_decision(candidate, subtitle_info, reframe_debug), "accept")

    def test_story_thread_assignment_separates_unrelated_segments(self):
        pipeline = Pipeline({"story_coherence_threshold": 0.62, "story_thread_window_seconds": 24.0})
        candidates = [
            {
                "start": 0.0,
                "end": 18.0,
                "source": "scene_cluster_a",
                "story_unit_type": "dialogue_cluster",
                "score_breakdown": {
                    "story_unit_type": "dialogue_cluster",
                    "story_context_score": 0.60,
                    "hook_score": 0.58,
                    "first_second_hook_score": 0.57,
                    "sound_off_hook_score": 0.56,
                    "face_presence": 0.66,
                    "person_presence": 0.50,
                    "subject_presence": 0.52,
                },
            },
            {
                "start": 19.0,
                "end": 39.0,
                "source": "scene_cluster_a",
                "story_unit_type": "dialogue_cluster",
                "score_breakdown": {
                    "story_unit_type": "dialogue_cluster",
                    "story_context_score": 0.61,
                    "hook_score": 0.59,
                    "first_second_hook_score": 0.58,
                    "sound_off_hook_score": 0.57,
                    "face_presence": 0.67,
                    "person_presence": 0.51,
                    "subject_presence": 0.53,
                },
            },
            {
                "start": 130.0,
                "end": 160.0,
                "source": "scene_cluster_b",
                "story_unit_type": "reveal_discovery",
                "score_breakdown": {
                    "story_unit_type": "reveal_discovery",
                    "story_context_score": 0.25,
                    "hook_score": 0.36,
                    "first_second_hook_score": 0.32,
                    "sound_off_hook_score": 0.31,
                    "face_presence": 0.61,
                    "person_presence": 0.33,
                    "subject_presence": 0.31,
                },
            },
        ]

        assigned = pipeline._assign_story_threads(candidates)
        self.assertEqual(assigned[0]["story_thread_id"], assigned[1]["story_thread_id"])
        self.assertNotEqual(assigned[0]["story_thread_id"], assigned[2]["story_thread_id"])
        self.assertGreaterEqual(float(assigned[1]["story_coherence_score"]), 0.62)
        self.assertLess(float(assigned[2]["story_coherence_score"]), 1.0)

    def test_story_pair_coherence_penalizes_thread_breaks(self):
        pipeline = Pipeline({"story_coherence_threshold": 0.62, "story_thread_window_seconds": 24.0})
        left = {
            "start": 0.0,
            "end": 20.0,
            "source": "scene_cluster_a",
            "story_unit_type": "dialogue_cluster",
            "score_breakdown": {
                "story_unit_type": "dialogue_cluster",
                "story_context_score": 0.58,
                "story_clarity_score": 0.60,
                "hook_score": 0.57,
                "first_second_hook_score": 0.56,
                "sound_off_hook_score": 0.55,
                "face_presence": 0.70,
                "person_presence": 0.53,
                "subject_presence": 0.54,
                "silence_ratio": 0.20,
            },
        }
        right = {
            "start": 102.0,
            "end": 130.0,
            "source": "scene_cluster_b",
            "story_unit_type": "reveal_discovery",
            "score_breakdown": {
                "story_unit_type": "reveal_discovery",
                "story_context_score": 0.22,
                "story_clarity_score": 0.26,
                "hook_score": 0.28,
                "first_second_hook_score": 0.24,
                "sound_off_hook_score": 0.23,
                "face_presence": 0.56,
                "person_presence": 0.21,
                "subject_presence": 0.19,
                "silence_ratio": 0.38,
            },
        }

        coherence = pipeline._story_pair_coherence_score(left, right)
        self.assertLess(coherence, 0.62)
        self.assertGreaterEqual(pipeline._story_pair_coherence_score(left, left), coherence)

    def test_accepts_no_subject_center_safe_fallback(self):
        pipeline = Pipeline({})
        candidate = {
            "selection_visual_soft_gate": True,
            "publishable_story_override": False,
            "score_breakdown": {
                "visual_premise_strength": 0.62,
                "sound_off_hook_score": 0.61,
                "first_second_hook_score": 0.60,
                "premise_signal_score": 0.60,
                "story_interest_score": 0.67,
                "story_completeness_score": 0.64,
                "watchability_score": 0.66,
                "recommendation_readiness_score": 0.63,
                "visual_subject_score": 0.28,
                "reframe_feasibility_score": 0.33,
                "empty_frame_risk": 0.52,
                "hook_strength": 0.61,
                "payoff_strength": 0.58,
                "dialogue_dependency_penalty": 0.21,
            },
        }
        subtitle_info = {
            "signals": {
                "interestingness_score": 0.68,
                "closure_score": 0.63,
                "story_boundary_confidence": 0.60,
                "subtitle_quality_score": 0.68,
            }
        }
        reframe_debug = {
            "hard_timeout_triggered": True,
            "anchor_switches": 1,
            "center_safe_fallback_used": True,
            "subject_acquisition_state": "no_visible_subject",
            "speaker_face_centered_windows": 0,
            "speaker_centered_rate": 0.0,
            "evidence_visible_faces_peak": 0,
            "evidence_visible_persons_peak": 0,
        }

        self.assertEqual(
            pipeline._quality_governor_decision(candidate, subtitle_info, reframe_debug),
            "accept",
        )

    def test_accepts_fallback_when_visual_evidence_is_present(self):
        pipeline = Pipeline({})
        candidate = {
            "selection_visual_soft_gate": True,
            "publishable_story_override": False,
            "score_breakdown": {
                "visual_premise_strength": 0.62,
                "sound_off_hook_score": 0.61,
                "first_second_hook_score": 0.60,
                "premise_signal_score": 0.60,
                "story_interest_score": 0.67,
                "story_completeness_score": 0.64,
                "watchability_score": 0.66,
                "recommendation_readiness_score": 0.63,
                "visual_subject_score": 0.28,
                "reframe_feasibility_score": 0.33,
                "empty_frame_risk": 0.52,
                "hook_strength": 0.61,
                "payoff_strength": 0.58,
                "dialogue_dependency_penalty": 0.21,
                "face_presence": 0.22,
                "person_presence": 0.0,
                "subject_presence": 0.0,
            },
        }
        subtitle_info = {
            "signals": {
                "interestingness_score": 0.68,
                "closure_score": 0.63,
                "story_boundary_confidence": 0.60,
                "subtitle_quality_score": 0.68,
            }
        }
        reframe_debug = {
            "hard_timeout_triggered": True,
            "anchor_switches": 1,
            "center_safe_fallback_used": True,
            "subject_acquisition_state": "no_visible_subject",
            "speaker_face_centered_windows": 0,
            "speaker_centered_rate": 0.0,
            "evidence_visible_faces_peak": 1,
            "evidence_visible_persons_peak": 0,
        }

        self.assertEqual(
            pipeline._quality_governor_decision(candidate, subtitle_info, reframe_debug),
            "accept",
        )

    def test_dialogue_safe_accept_can_skip_light_subtitle_noise(self):
        pipeline = Pipeline({})
        candidate = {
            "selection_visual_soft_gate": True,
            "publishable_story_override": False,
            "score_breakdown": {
                "visual_premise_strength": 0.66,
                "sound_off_hook_score": 0.64,
                "first_second_hook_score": 0.63,
                "premise_signal_score": 0.65,
                "story_interest_score": 0.64,
                "story_completeness_score": 0.63,
                "watchability_score": 0.66,
                "recommendation_readiness_score": 0.64,
                "visual_subject_score": 0.34,
                "reframe_feasibility_score": 0.31,
                "empty_frame_risk": 0.46,
                "hook_strength": 0.63,
                "payoff_strength": 0.60,
                "dialogue_dependency_penalty": 0.20,
                "face_presence": 0.18,
                "person_presence": 0.10,
                "subject_presence": 0.12,
            },
        }
        subtitle_info = {
            "signals": {
                "interestingness_score": 0.64,
                "closure_score": 0.61,
                "story_boundary_confidence": 0.59,
                "subtitle_quality_score": 0.68,
                "subtitle_text_sanity_score": 0.55,
                "dialogue_flow_score": 0.48,
            }
        }
        reframe_debug = {
            "hard_timeout_triggered": False,
            "anchor_switches": 0,
            "center_safe_fallback_used": False,
            "subject_acquisition_state": "speaker_lock_uncertain",
            "speaker_face_centered_windows": 3,
            "speaker_centered_rate": 0.42,
            "evidence_visible_faces_peak": 2,
            "evidence_visible_persons_peak": 1,
            "face_edge_clip_rate": 0.08,
            "dialogue_mode_windows": 2,
        }

        self.assertEqual(
            pipeline._quality_governor_decision(candidate, subtitle_info, reframe_debug),
            "accept",
        )


if __name__ == "__main__":
    unittest.main()

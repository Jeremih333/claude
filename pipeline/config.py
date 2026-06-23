from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml


DEFAULT_CONFIG = {
    "max_shorts": 50,
    "review_fast_mode_enabled": False,
    "review_fast_output_cap": 8,
    "review_fast_story_candidate_cap": 24,
    "review_fast_reframe_soft_timeout_seconds": 24,
    "review_fast_reframe_hard_timeout_seconds": 40,
    "review_pass_enabled": False,
    "review_pass_min_outputs": 10,
    "review_pass_output_cap": 20,
    "review_pass_face_floor": 0.10,
    "review_pass_min_speech_density": 0.14,
    "review_pass_chain_gap_seconds": 72.0,
    "review_pass_macro_window_seconds": 600,
    "review_pass_max_chain_windows": 4,
    "review_pass_max_stitched_seconds": 60,
    "max_short_seconds": 60,
    "selection_policy": "quality_first",
    "selection_admission_fraction": 0.20,
    "selection_admission_min_pool": 6,
    "selection_admission_max_pool": 48,
    "story_mode": "standard",
    "context_builder": "adjacent_dialogue_merge",
    "keep_dialogue_gap_seconds": 1.0,
    "story_continue_after_silence": False,
    "story_soft_max_seconds": 60,
    "story_hard_max_seconds": 60,
    "tension_context_window_seconds": 1200,
    "tension_target_story_seconds": 45,
    "tension_min_story_seconds": 35,
    "tension_story_soft_max_seconds": 60,
    "tension_story_hard_max_seconds": 60,
    "tension_exceptional_target_seconds": 60,
    "tension_exceptional_max_seconds": 60,
    "tension_pause_cut_threshold_seconds": 1.0,
    "tension_pause_keep_max_seconds": 1.15,
    "tension_dialogue_compact_lead_pad_seconds": 0.06,
    "tension_dialogue_compact_tail_pad_seconds": 0.08,
    "tension_square_canvas_conflict_only": True,
    "tension_admission_fraction": 0.24,
    "story_extension_max_pause_seconds": 1.15,
    "story_extension_bonus_enabled": False,
    "story_stitching_enabled": False,
    "max_stitched_story_seconds": 60,
    "stitch_gap_max_seconds": 1.35,
    "stitch_requires_payoff_gain": True,
    "story_completion_required": True,
    "target_story_min_seconds": 35,
    "target_story_seconds": 45,
    "story_soft_max_seconds": 60,
    "story_window_min_seconds": 35,
    "story_window_max_seconds": 60,
    "story_strong_target_seconds": 45,
    "story_strong_max_seconds": 60,
    "story_exceptional_target_seconds": 60,
    "story_exceptional_max_seconds": 60,
    "allow_story_extension_seconds": 60,
    "min_publishable_seconds": 35,
    "min_exceptional_publishable_seconds": 35,
    "story_merge_gap_seconds": 1.0,
    "segment_merge_gap_seconds": 1.0,
    "segment_merge_semantic_threshold": 0.56,
    "story_thread_window_seconds": 24.0,
    "story_coherence_threshold": 0.62,
    "context_left_pad_seconds": 2.0,
    "context_right_pad_seconds": 1.4,
    "hook_max_lead_seconds": 4.5,
    "story_clarity_threshold": 0.56,
    "clarity_threshold": 0.56,
    "duration_floor_penalty": 0.22,
    "fallback_story_window_enabled": True,
    "interestingness_threshold": 0.52,
    "visual_premise_threshold": 0.56,
    "sound_off_hook_threshold": 0.62,
    "first_second_hook_threshold": 0.60,
    "min_subtitle_turns": 3,
    "line_completion_required": True,
    "reframe_switch_on_dialogue_turn": True,
    "candidate_window_seconds": 35,
    "candidate_step_seconds": 10,
    "min_candidate_seconds": 35,
    "max_candidates_for_rerank": 5,
    "max_candidates_for_semantic_preview": 12,
    "max_stitch_pairs_to_evaluate": 8,
    "ranking_candidate_timeout_seconds": 90,
    "ranking_fallback_timeout_seconds": 18,
    "semantic_preview_candidate_timeout_seconds": 120,
    "heartbeat_interval_seconds": 30,
    "timeout_fallback_enabled": True,
    "watchdog_mode": "hard_kill_subprocess",
    "watchdog_skip_policy": "skip_or_defer",
    "ranking_soft_timeout_seconds": 24,
    "ranking_hard_timeout_seconds": 30,
    "semantic_preview_soft_timeout_seconds": 45,
    "semantic_preview_hard_timeout_seconds": 75,
    "subtitle_soft_timeout_seconds": 90,
    "subtitle_hard_timeout_seconds": 180,
    "reframe_soft_timeout_seconds": 150,
    "reframe_hard_timeout_seconds": 240,
    "deferred_retry_tail_pass": True,
    "target_aspect_ratio": "9:16",
    "selection_profile": "premise_first",
    "quality_profile": "quality_first",
    "throughput_profile": "quality_first_with_broader_publishable_pool",
    "growth_profile": "youtube_shorts_retention_first",
    "packaging_profile": "ru_serial_drama",
    "recommendation_readiness_enabled": True,
    "story_selection_mode": "dialogue_first",
    "transcription_profile": "balanced",
    "quality_mode": "auto",
    "quality_governor_mode": "auto",
    "local_quality_escalation": True,
    "test_mode_enabled": False,
    "test_candidate_rank": 1,
    "subtitle_processing_mode": "balanced_local",
    "subtitle_correction_enabled": True,
    "subtitle_alignment_used": False,
    "subtitle_context_prompt_enabled": True,
    "subtitle_initial_prompt": "",
    "subtitle_retry_confidence_threshold": 0.70,
    "subtitle_retry_text_sanity_threshold": 0.72,
    "subtitle_retry_language_consistency_threshold": 0.84,
    "subtitle_quality_score_threshold": 0.66,
    "speaker_selection_mode": "evidence_scored",
    "speaker_lock_mode": "state_machine",
    "remote_quality_fallback": "off",
    "remote_quality_provider": "",
    "remote_quality_enabled": False,
    "subtitle_language": "auto",
    "subtitle_template": "classic_bold",
    "subtitle_renderer_mode": "persistent_sentence_layer",
    "subtitle_text_sanity_threshold": 0.62,
    "subtitle_vertical_zone": "mid_lower",
    "subtitle_vertical_anchor_mode": "fixed_mid_lower",
    "subtitle_anchor_jitter_tolerance_px": 0,
    "subtitle_compact_mode": True,
    "subtitle_max_visible_lines": 2,
    "subtitle_max_chars_per_block": 26,
    "subtitle_max_visible_words": 3,
    "subtitle_chunk_mode": "sentence_highlight",
    "subtitle_display_mode": "sentence_highlight",
    "subtitle_sentence_max_words": 6,
    "subtitle_sentence_split_mode": "punctuation_pause",
    "subtitle_sentence_context_window": 1,
    "subtitle_hold_max_seconds": 0.48,
    "subtitle_tail_hold_seconds": 0.12,
    "subtitle_phrase_ttl_seconds": 1.05,
    "subtitle_alignment_mode": "word_ts",
    "subtitle_render_mode": "ass_word_highlight",
    "subtitle_hide_when_silent": True,
    "subtitle_persist_gap_seconds": 0.85,  # PHASE 3B: Raised from 0.55 (covers most natural pauses < 1.0s)
    "subtitle_clear_gap_seconds": 1.80,    # PHASE 3B: Raised from 1.35 (prevents overly long stale subtitles)
    "subtitle_continuity_mode": "always_on_short_gaps",
    "subtitle_gap_blink_threshold_ms": 180,
    "subtitle_state_transition_epsilon_ms": 20,
    "subtitle_words_per_batch": 1,
    "subtitle_word_batch_size": 2,
    "subtitle_active_word_color": "#FFD54F",
    "subtitle_remap_after_silence_cut": True,
    "compaction_integrity_check": True,
    "title_generation_enabled": True,
    "title_language": "ru",
    "title_style": "context_clean",
    "title_max_length": 72,
    "title_include_hashtags": True,
    "title_max_hashtags": 2,
    "title_include_emoji": False,
    "title_max_emojis": 1,
    "drop_silent": True,
    "remove_silent": True,
    "silence_cut_mode": "flexible",
    "story_pause_cut_threshold_seconds": 2.0,
    "story_pause_keep_max_seconds": 1.15,
    "cold_open_window_seconds": 3.0,
    "cold_open_dead_time_threshold_seconds": 0.45,
    "pause_keep_event_sensitive": True,
    "min_non_silent_event_energy": 0.16,
    "pause_soft_keep_min_energy": 0.11,
    "pause_story_keep_min_energy": 0.18,
    "dialogue_compact_lead_pad_seconds": 0.08,
    "dialogue_compact_tail_pad_seconds": 0.12,
    "story_boundary_mode": "context_first",
    "story_archetype_detection": False,
    "story_type_repeat_limit": 2,
    "story_type_repeat_penalty": 0.018,
    "story_boundary_expand_left_seconds": 6.0,
    "story_boundary_expand_right_seconds": 3.0,
    "story_boundary_confidence_threshold": 0.58,
    "boundary_retry_limit": 3,
    "publishable_story_override_enabled": False,
    "publishable_story_interest_threshold": 0.60,
    "publishable_story_completeness_threshold": 0.68,
    "publishable_story_watchability_threshold": 0.62,
    "publishable_story_recommendation_threshold": 0.64,
    "selection_visual_subject_soft_floor": 0.28,
    "selection_reframe_soft_floor": 0.22,
    "selection_empty_frame_soft_ceiling": 0.72,
    "final_visual_subject_hard_floor": 0.20,
    "final_reframe_hard_floor": 0.15,
    "ranking_mode": "fast_fallback_first",
    "ranking_fast_pool_multiplier": 2,
    "ranking_fast_pool_min": 10,
    "ranking_pool_cap": 18,
    "ranking_rerank_short_goal_cap": 5,
    "ranking_large_pool_timeout_threshold": 18,
    "ranking_large_pool_soft_timeout_seconds": 18,
    "ranking_large_pool_hard_timeout_seconds": 24,
    "use_visual_asd": True,
    "reframe_mode": "balanced",
    "reframe_priority": "stability_first",
    "reframe_transition_mode": "smooth",
    "reframe_anchor_mode": "stable_primary",
    "reframe_subject_mode": "subject_first",
    "framing_mode": "wide_subject",
    "reframe_scene_interest_fallback": False,
    "scene_interest_fallback_mode": "emergency_only",
    "reframe_listener_face_fallback": False,
    "dialogue_two_shot_preferred": True,
    "empty_frame_guard_enabled": True,
    "speaker_lock_strict_mode": False,
    "speaker_center_strict_mode": False,
    "speaker_center_max_offset": 0.16,
    "speaker_face_lock_min_margin": 0.12,
    "face_preserving_fallback_enabled": False,
    "face_preserving_safe_margin": 0.12,
    "face_preserving_dense_scan_fps": 10,
    "dialogue_center_use_threshold": 0.82,
    "listener_fallback_max_hold_seconds": 0.35,
    "listener_fallback_speech_hold_max_seconds": 0.22,
    "strict_reframe_transition_mode": "hard_switch",
    "accent_frame_hold_windows": 1,
    "accent_frame_hold_story_interest_threshold": 0.74,
    "accent_frame_hold_payoff_threshold": 0.50,
    "subtitle_text_repair_enabled": True,
    "subtitle_mojibake_repair_enabled": True,
    "speaker_min_hold_seconds": 0.9,
    "listener_hold_seconds": 0.40,
    "dialogue_center_min_likelihood": 0.78,
    "dialogue_center_balance_margin": 0.03,
    "reframe_switch_score_margin": 0.08,
    "reframe_subject_confidence_floor": 0.42,
    "subject_visibility_threshold": 0.46,
    "reframe_feasibility_threshold": 0.34,
    "empty_frame_risk_reject_threshold": 0.58,
    "reframe_track_count_limit": 3,
    "reframe_switch_confirm_windows": 3,
    "reframe_lost_face_hold_seconds": 2.2,
    "reframe_dual_face_margin": 0.14,
    "reframe_switch_min_visibility": 0.38,
    "reframe_dual_face_hold": True,
    "reframe_allow_wide_dialogue_center": False,
    "speaker_switch_hold_windows": 1,
    "empty_face_hold_seconds": 2.2,
    "max_crop_shift_per_second": 0.10,
    "max_crop_delta_per_window": 0.05,
    "motion_blend_normal": 0.2,
    "motion_blend_switch": 0.32,
    "reframe_glide_windows": 1,
    "lock_confidence_threshold": 0.72,
    "speaker_confidence_threshold": 0.62,
    "handoff_min_hold_windows": 2,
    "confident_lock_min_hold_windows": 4,
    "target_deadband_handoff": 0.028,
    "target_deadband_lock": 0.018,
    "max_delta_handoff": 0.028,
    "max_delta_lock": 0.020,
    "motion_blend_switch_handoff": 0.22,
    "motion_blend_normal_handoff": 0.14,
    "hook_score_threshold": 0.34,
    "hook_strength_threshold": 0.42,
    "closure_score_threshold": 0.32,
    "min_story_payoff_score": 0.40,
    "payoff_strength_threshold": 0.44,
    "recommendation_readiness_threshold": 0.56,
    "watchability_threshold": 0.54,
    "packaging_quality_threshold": 0.52,
    "story_interest_weight": 0.40,
    "story_completeness_weight": 0.28,
    "story_context_weight": 0.18,
    "story_visual_weight": 0.08,
    "story_subtitle_sanity_weight": 0.06,
    "payoff_after_pause_bonus_enabled": False,
    "ui_language": "ru",
    "output_root": "",
    "analysis_fps": 3,
    "face_detection_fps": 3,
    "active_speaker_mode": "hybrid_subject_first",
    "active_speaker_scan_profile": "light",
    "active_speaker_refine_profile": "final_clip_strong",
    "ranking_visual_precheck_enabled": True,
    "ranking_visual_precheck_seconds": 8.0,
    "ranking_visual_precheck_fps": 1.0,
    "final_crop_visual_probe_enabled": True,
    "final_crop_visual_probe_seconds": 8.0,
    "final_crop_visual_probe_fps": 1.0,
    "subject_detector_final_pass_enabled": True,
    "shot_reacquire_boost_windows": 2,
    "new_face_fast_acquire_threshold": 0.78,
    "vertical_w": 720,
    "vertical_h": 1280,
    "crop_window_sec": 0.8,
    "subtitle_margin_v": 360,
    "subtitle_fontsize": 40,
    "vad_aggressiveness": 2,
    "frame_ms": 30,
    "pad_ms": 180,
    "merge_threshold_ms": 220,
    "min_speech_ms": 180,
    "status_contract": [
        "queued",
        "analyzing",
        "discovering",
        "building_context",
        "ranking",
        "selecting",
        "refining_boundaries",
        "trimming",
        "reframing",
        "subtitling",
        "exporting",
        "titling",
        "done",
        "warning",
        "failed",
    ],
    "scoring_weights": {
        "speech_density": 0.40,
        "silence_penalty": 0.22,
        "face_presence": 0.18,
        "motion": 0.10,
        "audio_energy": 0.10,
    },
}


def normalize_config(cfg: dict | None) -> dict:
    merged = deepcopy(DEFAULT_CONFIG)
    cfg = cfg or {}
    for key, value in cfg.items():
        if key == "scoring_weights" and isinstance(value, dict):
            merged["scoring_weights"].update(value)
        else:
            merged[key] = value
    merged["max_shorts"] = max(1, min(int(merged.get("max_shorts", DEFAULT_CONFIG["max_shorts"]) or DEFAULT_CONFIG["max_shorts"]), 50))
    # Fast mode remains available for diagnostics, but production selection must not be capped at 3.
    merged["review_fast_mode_enabled"] = False
    merged["review_fast_output_cap"] = max(
        8,
        min(int(merged.get("review_fast_output_cap", DEFAULT_CONFIG["review_fast_output_cap"]) or DEFAULT_CONFIG["review_fast_output_cap"]), 12),
    )
    merged["review_fast_story_candidate_cap"] = max(
        8,
        min(
            int(merged.get("review_fast_story_candidate_cap", DEFAULT_CONFIG["review_fast_story_candidate_cap"]) or DEFAULT_CONFIG["review_fast_story_candidate_cap"]),
            40,
        ),
    )
    merged["review_fast_reframe_soft_timeout_seconds"] = max(
        8.0,
        min(
            float(merged.get("review_fast_reframe_soft_timeout_seconds", DEFAULT_CONFIG["review_fast_reframe_soft_timeout_seconds"]) or DEFAULT_CONFIG["review_fast_reframe_soft_timeout_seconds"]),
            30.0,
        ),
    )
    merged["review_fast_reframe_hard_timeout_seconds"] = max(
        12.0,
        min(
            float(merged.get("review_fast_reframe_hard_timeout_seconds", DEFAULT_CONFIG["review_fast_reframe_hard_timeout_seconds"]) or DEFAULT_CONFIG["review_fast_reframe_hard_timeout_seconds"]),
            45.0,
        ),
    )
    merged["review_pass_enabled"] = False
    merged["review_pass_min_outputs"] = max(
        4,
        min(int(merged.get("review_pass_min_outputs", DEFAULT_CONFIG["review_pass_min_outputs"]) or DEFAULT_CONFIG["review_pass_min_outputs"]), 20),
    )
    merged["review_pass_output_cap"] = max(
        merged["review_pass_min_outputs"],
        min(int(merged.get("review_pass_output_cap", DEFAULT_CONFIG["review_pass_output_cap"]) or DEFAULT_CONFIG["review_pass_output_cap"]), 25),
    )
    merged["review_pass_face_floor"] = max(
        0.06,
        min(float(merged.get("review_pass_face_floor", DEFAULT_CONFIG["review_pass_face_floor"]) or DEFAULT_CONFIG["review_pass_face_floor"]), 0.24),
    )
    merged["review_pass_min_speech_density"] = max(
        0.08,
        min(float(merged.get("review_pass_min_speech_density", DEFAULT_CONFIG["review_pass_min_speech_density"]) or DEFAULT_CONFIG["review_pass_min_speech_density"]), 0.30),
    )
    merged["review_pass_chain_gap_seconds"] = max(
        12.0,
        min(float(merged.get("review_pass_chain_gap_seconds", DEFAULT_CONFIG["review_pass_chain_gap_seconds"]) or DEFAULT_CONFIG["review_pass_chain_gap_seconds"]), 120.0),
    )
    merged["review_pass_macro_window_seconds"] = max(
        180,
        min(int(merged.get("review_pass_macro_window_seconds", DEFAULT_CONFIG["review_pass_macro_window_seconds"]) or DEFAULT_CONFIG["review_pass_macro_window_seconds"]), 1200),
    )
    merged["review_pass_max_chain_windows"] = max(
        2,
        min(int(merged.get("review_pass_max_chain_windows", DEFAULT_CONFIG["review_pass_max_chain_windows"]) or DEFAULT_CONFIG["review_pass_max_chain_windows"]), 6),
    )
    merged["review_pass_max_stitched_seconds"] = max(
        30.0,
        min(float(merged.get("review_pass_max_stitched_seconds", DEFAULT_CONFIG["review_pass_max_stitched_seconds"]) or DEFAULT_CONFIG["review_pass_max_stitched_seconds"]), 60.0),
    )
    merged["story_mode"] = str(merged.get("story_mode", DEFAULT_CONFIG["story_mode"]) or DEFAULT_CONFIG["story_mode"]).lower()
    if merged["story_mode"] not in {"standard", "auto", "tension"}:
        merged["story_mode"] = DEFAULT_CONFIG["story_mode"]
    merged["target_story_min_seconds"] = 35
    merged["target_story_seconds"] = 45
    merged["story_soft_max_seconds"] = 60
    merged["story_window_min_seconds"] = 35
    merged["story_window_max_seconds"] = 60
    merged["story_hard_max_seconds"] = 60
    merged["story_strong_target_seconds"] = 45
    merged["story_strong_max_seconds"] = 60
    merged["story_exceptional_target_seconds"] = 60
    merged["story_exceptional_max_seconds"] = 60
    merged["min_publishable_seconds"] = 35
    merged["min_exceptional_publishable_seconds"] = 35
    merged["max_short_seconds"] = 60
    merged["allow_story_extension_seconds"] = 60
    merged["segment_merge_gap_seconds"] = max(
        0.35,
        min(float(merged.get("segment_merge_gap_seconds", DEFAULT_CONFIG["segment_merge_gap_seconds"]) or DEFAULT_CONFIG["segment_merge_gap_seconds"]), 2.5),
    )
    merged["segment_merge_semantic_threshold"] = max(
        0.34,
        min(float(merged.get("segment_merge_semantic_threshold", DEFAULT_CONFIG["segment_merge_semantic_threshold"]) or DEFAULT_CONFIG["segment_merge_semantic_threshold"]), 0.88),
    )
    merged["story_thread_window_seconds"] = max(
        12.0,
        min(float(merged.get("story_thread_window_seconds", DEFAULT_CONFIG["story_thread_window_seconds"]) or DEFAULT_CONFIG["story_thread_window_seconds"]), 48.0),
    )
    merged["story_coherence_threshold"] = max(
        0.42,
        min(float(merged.get("story_coherence_threshold", DEFAULT_CONFIG["story_coherence_threshold"]) or DEFAULT_CONFIG["story_coherence_threshold"]), 0.88),
    )
    merged["clarity_threshold"] = max(
        0.40,
        min(float(merged.get("clarity_threshold", DEFAULT_CONFIG["clarity_threshold"]) or DEFAULT_CONFIG["clarity_threshold"]), 0.92),
    )
    merged["duration_floor_penalty"] = max(
        0.10,
        min(float(merged.get("duration_floor_penalty", DEFAULT_CONFIG["duration_floor_penalty"]) or DEFAULT_CONFIG["duration_floor_penalty"]), 0.50),
    )
    merged["fallback_story_window_enabled"] = bool(merged.get("fallback_story_window_enabled", DEFAULT_CONFIG["fallback_story_window_enabled"]))
    merged["keep_dialogue_gap_seconds"] = max(
        1.0,
        min(float(merged.get("keep_dialogue_gap_seconds", DEFAULT_CONFIG["keep_dialogue_gap_seconds"]) or DEFAULT_CONFIG["keep_dialogue_gap_seconds"]), 1.4),
    )
    merged["selection_admission_fraction"] = max(
        0.10,
        min(float(merged.get("selection_admission_fraction", DEFAULT_CONFIG["selection_admission_fraction"]) or DEFAULT_CONFIG["selection_admission_fraction"]), 0.35),
    )
    merged["selection_admission_min_pool"] = max(
        4,
        min(int(merged.get("selection_admission_min_pool", DEFAULT_CONFIG["selection_admission_min_pool"]) or DEFAULT_CONFIG["selection_admission_min_pool"]), 12),
    )
    merged["selection_admission_max_pool"] = max(
        merged["selection_admission_min_pool"],
        min(int(merged.get("selection_admission_max_pool", DEFAULT_CONFIG["selection_admission_max_pool"]) or DEFAULT_CONFIG["selection_admission_max_pool"]), 64),
    )
    merged["tension_context_window_seconds"] = max(
        600,
        min(int(merged.get("tension_context_window_seconds", DEFAULT_CONFIG["tension_context_window_seconds"]) or DEFAULT_CONFIG["tension_context_window_seconds"]), 1800),
    )
    merged["tension_target_story_seconds"] = max(
        35,
        min(int(merged.get("tension_target_story_seconds", DEFAULT_CONFIG["tension_target_story_seconds"]) or DEFAULT_CONFIG["tension_target_story_seconds"]), 60),
    )
    merged["tension_min_story_seconds"] = max(
        35,
        min(int(merged.get("tension_min_story_seconds", DEFAULT_CONFIG["tension_min_story_seconds"]) or DEFAULT_CONFIG["tension_min_story_seconds"]), merged["tension_target_story_seconds"]),
    )
    merged["tension_story_soft_max_seconds"] = max(
        merged["tension_target_story_seconds"],
        min(int(merged.get("tension_story_soft_max_seconds", DEFAULT_CONFIG["tension_story_soft_max_seconds"]) or DEFAULT_CONFIG["tension_story_soft_max_seconds"]), 60),
    )
    merged["tension_story_hard_max_seconds"] = max(
        merged["tension_story_soft_max_seconds"],
        min(int(merged.get("tension_story_hard_max_seconds", DEFAULT_CONFIG["tension_story_hard_max_seconds"]) or DEFAULT_CONFIG["tension_story_hard_max_seconds"]), 60),
    )
    merged["tension_exceptional_target_seconds"] = max(
        merged["tension_target_story_seconds"],
        min(int(merged.get("tension_exceptional_target_seconds", DEFAULT_CONFIG["tension_exceptional_target_seconds"]) or DEFAULT_CONFIG["tension_exceptional_target_seconds"]), merged["tension_story_hard_max_seconds"]),
    )
    merged["tension_exceptional_max_seconds"] = max(
        merged["tension_story_soft_max_seconds"],
        min(int(merged.get("tension_exceptional_max_seconds", DEFAULT_CONFIG["tension_exceptional_max_seconds"]) or DEFAULT_CONFIG["tension_exceptional_max_seconds"]), 60),
    )
    merged["tension_pause_cut_threshold_seconds"] = max(
        1.0,
        min(float(merged.get("tension_pause_cut_threshold_seconds", DEFAULT_CONFIG["tension_pause_cut_threshold_seconds"]) or DEFAULT_CONFIG["tension_pause_cut_threshold_seconds"]), 1.4),
    )
    merged["tension_pause_keep_max_seconds"] = max(
        merged["tension_pause_cut_threshold_seconds"],
        min(float(merged.get("tension_pause_keep_max_seconds", DEFAULT_CONFIG["tension_pause_keep_max_seconds"]) or DEFAULT_CONFIG["tension_pause_keep_max_seconds"]), 1.6),
    )
    merged["tension_dialogue_compact_lead_pad_seconds"] = max(
        0.02,
        min(float(merged.get("tension_dialogue_compact_lead_pad_seconds", DEFAULT_CONFIG["tension_dialogue_compact_lead_pad_seconds"]) or DEFAULT_CONFIG["tension_dialogue_compact_lead_pad_seconds"]), 0.18),
    )
    merged["tension_dialogue_compact_tail_pad_seconds"] = max(
        0.02,
        min(float(merged.get("tension_dialogue_compact_tail_pad_seconds", DEFAULT_CONFIG["tension_dialogue_compact_tail_pad_seconds"]) or DEFAULT_CONFIG["tension_dialogue_compact_tail_pad_seconds"]), 0.18),
    )
    merged["tension_square_canvas_conflict_only"] = bool(merged.get("tension_square_canvas_conflict_only", DEFAULT_CONFIG["tension_square_canvas_conflict_only"]))
    merged["tension_admission_fraction"] = max(
        merged["selection_admission_fraction"],
        min(float(merged.get("tension_admission_fraction", DEFAULT_CONFIG["tension_admission_fraction"]) or DEFAULT_CONFIG["tension_admission_fraction"]), 0.40),
    )
    merged["subtitle_hide_when_silent"] = bool(merged.get("subtitle_hide_when_silent", DEFAULT_CONFIG["subtitle_hide_when_silent"]))
    merged["subtitle_continuity_mode"] = str(
        merged.get("subtitle_continuity_mode", DEFAULT_CONFIG["subtitle_continuity_mode"]) or DEFAULT_CONFIG["subtitle_continuity_mode"]
    ).lower()
    if merged["subtitle_continuity_mode"] not in {"always_on_short_gaps", "always_on", "off"}:
        merged["subtitle_continuity_mode"] = DEFAULT_CONFIG["subtitle_continuity_mode"]
    if merged["subtitle_hide_when_silent"] and merged["subtitle_continuity_mode"] == "always_on":
        merged["subtitle_continuity_mode"] = "always_on_short_gaps"
    merged["subtitle_persist_gap_seconds"] = max(
        0.18,
        min(float(merged.get("subtitle_persist_gap_seconds", DEFAULT_CONFIG["subtitle_persist_gap_seconds"]) or DEFAULT_CONFIG["subtitle_persist_gap_seconds"]), 0.85),
    )
    merged["subtitle_clear_gap_seconds"] = max(
        merged["subtitle_persist_gap_seconds"],
        min(float(merged.get("subtitle_clear_gap_seconds", DEFAULT_CONFIG["subtitle_clear_gap_seconds"]) or DEFAULT_CONFIG["subtitle_clear_gap_seconds"]), 2.0),
    )
    merged["story_pause_cut_threshold_seconds"] = max(
        1.0,
        min(float(merged.get("story_pause_cut_threshold_seconds", DEFAULT_CONFIG["story_pause_cut_threshold_seconds"]) or DEFAULT_CONFIG["story_pause_cut_threshold_seconds"]), 2.2),
    )
    merged["story_pause_keep_max_seconds"] = max(
        merged["story_pause_cut_threshold_seconds"],
        min(float(merged.get("story_pause_keep_max_seconds", DEFAULT_CONFIG["story_pause_keep_max_seconds"]) or DEFAULT_CONFIG["story_pause_keep_max_seconds"]), 1.6),
    )
    merged["story_extension_max_pause_seconds"] = max(
        merged["story_pause_cut_threshold_seconds"],
        min(float(merged.get("story_extension_max_pause_seconds", DEFAULT_CONFIG["story_extension_max_pause_seconds"]) or DEFAULT_CONFIG["story_extension_max_pause_seconds"]), 2.6),
    )
    merged["story_merge_gap_seconds"] = max(
        1.0,
        min(float(merged.get("story_merge_gap_seconds", DEFAULT_CONFIG["story_merge_gap_seconds"]) or DEFAULT_CONFIG["story_merge_gap_seconds"]), 1.6),
    )
    if bool(merged.get("title_include_hashtags", DEFAULT_CONFIG["title_include_hashtags"])):
        merged["title_max_hashtags"] = max(
            2,
            min(int(merged.get("title_max_hashtags", DEFAULT_CONFIG["title_max_hashtags"]) or DEFAULT_CONFIG["title_max_hashtags"]), 3),
        )
    else:
        merged["title_max_hashtags"] = max(
            0,
            min(int(merged.get("title_max_hashtags", DEFAULT_CONFIG["title_max_hashtags"]) or DEFAULT_CONFIG["title_max_hashtags"]), 3),
        )
    title_style = str(merged.get("title_style", DEFAULT_CONFIG["title_style"]) or DEFAULT_CONFIG["title_style"]).lower()
    if title_style == "viral_soft":
        title_style = "retention_soft"
    merged["title_style"] = title_style if title_style in {"context_clean", "dramatic", "retention_soft"} else DEFAULT_CONFIG["title_style"]
    merged["candidate_window_seconds"] = max(
        35,
        min(int(merged.get("candidate_window_seconds", DEFAULT_CONFIG["candidate_window_seconds"]) or DEFAULT_CONFIG["candidate_window_seconds"]), 60),
    )
    merged["candidate_step_seconds"] = max(
        8,
        min(int(merged.get("candidate_step_seconds", DEFAULT_CONFIG["candidate_step_seconds"]) or DEFAULT_CONFIG["candidate_step_seconds"]), 20),
    )
    merged["ranking_pool_cap"] = 18
    merged["ranking_rerank_short_goal_cap"] = 5
    merged["ranking_large_pool_timeout_threshold"] = 18
    merged["ranking_large_pool_soft_timeout_seconds"] = 18
    merged["ranking_large_pool_hard_timeout_seconds"] = 24
    merged["ranking_fast_pool_multiplier"] = 2
    merged["ranking_fast_pool_min"] = 10
    merged["ranking_soft_timeout_seconds"] = 24
    merged["ranking_hard_timeout_seconds"] = 30
    merged["max_candidates_for_rerank"] = max(4, min(int(merged.get("max_candidates_for_rerank", DEFAULT_CONFIG["max_candidates_for_rerank"]) or DEFAULT_CONFIG["max_candidates_for_rerank"]), 5))
    merged["max_candidates_for_semantic_preview"] = max(8, min(int(merged.get("max_candidates_for_semantic_preview", DEFAULT_CONFIG["max_candidates_for_semantic_preview"]) or DEFAULT_CONFIG["max_candidates_for_semantic_preview"]), 12))
    remote_mode = merged.get("remote_quality_fallback", "off")
    if isinstance(remote_mode, bool):
        merged["remote_quality_fallback"] = "off" if remote_mode is False else "manual"
    else:
        merged["remote_quality_fallback"] = str(remote_mode or "off")
    merged["speaker_selection_mode"] = str(merged.get("speaker_selection_mode", "evidence_scored") or "evidence_scored")
    if str(merged.get("active_speaker_scan_profile", "light") or "light").lower() == "episode_light":
        merged["active_speaker_scan_profile"] = "light"
    else:
        merged["active_speaker_scan_profile"] = str(merged.get("active_speaker_scan_profile", "light") or "light")
    merged["ranking_visual_precheck_enabled"] = bool(merged.get("ranking_visual_precheck_enabled", DEFAULT_CONFIG["ranking_visual_precheck_enabled"]))
    merged["ranking_visual_precheck_seconds"] = max(
        3.0,
        min(float(merged.get("ranking_visual_precheck_seconds", DEFAULT_CONFIG["ranking_visual_precheck_seconds"]) or DEFAULT_CONFIG["ranking_visual_precheck_seconds"]), 12.0),
    )
    merged["ranking_visual_precheck_fps"] = max(
        1.0,
        min(float(merged.get("ranking_visual_precheck_fps", DEFAULT_CONFIG["ranking_visual_precheck_fps"]) or DEFAULT_CONFIG["ranking_visual_precheck_fps"]), 2.0),
    )
    merged["final_crop_visual_probe_enabled"] = bool(merged.get("final_crop_visual_probe_enabled", DEFAULT_CONFIG["final_crop_visual_probe_enabled"]))
    merged["final_crop_visual_probe_seconds"] = max(
        2.0,
        min(float(merged.get("final_crop_visual_probe_seconds", DEFAULT_CONFIG["final_crop_visual_probe_seconds"]) or DEFAULT_CONFIG["final_crop_visual_probe_seconds"]), 12.0),
    )
    merged["final_crop_visual_probe_fps"] = max(
        0.5,
        min(float(merged.get("final_crop_visual_probe_fps", DEFAULT_CONFIG["final_crop_visual_probe_fps"]) or DEFAULT_CONFIG["final_crop_visual_probe_fps"]), 2.0),
    )
    merged["subtitle_processing_mode"] = str(merged.get("subtitle_processing_mode", "balanced_local") or "balanced_local")
    merged["reframe_priority"] = str(merged.get("reframe_priority", "stability_first") or "stability_first")
    merged["story_stitching_enabled"] = bool(merged.get("story_stitching_enabled", DEFAULT_CONFIG["story_stitching_enabled"]))
    merged["story_extension_bonus_enabled"] = bool(merged.get("story_extension_bonus_enabled", DEFAULT_CONFIG["story_extension_bonus_enabled"]))
    merged["publishable_story_override_enabled"] = bool(merged.get("publishable_story_override_enabled", DEFAULT_CONFIG["publishable_story_override_enabled"]))
    merged["reframe_scene_interest_fallback"] = bool(merged.get("reframe_scene_interest_fallback", DEFAULT_CONFIG["reframe_scene_interest_fallback"]))
    merged["reframe_listener_face_fallback"] = bool(merged.get("reframe_listener_face_fallback", DEFAULT_CONFIG["reframe_listener_face_fallback"]))
    merged["dialogue_two_shot_preferred"] = bool(merged.get("dialogue_two_shot_preferred", DEFAULT_CONFIG["dialogue_two_shot_preferred"]))
    merged["speaker_lock_strict_mode"] = bool(merged.get("speaker_lock_strict_mode", DEFAULT_CONFIG["speaker_lock_strict_mode"]))
    merged["speaker_center_strict_mode"] = bool(merged.get("speaker_center_strict_mode", DEFAULT_CONFIG["speaker_center_strict_mode"]))
    return merged


def load_config(path: str | Path) -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return normalize_config({})
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    return normalize_config(raw if isinstance(raw, dict) else {})


def save_config(path: str | Path, cfg: dict) -> None:
    cfg_path = Path(path)
    cfg_path.write_text(
        yaml.safe_dump(normalize_config(cfg), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

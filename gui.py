import json
import json
import os
import re
import subprocess
import sys
import threading
import time
import warnings
from collections import Counter
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from PySide6.QtWidgets import QFileDialog, QMessageBox

from diagnostics import run_diagnostics_text
from pipeline.config import load_config, save_config
from pipeline.feedback_store import append_feedback_event, rank_assisted_candidates

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
    module="webrtcvad",
)

try:
    from pipeline.highlight import Pipeline
    PIPELINE_AVAILABLE = True
except Exception as exc:
    print(f"[WARN] Pipeline import failed: {exc}", flush=True)
    Pipeline = None
    PIPELINE_AVAILABLE = False


CONFIG_BINDINGS = [
    ("ui_language_combo", "ui_language", str, "ru"),
    ("quality_mode_combo", "quality_mode", str, "auto"),
    ("test_mode_check", "test_mode_enabled", bool, False),
    ("test_candidate_spin", "test_candidate_rank", int, 1),
    ("max_shorts_spin", "max_shorts", int, 50),
    ("max_duration_spin", "max_short_seconds", int, 60),
    ("profile_combo", "transcription_profile", str, "fast"),
    ("subtitle_processing_combo", "subtitle_processing_mode", str, "balanced_local"),
    ("reframe_combo", "reframe_mode", str, "balanced"),
    ("reframe_priority_combo", "reframe_priority", str, "stability_first"),
    ("growth_profile_combo", "growth_profile", str, "youtube_shorts_retention_first"),
    ("packaging_profile_combo", "packaging_profile", str, "ru_serial_drama"),
    ("language_combo", "subtitle_language", str, "auto"),
    ("selection_policy_combo", "selection_policy", str, "quality_first"),
    ("story_mode_combo", "story_mode", str, "standard"),
    ("story_target_spin", "target_story_seconds", int, 30),
    ("story_min_spin", "target_story_min_seconds", int, 20),
    ("interestingness_spin", "interestingness_threshold", float, 0.52),
    ("subtitle_template_combo", "subtitle_template", str, "classic_bold"),
    ("subtitle_render_combo", "subtitle_render_mode", str, "ass_word_highlight"),
    ("subtitle_display_combo", "subtitle_display_mode", str, "sentence_highlight"),
    ("subtitle_compact_check", "subtitle_compact_mode", bool, True),
    ("subtitle_fontsize_spin", "subtitle_fontsize", int, 44),
    ("subtitle_chars_spin", "subtitle_max_chars_per_block", int, 26),
    ("subtitle_words_spin", "subtitle_words_per_batch", int, 1),
    ("subtitle_visible_words_spin", "subtitle_max_visible_words", int, 3),
    ("subtitle_sentence_words_spin", "subtitle_sentence_max_words", int, 9),
    ("subtitle_active_color_edit", "subtitle_active_word_color", str, "#FFD54F"),
    ("reframe_transition_combo", "reframe_transition_mode", str, "smooth"),
    ("reframe_anchor_combo", "reframe_anchor_mode", str, "stable_primary"),
    ("framing_mode_combo", "framing_mode", str, "face_locked"),
    ("reframe_track_limit_spin", "reframe_track_count_limit", int, 3),
    ("reframe_switch_confirm_spin", "reframe_switch_confirm_windows", int, 3),
    ("story_stitching_check", "story_stitching_enabled", bool, False),
    ("subtitle_correction_check", "subtitle_correction_enabled", bool, True),
    ("scene_interest_check", "reframe_scene_interest_fallback", bool, False),
    ("listener_fallback_check", "reframe_listener_face_fallback", bool, False),
    ("remote_quality_combo", "remote_quality_fallback", str, "off"),
    ("remote_quality_enabled_check", "remote_quality_enabled", bool, False),
    ("remote_provider_edit", "remote_quality_provider", str, ""),
    ("title_generation_check", "title_generation_enabled", bool, True),
    ("title_style_combo", "title_style", str, "context_clean"),
    ("title_max_length_spin", "title_max_length", int, 72),
    ("title_hashtags_check", "title_include_hashtags", bool, True),
    ("title_emoji_check", "title_include_emoji", bool, False),
    ("story_continue_check", "story_continue_after_silence", bool, False),
    ("drop_silent_check", "drop_silent", bool, True),
    ("remove_silent_check", "remove_silent", bool, True),
    ("use_visual_asd_check", "use_visual_asd", bool, True),
    ("output_root_edit", "output_root", str, ""),
]


UI_TRANSLATIONS = {
    "ru": {
        "ShortsFactory": "Shorts Factory",
        "Queue": "Очередь",
        "Diagnostics": "Диагностика",
        "Project Settings": "Настройки проекта",
        "Add Episode(s)": "Добавить эпизоды",
        "Clear Queue": "Очистить очередь",
        "Save Settings": "Сохранить настройки",
        "Generate Shorts": "Сгенерировать Shorts",
        "Stop": "Остановить",
        "Episode Queue": "Очередь эпизодов",
        "Open Shorts Folder": "Открыть папку Shorts",
        "Open Selected Short": "Открыть выбранный Short",
        "Generated Shorts": "Готовые Shorts",
        "Selected File Summary": "Сводка по выбранному файлу",
        "Log": "Лог",
        "Run Diagnostics": "Запустить диагностику",
        "Ready": "Готово",
        "Processing": "Обработка",
        "Story stitching": "Склейка соседних сцен",
        "Static subtitle frame": "Статичная рамка субтитров",
        "Use scene-interest fallback": "Fallback на интересный кадр",
        "Keep alternate face fallback": "Держать второе лицо как fallback",
        "Compact subtitles": "Компактные субтитры",
        "Auto-title exported shorts": "Автотайтлы для готовых shorts",
        "Add hashtags": "Добавлять хештеги",
        "Add emojis": "Добавлять эмодзи",
        "Keep pauses shorter than 1s": "Сохранять паузы короче 1 сек",
        "Continue story after brief silence": "Продлевать сцену после короткой паузы",
        "Drop silent parts": "Вырезать пустые паузы",
        "Remove silent scenes": "Убирать silent-сцены",
        "Use visual active-speaker": "Использовать visual active-speaker",
        "Browse": "Обзор",
        "Max shorts per episode": "Максимум Shorts на эпизод",
        "Max short duration (sec)": "Макс. длительность short (сек)",
        "Transcription profile": "Профиль транскрипции",
        "Reframe mode": "Режим reframe",
        "Subtitle language": "Язык субтитров",
        "Selection policy": "Политика отбора",
        "Target story sec": "Целевая длина истории",
        "Min story sec": "Мин. длина истории",
        "Interestingness": "Порог интересности",
        "Subtitle template": "Шаблон субтитров",
        "Subtitle render": "Рендер субтитров",
        "Subtitle display": "Показ субтитров",
        "Reframe transition": "Переход reframe",
        "Reframe anchor": "Якорь reframe",
        "Track limit": "Лимит треков",
        "Subtitle font size": "Размер шрифта",
        "Chars per subtitle block": "Символов в блоке",
        "Highlight color": "Цвет активного слова",
        "Words per highlight batch": "Слов в highlight-блоке",
        "Visible words": "Видимых слов",
        "Sentence max words": "Макс. слов в фразе",
        "Title style": "Стиль названия",
        "Title max length": "Макс. длина названия",
        "Switch confirm": "Подтверждение переключения",
        "Output root": "Папка вывода",
        "No run summary yet for this file.": "Для этого файла пока нет сводки.",
        "Status": "Статус",
        "Outputs": "Результат",
        "Windows": "Окна",
        "Story candidates": "Story-кандидаты",
        "Publishable candidates": "Публикуемые кандидаты",
        "Main rejection reason": "Главная причина отклонения",
        "Interestingness avg": "Средняя интересность",
        "Review required outputs": "Нужно review",
        "Titled outputs": "Тайтлы созданы",
        "Subtitle missing outputs": "Без субтитров",
        "Fallback reframe outputs": "Fallback reframe",
        "Stitched outputs": "Склеенные outputs",
        "Dialogue-center outputs": "Dialogue-center outputs",
        "Selection seconds": "Время отбора",
        "Median stage seconds": "Медиана этапа",
        "Warnings:": "Предупреждения:",
        "Pipeline missing": "Pipeline недоступен",
        "Pipeline is unavailable. Check diagnostics.": "Pipeline недоступен. Проверьте диагностику.",
        "No files": "Нет файлов",
        "Please add at least one source video.": "Добавьте хотя бы один исходный видеофайл.",
        "Select episodes": "Выберите эпизоды",
        "Select output root": "Выберите папку вывода",
        "Not found": "Не найдено",
        "Path does not exist:\n{path}": "Путь не существует:\n{path}",
        "No selection": "Нет выбора",
        "Select a source file first.": "Сначала выберите исходный файл.",
        "No short selected": "Short не выбран",
        "Select a generated short first.": "Сначала выберите готовый short.",
    },
    "en": {},
}


def _widget_get_value(widget):
    if isinstance(widget, QtWidgets.QAbstractButton):
        return widget.isChecked()
    if isinstance(widget, QtWidgets.QComboBox):
        return widget.currentText()
    if isinstance(widget, QtWidgets.QSpinBox):
        return widget.value()
    if isinstance(widget, QtWidgets.QDoubleSpinBox):
        return widget.value()
    if isinstance(widget, QtWidgets.QLineEdit):
        return widget.text().strip()
    raise TypeError(f"Unsupported widget type: {type(widget)!r}")


def _widget_set_value(widget, value):
    if isinstance(widget, QtWidgets.QAbstractButton):
        widget.setChecked(bool(value))
        return
    if isinstance(widget, QtWidgets.QComboBox):
        widget.setCurrentText(str(value))
        return
    if isinstance(widget, QtWidgets.QSpinBox):
        widget.setValue(int(value))
        return
    if isinstance(widget, QtWidgets.QDoubleSpinBox):
        widget.setValue(float(value))
        return
    if isinstance(widget, QtWidgets.QLineEdit):
        widget.setText(str(value))
        return
    raise TypeError(f"Unsupported widget type: {type(widget)!r}")


def create_pipeline_from_config(config_path="settings.yaml"):
    cfg = load_config(config_path)
    pipeline = Pipeline(cfg) if PIPELINE_AVAILABLE and Pipeline is not None else None
    return cfg, pipeline


def load_report_metadata(meta_path: str) -> dict:
    try:
        return json.loads(Path(meta_path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _looks_mojibake(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return False
    marker_hits = sum(cleaned.count(marker) for marker in ("Ð", "Ñ", "Гђ", "Г‘", "Гѓ", "Г‚", "Гўв‚¬", "Гўв‚¬вЂќ", "Гўв‚¬вЂњ", "РЎ", "Рћ", "СЌ", "Сѓ", "Рє"))
    if marker_hits >= 2:
        return True
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", cleaned.lower())
    common_hits = sum(1 for token in tokens if token in {"это", "ведь", "правда", "теперь", "все", "не", "да", "нет", "что", "как"})
    weird_cyrillic_ratio = sum(1 for ch in cleaned if "\u0400" <= ch <= "\u04ff") / max(1, len(cleaned))
    return common_hits == 0 and weird_cyrillic_ratio > 0.38 and len(tokens) >= 2


def summarize_report_for_gui(report: dict) -> dict:
    stats = dict(report.get("stats", {}) or {})
    if isinstance(report.get("gui_summary"), dict):
        summary = dict(report["gui_summary"])
        summary.setdefault("warnings", list(report.get("warnings", []) or []))
        summary.setdefault("review_required", 0)
        summary.setdefault("titled_outputs", summary.get("titles_generated", 0))
        summary.setdefault("subtitle_missing_outputs", 0)
        summary.setdefault("fallback_reframe_outputs", 0)
        summary.setdefault("selection_seconds", (report.get("stage_timings", {}) or {}).get("selection_total_seconds"))
        summary.setdefault("story_candidates", summary.get("total_story_candidates", 0))
        summary.setdefault("windows", summary.get("total_windows", 0))
        summary.setdefault("audio_gate_reasons", stats.get("audio_gate_reasons", {}))
        summary.setdefault("audio_gate_admissions", stats.get("audio_gate_admissions", {}))
        summary.setdefault("audio_summary_cache_hits", stats.get("audio_summary_cache_hits", 0))
        summary.setdefault("audio_summary_cache_misses", stats.get("audio_summary_cache_misses", 0))
        summary.setdefault("episode_audio_cache_hits", stats.get("episode_audio_cache_hits", 0))
        summary.setdefault("episode_audio_cache_misses", stats.get("episode_audio_cache_misses", 0))
        summary.setdefault("dialogue_audio_mismatch_candidates", stats.get("dialogue_audio_mismatch_candidates", 0))
        summary.setdefault("ranking_timeouts", 0)
        summary.setdefault("ranking_fallback_used", 0)
        summary.setdefault("ranking_fast_fallback_used", 0)
        summary.setdefault("ranking_failed", 0)
        summary.setdefault("semantic_preview_timeouts", 0)
        summary.setdefault("semantic_preview_fallback_used", 0)
        summary.setdefault("slow_stage_events", 0)
        summary.setdefault("hard_timeouts", 0)
        summary.setdefault("deferred_candidates", 0)
        summary.setdefault("skipped_due_to_timeout", 0)
        summary.setdefault("watchdog_fallback_used", 0)
        summary.setdefault("avg_hook_strength", None)
        summary.setdefault("avg_watchability_score", None)
        summary.setdefault("avg_recommendation_readiness", None)
        summary.setdefault("avg_packaging_quality", None)
        summary.setdefault("avg_subtitle_confidence", None)
        summary.setdefault("avg_subtitle_text_sanity", None)
        summary.setdefault("avg_subtitle_language_consistency", None)
        summary.setdefault("avg_subtitle_quality_score", None)
        summary.setdefault("avg_subtitle_blackout", None)
        summary.setdefault("weak_cold_open_outputs", 0)
        summary.setdefault("weak_subject_outputs", 0)
        summary.setdefault("weak_packaging_outputs", 0)
        summary.setdefault("publishable_pool_before_final_visual_gate", summary.get("publishable_candidates", 0))
        summary.setdefault("story_override_candidates", 0)
        summary.setdefault("final_visual_rejects", 0)
        summary.setdefault("silent_parts_removed_total", 0)
        summary.setdefault("review_pass_considered", False)
        summary.setdefault("selection_starvation_reasons", {})
        summary.setdefault("selection_starvation_visual", 0)
        summary.setdefault("selection_starvation_subtitle", 0)
        summary.setdefault("selection_starvation_boundary", 0)
        summary.setdefault("selection_starvation_vad", 0)
        summary.setdefault("main_rejection_bucket", None)
        summary.setdefault("speaker_to_listener_switch_rate", None)
        summary.setdefault("subtitle_remap_usage_rate", None)
        summary.setdefault("compaction_integrity_failed_total", 0)
        summary.setdefault("subtitle_blackout_outputs", 0)
        summary.setdefault("speaker_to_listener_switch_outputs", 0)
        summary.setdefault("subtitle_remap_outputs", 0)
        summary.setdefault("compaction_integrity_failed_outputs", 0)
        summary.setdefault("subtitle_quality_retry_outputs", 0)
        summary.setdefault("pause_policy_failed_outputs", 0)
        summary.setdefault("square_reframe_mode_outputs", 0)
        summary.setdefault("end_boundary_completion_ok_outputs", 0)
        summary.setdefault("incomplete_phrase_end_outputs", 0)
        return summary
    stats = dict(report.get("stats", {}) or {})
    generated = list(report.get("generated_outputs", []) or [])
    warnings_list = list(report.get("warnings", []) or [])
    rejection_reason = stats.get("main_rejection_reason")
    if not rejection_reason:
        joined = " | ".join(str(item) for item in warnings_list).lower()
        if "no valid story candidates" in joined:
            rejection_reason = "no_story_candidates"
        elif "no subtitles" in joined:
            rejection_reason = "no_subtitles"
        elif "low subtitle turns" in joined:
            rejection_reason = "low_subtitle_turns"
        elif "subtitle_confidence_low" in joined:
            rejection_reason = "subtitle_confidence_low"
        elif "export_failed" in joined:
            rejection_reason = "export_failed"
    titled = 0
    review_required = 0
    subtitle_missing = 0
    fallback_reframe = 0
    stitched_outputs = 0
    dialogue_center_outputs = 0
    scene_interest_outputs = 0
    subtitle_jitter_suspects = 0
    subtitle_overlap_outputs = 0
    subtitle_blink_outputs = 0
    subtitle_blackout_outputs = 0
    subtitle_visual_drop_outputs = 0
    subtitle_persist_outputs = 0
    subtitle_quality_retry_outputs = 0
    listener_fallback_outputs = 0
    subject_person_outputs = 0
    face_preserving_fallback_outputs = 0
    speaker_to_listener_switch_outputs = 0
    subtitle_remap_outputs = 0
    compaction_integrity_failed_outputs = 0
    subtitle_correction_outputs = 0
    title_mojibake_outputs = 0
    remote_retry_candidates = 0
    auto_quality_retry_outputs = 0
    auto_reframe_retry_outputs = 0
    center_safe_fallback_outputs = 0
    square_reframe_mode_outputs = 0
    end_boundary_completion_ok_outputs = 0
    incomplete_phrase_end_outputs = 0
    tension_mode_outputs = 0
    story_mode_counts = Counter()
    acquisition_state_counts = Counter()
    acquisition_outcome_counts = Counter()
    anchor_switch_values = []
    interestingness_values = []
    hook_values = []
    visual_premise_values = []
    visible_stakes_values = []
    first_frame_clarity_values = []
    sound_off_hook_values = []
    sound_off_premise_values = []
    first_second_hook_values = []
    premise_signal_values = []
    dialogue_dependency_values = []
    watchability_values = []
    recommendation_values = []
    packaging_values = []
    subtitle_confidence_values = []
    subtitle_text_sanity_values = []
    subtitle_language_consistency_values = []
    subtitle_quality_values = []
    final_duration_values = []
    duration_policy_bands = Counter()
    speaker_transition_direct_total = 0
    speaker_switch_latency_total = 0
    handoff_glide_total = 0
    accent_frame_hold_total = 0
    weak_cold_open_outputs = 0
    weak_premise_outputs = 0
    weak_subject_outputs = 0
    weak_packaging_outputs = 0
    for output in generated:
        meta = load_report_metadata(output.get("metadata", ""))
        if meta.get("generated_title"):
            titled += 1
            if _looks_mojibake(str(meta.get("generated_title", ""))):
                title_mojibake_outputs += 1
        if meta.get("needs_review"):
            review_required += 1
        if isinstance(meta.get("interestingness_score"), (int, float)):
            interestingness_values.append(float(meta.get("interestingness_score")))
        if isinstance(meta.get("hook_strength"), (int, float)):
            hook_values.append(float(meta.get("hook_strength")))
        if isinstance(meta.get("visual_premise_strength"), (int, float)):
            visual_premise_values.append(float(meta.get("visual_premise_strength")))
        if isinstance(meta.get("visible_stakes_score"), (int, float)):
            visible_stakes_values.append(float(meta.get("visible_stakes_score")))
        if isinstance(meta.get("first_frame_clarity_score"), (int, float)):
            first_frame_clarity_values.append(float(meta.get("first_frame_clarity_score")))
        if isinstance(meta.get("sound_off_hook_score"), (int, float)):
            sound_off_hook_values.append(float(meta.get("sound_off_hook_score")))
        if isinstance(meta.get("sound_off_premise_score"), (int, float)):
            sound_off_premise_values.append(float(meta.get("sound_off_premise_score")))
        if isinstance(meta.get("first_second_hook_score"), (int, float)):
            first_second_hook_values.append(float(meta.get("first_second_hook_score")))
        if isinstance(meta.get("premise_signal_score"), (int, float)):
            premise_signal_values.append(float(meta.get("premise_signal_score")))
        if isinstance(meta.get("dialogue_dependency_penalty"), (int, float)):
            dialogue_dependency_values.append(float(meta.get("dialogue_dependency_penalty")))
        if isinstance(meta.get("watchability_score"), (int, float)):
            watchability_values.append(float(meta.get("watchability_score")))
        if isinstance(meta.get("recommendation_readiness_score"), (int, float)):
            recommendation_values.append(float(meta.get("recommendation_readiness_score")))
        if isinstance(meta.get("packaging_quality_score"), (int, float)):
            packaging_values.append(float(meta.get("packaging_quality_score")))
        if isinstance(meta.get("subtitle_confidence"), (int, float)):
            subtitle_confidence_values.append(float(meta.get("subtitle_confidence")))
        if isinstance(meta.get("subtitle_text_sanity_score"), (int, float)):
            subtitle_text_sanity_values.append(float(meta.get("subtitle_text_sanity_score")))
        if isinstance(meta.get("subtitle_language_consistency"), (int, float)):
            subtitle_language_consistency_values.append(float(meta.get("subtitle_language_consistency")))
        if isinstance(meta.get("subtitle_quality_score"), (int, float)):
            subtitle_quality_values.append(float(meta.get("subtitle_quality_score")))
        if isinstance(meta.get("final_duration"), (int, float)):
            final_duration_values.append(float(meta.get("final_duration")))
        duration_policy_band = str(meta.get("duration_policy_band", "") or "")
        if duration_policy_band:
            duration_policy_bands[duration_policy_band] += 1
        story_mode = str(meta.get("story_mode", "") or "")
        if story_mode:
            story_mode_counts[story_mode] += 1
        if meta.get("tension_mode_active"):
            tension_mode_outputs += 1
        speaker_transition_direct_total += int(meta.get("speaker_transition_direct_windows", 0) or 0)
        speaker_switch_latency_total += int(meta.get("speaker_switch_latency_windows", 0) or 0)
        handoff_glide_total += int((meta.get("speaker_lock_state_usage") or {}).get("handoff_glide_windows", meta.get("handoff_glide_windows", 0)) or 0)
        accent_frame_hold_total += int(meta.get("accent_frame_hold_windows", 0) or 0)
        if meta.get("subtitle_status") in {"missing", "generated"} and not meta.get("subtitle_confidence"):
            subtitle_missing += 1
        if meta.get("active_speaker_fallback_used"):
            fallback_reframe += 1
        if meta.get("stitched_story_unit"):
            stitched_outputs += 1
        if meta.get("reframe_dialogue_center_used"):
            dialogue_center_outputs += 1
        if meta.get("reframe_listener_face_fallback_used"):
            listener_fallback_outputs += 1
        if meta.get("subject_person_fallback_used"):
            subject_person_outputs += 1
        if meta.get("subtitle_correction_used"):
            subtitle_correction_outputs += 1
        if meta.get("subtitle_quality_retry_used") or meta.get("auto_quality_retry_used"):
            subtitle_quality_retry_outputs += 1
        if meta.get("auto_quality_retry_used"):
            auto_quality_retry_outputs += 1
        if meta.get("auto_reframe_retry_used"):
            auto_reframe_retry_outputs += 1
        if meta.get("remote_quality_should_retry"):
            remote_retry_candidates += 1
        if meta.get("reframe_scene_interest_fallback_used"):
            scene_interest_outputs += 1
        if meta.get("center_safe_fallback_used"):
            center_safe_fallback_outputs += 1
        if meta.get("face_preserving_fallback_used"):
            face_preserving_fallback_outputs += 1
        if meta.get("square_reframe_mode_used"):
            square_reframe_mode_outputs += 1
        if meta.get("end_boundary_completion_ok"):
            end_boundary_completion_ok_outputs += 1
        if int(meta.get("incomplete_phrase_end_count", 0) or 0) > 0:
            incomplete_phrase_end_outputs += 1
        if float(meta.get("subtitle_anchor_jitter_px", 0) or 0) > 0:
            subtitle_jitter_suspects += 1
        if int(meta.get("subtitle_event_overlap_count", 0) or 0) > 0:
            subtitle_overlap_outputs += 1
        if int(meta.get("subtitle_gap_blink_count", 0) or 0) > 0:
            subtitle_blink_outputs += 1
        if int(meta.get("subtitle_visual_drop_count", 0) or 0) > 0:
            subtitle_visual_drop_outputs += 1
        if int(meta.get("subtitle_blackout_count", 0) or 0) > 0:
            subtitle_blackout_outputs += 1
        if int(meta.get("subtitle_persisted_gaps_count", 0) or 0) > 0:
            subtitle_persist_outputs += 1
        if int(meta.get("reframe_speaker_to_listener_switches", 0) or 0) > 0:
            speaker_to_listener_switch_outputs += 1
        if isinstance(meta.get("reframe_anchor_switches"), (int, float)):
            anchor_switch_values.append(float(meta.get("reframe_anchor_switches")))
        if bool(meta.get("subtitle_remap_used", False)):
            subtitle_remap_outputs += 1
        if bool(meta.get("compaction_integrity_failed", False)):
            compaction_integrity_failed_outputs += 1
        if float(meta.get("cold_open_dead_time_penalty", 0.0) or 0.0) > 0.0:
            weak_cold_open_outputs += 1
        premise_strength = max(
            float(meta.get("visual_premise_strength", 0.0) or 0.0),
            float(meta.get("sound_off_hook_score", 0.0) or 0.0),
            float(meta.get("first_second_hook_score", 0.0) or 0.0),
            float(meta.get("premise_signal_score", 0.0) or 0.0),
        )
        if premise_strength < float(meta.get("visual_premise_threshold", 0.48) or 0.48):
            weak_premise_outputs += 1
        if float(meta.get("subject_visibility_ratio", 1.0) or 0.0) < float(meta.get("subject_visibility_threshold", 0.46) or 0.46) or int(meta.get("no_subject_windows", 0) or 0) > 0:
            weak_subject_outputs += 1
        if float(meta.get("packaging_quality_score", 1.0) or 0.0) < 0.52:
            weak_packaging_outputs += 1
        acquisition_state = str(meta.get("subject_acquisition_state", "") or "")
        if acquisition_state:
            acquisition_state_counts[acquisition_state] += 1
        acquisition_outcome = str(meta.get("subject_acquisition_outcome", acquisition_state) or "")
        if acquisition_outcome:
            acquisition_outcome_counts[acquisition_outcome] += 1
    timings = dict(report.get("stage_timings", {}) or {})
    return {
        "status": report.get("status", "unknown"),
        "story_mode": str(report.get("story_mode", "") or ""),
        "episode_story_mode": str(stats.get("episode_story_mode", report.get("story_mode", "") or "") or ""),
        "requested_max": report.get("requested_max"),
        "outputs": len(generated),
        "warnings_count": len(warnings_list),
        "main_rejection_reason": rejection_reason,
        "windows": int(stats.get("total_windows", 0) or 0),
        "story_candidates": int(stats.get("total_story_candidates", 0) or 0),
        "publishable_candidates": int(stats.get("publishable_candidates", 0) or 0),
        "publishable_pool_before_final_visual_gate": int(stats.get("publishable_pool_before_final_visual_gate", stats.get("publishable_candidates", 0)) or 0),
        "episode_output_budget": int(stats.get("episode_output_budget", 0) or 0),
        "episode_quality_floor": float(stats.get("episode_quality_floor", 0.0) or 0.0),
        "episode_tension_density": float(stats.get("episode_tension_density", 0.0) or 0.0),
        "episode_arc_count": int(stats.get("episode_arc_count", 0) or 0),
        "selection_admission_fraction": float(stats.get("selection_admission_fraction", 0.0) or 0.0),
        "selection_admission_target": int(stats.get("selection_admission_target", 0) or 0),
        "selection_admission_cap": int(stats.get("selection_admission_cap", 0) or 0),
        "selection_admission_pool": int(stats.get("selection_admission_pool", 0) or 0),
        "story_override_candidates": int(stats.get("story_override_candidates", 0) or 0),
        "review_required": review_required,
        "titled_outputs": titled,
        "interestingness_avg": round(sum(interestingness_values) / len(interestingness_values), 4) if interestingness_values else None,
        "avg_hook_strength": round(sum(hook_values) / len(hook_values), 4) if hook_values else None,
        "avg_visual_premise_strength": round(sum(visual_premise_values) / len(visual_premise_values), 4) if visual_premise_values else None,
        "avg_visible_stakes_score": round(sum(visible_stakes_values) / len(visible_stakes_values), 4) if visible_stakes_values else None,
        "avg_first_frame_clarity_score": round(sum(first_frame_clarity_values) / len(first_frame_clarity_values), 4) if first_frame_clarity_values else None,
        "avg_sound_off_hook_score": round(sum(sound_off_hook_values) / len(sound_off_hook_values), 4) if sound_off_hook_values else None,
        "avg_sound_off_premise_score": round(sum(sound_off_premise_values) / len(sound_off_premise_values), 4) if sound_off_premise_values else None,
        "avg_first_second_hook_score": round(sum(first_second_hook_values) / len(first_second_hook_values), 4) if first_second_hook_values else None,
        "avg_premise_signal_score": round(sum(premise_signal_values) / len(premise_signal_values), 4) if premise_signal_values else None,
        "avg_dialogue_dependency_penalty": round(sum(dialogue_dependency_values) / len(dialogue_dependency_values), 4) if dialogue_dependency_values else None,
        "avg_watchability_score": round(sum(watchability_values) / len(watchability_values), 4) if watchability_values else None,
        "avg_recommendation_readiness": round(sum(recommendation_values) / len(recommendation_values), 4) if recommendation_values else None,
        "avg_packaging_quality": round(sum(packaging_values) / len(packaging_values), 4) if packaging_values else None,
        "avg_subtitle_confidence": round(sum(subtitle_confidence_values) / len(subtitle_confidence_values), 4) if subtitle_confidence_values else None,
        "avg_subtitle_text_sanity": round(sum(subtitle_text_sanity_values) / len(subtitle_text_sanity_values), 4) if subtitle_text_sanity_values else None,
        "avg_subtitle_language_consistency": round(sum(subtitle_language_consistency_values) / len(subtitle_language_consistency_values), 4) if subtitle_language_consistency_values else None,
        "avg_subtitle_quality_score": round(sum(subtitle_quality_values) / len(subtitle_quality_values), 4) if subtitle_quality_values else None,
        "avg_final_duration": round(sum(final_duration_values) / len(final_duration_values), 2) if final_duration_values else None,
        "duration_policy_bands": dict(duration_policy_bands),
        "story_mode_counts": dict(story_mode_counts),
        "tension_mode_outputs": tension_mode_outputs,
        "speaker_transition_direct_total": speaker_transition_direct_total,
        "speaker_switch_latency_total": speaker_switch_latency_total,
        "handoff_glide_total": handoff_glide_total,
        "accent_frame_hold_total": accent_frame_hold_total,
        "avg_subtitle_blackout": round(subtitle_blackout_outputs / len(generated), 4) if generated else None,
        "subtitle_missing_outputs": subtitle_missing,
        "fallback_reframe_outputs": fallback_reframe,
        "stitched_outputs": stitched_outputs,
        "dialogue_center_outputs": dialogue_center_outputs,
        "scene_interest_outputs": scene_interest_outputs,
        "center_safe_fallback_outputs": center_safe_fallback_outputs,
        "face_preserving_fallback_outputs": face_preserving_fallback_outputs,
        "subject_acquisition_state_counts": dict(acquisition_state_counts),
        "subject_acquisition_outcome_counts": dict(acquisition_outcome_counts),
        "subtitle_jitter_suspects": subtitle_jitter_suspects,
        "subtitle_overlap_outputs": subtitle_overlap_outputs,
        "subtitle_blink_outputs": subtitle_blink_outputs,
        "subtitle_visual_drop_outputs": subtitle_visual_drop_outputs,
        "subtitle_blackout_outputs": subtitle_blackout_outputs,
        "subtitle_persist_outputs": subtitle_persist_outputs,
        "listener_fallback_outputs": listener_fallback_outputs,
        "subject_person_outputs": subject_person_outputs,
        "speaker_to_listener_switch_rate": round(speaker_to_listener_switch_outputs / len(generated), 4) if generated else None,
        "speaker_to_listener_switch_outputs": speaker_to_listener_switch_outputs,
        "subtitle_remap_usage_rate": round(subtitle_remap_outputs / len(generated), 4) if generated else None,
        "subtitle_remap_outputs": subtitle_remap_outputs,
        "compaction_integrity_failed_outputs": compaction_integrity_failed_outputs,
        "subtitle_correction_outputs": subtitle_correction_outputs,
        "subtitle_quality_retry_outputs": subtitle_quality_retry_outputs,
        "title_mojibake_outputs": title_mojibake_outputs,
        "auto_quality_retry_outputs": auto_quality_retry_outputs,
        "auto_reframe_retry_outputs": auto_reframe_retry_outputs,
        "weak_cold_open_outputs": weak_cold_open_outputs,
        "weak_premise_outputs": weak_premise_outputs,
        "weak_subject_outputs": weak_subject_outputs,
        "weak_packaging_outputs": weak_packaging_outputs,
        "remote_retry_candidates": remote_retry_candidates,
        "avg_anchor_switches": round(sum(anchor_switch_values) / len(anchor_switch_values), 2) if anchor_switch_values else 0.0,
        "selection_seconds": timings.get("selection_total_seconds"),
        "median_stage_seconds": timings.get("median_stage_seconds"),
        "ranking_timeouts": int(stats.get("ranking_timeouts", 0) or 0),
        "ranking_fallback_used": int(stats.get("ranking_fallback_used", 0) or 0),
        "ranking_fast_fallback_used": int(stats.get("ranking_fast_fallback_used", 0) or 0),
        "ranking_failed": int(stats.get("ranking_failed", 0) or 0),
        "semantic_preview_timeouts": int(stats.get("semantic_preview_timeouts", 0) or 0),
        "semantic_preview_fallback_used": int(stats.get("semantic_preview_fallback_used", 0) or 0),
        "slow_stage_events": int(stats.get("slow_stage_events", 0) or 0),
        "hard_timeouts": int(stats.get("hard_timeouts", 0) or 0),
        "deferred_candidates": int(stats.get("deferred_candidates", 0) or 0),
        "skipped_due_to_timeout": int(stats.get("skipped_due_to_timeout", 0) or 0),
        "watchdog_fallback_used": int(stats.get("watchdog_fallback_used", 0) or 0),
        "final_visual_rejects": int(stats.get("final_visual_rejects", 0) or 0),
        "compaction_integrity_failed_total": compaction_integrity_failed_outputs,
        "silent_parts_removed_total": int(stats.get("silent_parts_removed_total", 0) or 0),
        "pause_policy_failed_outputs": int(stats.get("pause_policy_failed_outputs", 0) or 0),
        "square_reframe_mode_outputs": square_reframe_mode_outputs,
        "end_boundary_completion_ok_outputs": end_boundary_completion_ok_outputs,
        "incomplete_phrase_end_outputs": incomplete_phrase_end_outputs,
        "warnings": warnings_list,
    }


def iter_report_log_lines(report: dict):
    summary = summarize_report_for_gui(report)
    yield (
        f"[done] report_summary status={summary.get('status')} outputs={summary.get('outputs')}/"
        f"{summary.get('requested_max')} windows={summary.get('windows')} "
        f"story_candidates={summary.get('story_candidates')} publishable={summary.get('publishable_candidates')}"
    )
    if summary.get("audio_gate_reasons"):
        yield f"[done] audio_gate_reasons={summary.get('audio_gate_reasons')}"
    if summary.get("audio_gate_admissions"):
        yield f"[done] audio_gate_admissions={summary.get('audio_gate_admissions')}"
    if summary.get("audio_summary_cache_hits") is not None or summary.get("audio_summary_cache_misses") is not None:
        yield (
            f"[done] audio_summary_cache={summary.get('audio_summary_cache_hits', 0)}/"
            f"{summary.get('audio_summary_cache_misses', 0)}"
        )
    if summary.get("episode_audio_cache_hits") is not None or summary.get("episode_audio_cache_misses") is not None:
        yield (
            f"[done] episode_audio_cache={summary.get('episode_audio_cache_hits', 0)}/"
            f"{summary.get('episode_audio_cache_misses', 0)}"
        )
    if summary.get("dialogue_audio_mismatch_candidates"):
        yield f"[done] dialogue_audio_mismatch_candidates={summary.get('dialogue_audio_mismatch_candidates')}"
    if summary.get("story_mode"):
        yield f"[done] story_mode={summary.get('story_mode')}"
    if summary.get("episode_story_mode") and summary.get("episode_story_mode") != summary.get("story_mode"):
        yield f"[done] episode_story_mode={summary.get('episode_story_mode')}"
    if summary.get("episode_output_budget"):
        yield f"[done] episode_output_budget={summary.get('episode_output_budget')}"
    if summary.get("episode_quality_floor") is not None:
        yield f"[done] episode_quality_floor={summary.get('episode_quality_floor')}"
    if summary.get("episode_tension_density") is not None:
        yield f"[done] episode_tension_density={summary.get('episode_tension_density')}"
    if summary.get("selection_admission_pool"):
        yield (
            f"[done] selection_admission={summary.get('selection_admission_pool')}/"
            f"{summary.get('story_candidates')} fraction={summary.get('selection_admission_fraction')}"
        )
    if summary.get("publishable_pool_before_final_visual_gate") is not None:
        yield f"[done] publishable_pool_before_final_visual_gate={summary.get('publishable_pool_before_final_visual_gate')}"
    if summary.get("tension_mode_outputs"):
        yield f"[done] tension_mode_outputs={summary.get('tension_mode_outputs')}"
    if summary.get("story_override_candidates"):
        yield f"[done] story_override_candidates={summary.get('story_override_candidates')}"
    if summary.get("review_pass_used"):
        yield (
            f"[done] review_pass_used={summary.get('review_pass_used')} "
            f"rescued={summary.get('review_pass_rescued_outputs', 0)} "
            f"stitched={summary.get('review_pass_stitched_candidates', 0)} "
            f"candidates={summary.get('review_pass_candidates', 0)}"
        )
    if summary.get("review_pass_considered"):
        yield f"[done] review_pass_considered={summary.get('review_pass_considered')}"
    if summary.get("selection_starvation_reasons"):
        yield f"[done] selection_starvation_reasons={summary.get('selection_starvation_reasons')}"
    if any(summary.get(key, 0) for key in ("selection_starvation_visual", "selection_starvation_subtitle", "selection_starvation_boundary", "selection_starvation_vad")):
        yield (
            f"[done] selection_starvation_visual={summary.get('selection_starvation_visual', 0)} "
            f"subtitle={summary.get('selection_starvation_subtitle', 0)} "
            f"boundary={summary.get('selection_starvation_boundary', 0)} "
            f"vad={summary.get('selection_starvation_vad', 0)}"
        )
    if summary.get("main_rejection_reason"):
        yield f"[warning] main_rejection_reason={summary.get('main_rejection_reason')}"
    if summary.get("selection_seconds") is not None:
        yield f"[done] selection_total_seconds={summary.get('selection_seconds')}"
    if summary.get("median_stage_seconds") is not None:
        yield f"[done] median_stage_seconds={summary.get('median_stage_seconds')}"
    if summary.get("titled_outputs"):
        yield f"[done] titled_outputs={summary.get('titled_outputs')}"
    if summary.get("interestingness_avg") is not None:
        yield f"[done] interestingness_avg={summary['interestingness_avg']}"
    if summary.get("avg_hook_strength") is not None:
        yield f"[done] avg_hook_strength={summary['avg_hook_strength']}"
    if summary.get("avg_watchability_score") is not None:
        yield f"[done] avg_watchability_score={summary['avg_watchability_score']}"
    if summary.get("avg_recommendation_readiness") is not None:
        yield f"[done] avg_recommendation_readiness={summary['avg_recommendation_readiness']}"
    if summary.get("avg_packaging_quality") is not None:
        yield f"[done] avg_packaging_quality={summary['avg_packaging_quality']}"
    if summary.get("avg_subtitle_confidence") is not None:
        yield f"[done] avg_subtitle_confidence={summary['avg_subtitle_confidence']}"
    if summary.get("avg_subtitle_text_sanity") is not None:
        yield f"[done] avg_subtitle_text_sanity={summary['avg_subtitle_text_sanity']}"
    if summary.get("avg_subtitle_language_consistency") is not None:
        yield f"[done] avg_subtitle_language_consistency={summary['avg_subtitle_language_consistency']}"
    if summary.get("avg_subtitle_quality_score") is not None:
        yield f"[done] avg_subtitle_quality_score={summary['avg_subtitle_quality_score']}"
    if summary.get("review_required"):
        yield f"[warning] review_required_outputs={summary.get('review_required')}"
    if summary.get("subtitle_missing_outputs"):
        yield f"[warning] subtitle_missing_outputs={summary.get('subtitle_missing_outputs')}"
    if summary.get("fallback_reframe_outputs"):
        yield f"[warning] fallback_reframe_outputs={summary.get('fallback_reframe_outputs')}"
    if summary.get("weak_cold_open_outputs"):
        yield f"[warning] weak_cold_open_outputs={summary.get('weak_cold_open_outputs')}"
    if summary.get("weak_subject_outputs"):
        yield f"[warning] weak_subject_outputs={summary.get('weak_subject_outputs')}"
    if summary.get("weak_packaging_outputs"):
        yield f"[warning] weak_packaging_outputs={summary.get('weak_packaging_outputs')}"
    if summary.get("ranking_timeouts"):
        yield f"[warning] ranking_timeouts={summary['ranking_timeouts']}"
    if summary.get("ranking_fallback_used"):
        yield f"[done] ranking_fallback_used={summary['ranking_fallback_used']}"
    if summary.get("ranking_fast_fallback_used"):
        yield f"[done] ranking_fast_fallback_used={summary['ranking_fast_fallback_used']}"
    if summary.get("semantic_preview_timeouts"):
        yield f"[warning] semantic_preview_timeouts={summary['semantic_preview_timeouts']}"
    if summary.get("semantic_preview_fallback_used"):
        yield f"[done] semantic_preview_fallback_used={summary['semantic_preview_fallback_used']}"
    if summary.get("slow_stage_events"):
        yield f"[warning] slow_stage_events={summary['slow_stage_events']}"
    if summary.get("hard_timeouts"):
        yield f"[warning] hard_timeouts={summary['hard_timeouts']}"
    if summary.get("deferred_candidates"):
        yield f"[done] deferred_candidates={summary['deferred_candidates']}"
    if summary.get("skipped_due_to_timeout"):
        yield f"[warning] skipped_due_to_timeout={summary['skipped_due_to_timeout']}"
    if summary.get("watchdog_fallback_used"):
        yield f"[done] watchdog_fallback_used={summary['watchdog_fallback_used']}"
    if summary.get("final_visual_rejects"):
        yield f"[warning] final_visual_rejects={summary['final_visual_rejects']}"
    if summary.get("silent_parts_removed_total"):
        yield f"[done] silent_parts_removed_total={summary['silent_parts_removed_total']}"
    if summary.get("subtitle_quality_retry_outputs"):
        yield f"[done] subtitle_quality_retry_outputs={summary['subtitle_quality_retry_outputs']}"
    if summary.get("pause_policy_failed_outputs"):
        yield f"[warning] pause_policy_failed_outputs={summary['pause_policy_failed_outputs']}"
    if summary.get("square_reframe_mode_outputs"):
        yield f"[done] square_reframe_mode_outputs={summary['square_reframe_mode_outputs']}"
    if summary.get("end_boundary_completion_ok_outputs") is not None:
        yield f"[done] end_boundary_completion_ok_outputs={summary.get('end_boundary_completion_ok_outputs')}"
    if summary.get("incomplete_phrase_end_outputs"):
        yield f"[warning] incomplete_phrase_end_outputs={summary.get('incomplete_phrase_end_outputs')}"
    for warning in summary.get("warnings", []):
        yield f"[warning] {warning}"


def run_batch_via_gui_contract(input_folder: str, config_path: str, progress_callback=print):
    cfg, pipeline = create_pipeline_from_config(config_path)
    if pipeline is None:
        progress_callback("[failed] Pipeline unavailable")
        return 1
    files = sorted(
        os.path.join(input_folder, name)
        for name in os.listdir(input_folder)
        if name.lower().endswith((".mp4", ".mkv", ".mov", ".avi", ".webm"))
    )
    if not files:
        progress_callback("[warning] No supported video files found.")
        return 1
    total = len(files)
    for index, file_path in enumerate(files, start=1):
        progress_callback(f"[queued] ({index}/{total}) {file_path}")
        report = pipeline.process_episode(file_path, progress_callback=progress_callback)
        for line in iter_report_log_lines(report):
            progress_callback(line)
    progress_callback("[done] Batch done")
    return 0


class FileItem(QtWidgets.QListWidgetItem):
    def __init__(self, path):
        super().__init__(os.path.basename(path))
        self.path = path
        self.status = "queued"
        self.last_msg = ""
        self.report_summary = {}
        self.report_data = {}
        self.generated_outputs = []
        self.refresh()

    def refresh(self):
        suffix = f" - [{self.status}]"
        if self.last_msg:
            suffix += f" {self.last_msg}"
        self.setText(f"{os.path.basename(self.path)}{suffix}")
        if self.report_summary:
            tooltip = [
                f"status={self.report_summary.get('status')}",
                f"outputs={self.report_summary.get('outputs')}/{self.report_summary.get('requested_max')}",
                f"story_candidates={self.report_summary.get('story_candidates')}",
                f"publishable={self.report_summary.get('publishable_candidates')}",
                f"main_rejection_reason={self.report_summary.get('main_rejection_reason')}",
            ]
            self.setToolTip("\n".join(tooltip))

    def set_state(self, status, message=""):
        self.status = status
        self.last_msg = message
        self.refresh()

    def set_report_summary(self, summary: dict, report: dict | None = None, generated_outputs: list | None = None):
        self.report_summary = dict(summary or {})
        self.report_data = dict(report or {})
        self.generated_outputs = list(generated_outputs or [])
        self.refresh()


class MainWindow(QtWidgets.QWidget):
    def __init__(self, config_path="settings.yaml"):
        super().__init__()
        self.config_path = config_path
        self.cfg, self.pipeline = create_pipeline_from_config(config_path)
        self.stop_requested = False
        self.processing_start = None

        self.setWindowTitle("ShortsFactory")
        self.resize(1280, 860)
        root = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs)

        self.queue_tab = QtWidgets.QWidget()
        self.diagnostics_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.queue_tab, "Queue")
        self.tabs.addTab(self.diagnostics_tab, "Diagnostics")

        self._build_queue_tab()
        self._build_diagnostics_tab()
        self._load_config_into_widgets()

        self.elapsed_timer = QtCore.QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._update_elapsed)

        if self.pipeline is None:
            self.generate_btn.setEnabled(False)
            self.append_log("[warning] Pipeline unavailable. Check diagnostics.")

    def _build_queue_tab(self):
        layout = QtWidgets.QVBoxLayout(self.queue_tab)
        top = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add Episode(s)")
        self.add_btn.clicked.connect(self.add_files)
        top.addWidget(self.add_btn)
        self.clear_btn = QtWidgets.QPushButton("Clear Queue")
        self.clear_btn.clicked.connect(self.clear_queue)
        top.addWidget(self.clear_btn)
        self.ui_language_combo = QtWidgets.QComboBox()
        self.ui_language_combo.addItems(["ru", "en"])
        self.ui_language_combo.currentTextChanged.connect(self._on_ui_language_changed)
        top.addWidget(self.ui_language_combo)
        top.addWidget(QtWidgets.QLabel("Quality Mode"))
        self.quality_mode_combo = QtWidgets.QComboBox()
        self.quality_mode_combo.addItems(["auto", "balanced", "max_quality"])
        top.addWidget(self.quality_mode_combo)
        self.test_mode_check = QtWidgets.QCheckBox("Test mode")
        top.addWidget(self.test_mode_check)
        top.addWidget(QtWidgets.QLabel("Candidate rank"))
        self.test_candidate_spin = QtWidgets.QSpinBox()
        self.test_candidate_spin.setRange(1, 99)
        top.addWidget(self.test_candidate_spin)
        self.save_btn = QtWidgets.QPushButton("Save Settings")
        self.save_btn.clicked.connect(self.save_settings)
        top.addWidget(self.save_btn)
        self.generate_btn = QtWidgets.QPushButton("Generate Shorts")
        self.generate_btn.clicked.connect(self.start_generation)
        top.addWidget(self.generate_btn)
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.request_stop)
        top.addWidget(self.stop_btn)
        top.addStretch(1)
        layout.addLayout(top)

        settings_box = QtWidgets.QGroupBox("Project Settings")
        form = QtWidgets.QGridLayout(settings_box)
        self.max_shorts_spin = QtWidgets.QSpinBox()
        self.max_shorts_spin.setRange(1, 50)
        self.max_duration_spin = QtWidgets.QSpinBox()
        self.max_duration_spin.setRange(20, 60)
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.addItems(["fast", "balanced", "quality"])
        self.subtitle_processing_combo = QtWidgets.QComboBox()
        self.subtitle_processing_combo.addItems(["balanced_local", "enhanced_local"])
        self.reframe_combo = QtWidgets.QComboBox()
        self.reframe_combo.addItems(["balanced", "speaker_focus", "center_safe"])
        self.reframe_priority_combo = QtWidgets.QComboBox()
        self.reframe_priority_combo.addItems(["stability_first", "balanced_reactive"])
        self.growth_profile_combo = QtWidgets.QComboBox()
        self.growth_profile_combo.addItems(["youtube_shorts_retention_first"])
        self.packaging_profile_combo = QtWidgets.QComboBox()
        self.packaging_profile_combo.addItems(["ru_serial_drama", "broad_entertainment", "clean_neutral"])
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.addItems(["auto", "ru", "en"])
        self.selection_policy_combo = QtWidgets.QComboBox()
        self.selection_policy_combo.addItems(["quality_first", "quantity_first"])
        self.story_mode_combo = QtWidgets.QComboBox()
        self.story_mode_combo.addItems(["auto", "standard", "tension"])
        self.story_target_spin = QtWidgets.QSpinBox()
        self.story_target_spin.setRange(20, 60)
        self.story_min_spin = QtWidgets.QSpinBox()
        self.story_min_spin.setRange(20, 60)
        self.interestingness_spin = QtWidgets.QDoubleSpinBox()
        self.interestingness_spin.setRange(0.1, 1.0)
        self.interestingness_spin.setDecimals(2)
        self.interestingness_spin.setSingleStep(0.02)
        self.subtitle_template_combo = QtWidgets.QComboBox()
        self.subtitle_template_combo.addItems(["classic_bold", "shorts_clean", "focus_word_highlight", "drama_focus"])
        self.subtitle_render_combo = QtWidgets.QComboBox()
        self.subtitle_render_combo.addItems(["ass_word_highlight", "srt_block"])
        self.subtitle_display_combo = QtWidgets.QComboBox()
        self.subtitle_display_combo.addItems(["sentence_highlight", "active_chunk", "full_context"])
        self.subtitle_compact_check = QtWidgets.QCheckBox("Compact subtitles")
        self.subtitle_fontsize_spin = QtWidgets.QSpinBox()
        self.subtitle_fontsize_spin.setRange(28, 56)
        self.subtitle_chars_spin = QtWidgets.QSpinBox()
        self.subtitle_chars_spin.setRange(18, 42)
        self.subtitle_words_spin = QtWidgets.QSpinBox()
        self.subtitle_words_spin.setRange(1, 4)
        self.subtitle_visible_words_spin = QtWidgets.QSpinBox()
        self.subtitle_visible_words_spin.setRange(1, 4)
        self.subtitle_sentence_words_spin = QtWidgets.QSpinBox()
        self.subtitle_sentence_words_spin.setRange(4, 16)
        self.subtitle_active_color_edit = QtWidgets.QLineEdit()
        self.reframe_transition_combo = QtWidgets.QComboBox()
        self.reframe_transition_combo.addItems(["smooth", "fast_smooth", "hold_frame"])
        self.reframe_anchor_combo = QtWidgets.QComboBox()
        self.reframe_anchor_combo.addItems(["stable_primary", "dialogue_center", "adaptive_center"])
        self.framing_mode_combo = QtWidgets.QComboBox()
        self.framing_mode_combo.addItems(["tight_crop", "context_padded", "wide_subject", "human_handoff", "shot_lock", "scene_lock", "face_locked", "dialogue_dual", "square_canvas"])
        self.reframe_track_limit_spin = QtWidgets.QSpinBox()
        self.reframe_track_limit_spin.setRange(1, 4)
        self.reframe_switch_confirm_spin = QtWidgets.QSpinBox()
        self.reframe_switch_confirm_spin.setRange(1, 6)
        self.story_stitching_check = QtWidgets.QCheckBox("Story stitching")
        self.static_subtitle_frame_check = QtWidgets.QCheckBox("Static subtitle frame")
        self.subtitle_correction_check = QtWidgets.QCheckBox("Subtitle correction pass")
        self.scene_interest_check = QtWidgets.QCheckBox("Use scene-interest fallback")
        self.listener_fallback_check = QtWidgets.QCheckBox("Keep alternate face fallback")
        self.remote_quality_combo = QtWidgets.QComboBox()
        self.remote_quality_combo.addItems(["off", "manual", "difficult_clips_only"])
        self.remote_quality_enabled_check = QtWidgets.QCheckBox("Enable remote quality fallback")
        self.remote_provider_edit = QtWidgets.QLineEdit()
        self.title_generation_check = QtWidgets.QCheckBox("Auto-title exported shorts")
        self.title_style_combo = QtWidgets.QComboBox()
        self.title_style_combo.addItems(["context_clean", "dramatic", "retention_soft"])
        self.title_max_length_spin = QtWidgets.QSpinBox()
        self.title_max_length_spin.setRange(32, 120)
        self.title_hashtags_check = QtWidgets.QCheckBox("Add hashtags")
        self.title_emoji_check = QtWidgets.QCheckBox("Add emojis")
        self.keep_short_pauses_check = QtWidgets.QCheckBox("Keep pauses shorter than 1s")
        self.story_continue_check = QtWidgets.QCheckBox("Continue story after brief silence")
        self.drop_silent_check = QtWidgets.QCheckBox("Drop silent parts")
        self.remove_silent_check = QtWidgets.QCheckBox("Remove silent scenes")
        self.use_visual_asd_check = QtWidgets.QCheckBox("Use visual active-speaker")
        self.output_root_edit = QtWidgets.QLineEdit()
        self.output_root_btn = QtWidgets.QPushButton("Browse")
        self.output_root_btn.clicked.connect(self.pick_output_root)

        form.addWidget(QtWidgets.QLabel("Max shorts per episode"), 0, 0)
        form.addWidget(self.max_shorts_spin, 0, 1)
        form.addWidget(QtWidgets.QLabel("Max short duration (sec)"), 0, 2)
        form.addWidget(self.max_duration_spin, 0, 3)
        form.addWidget(QtWidgets.QLabel("Transcription profile"), 1, 0)
        form.addWidget(self.profile_combo, 1, 1)
        form.addWidget(QtWidgets.QLabel("ASR quality mode"), 1, 2)
        form.addWidget(self.subtitle_processing_combo, 1, 3)
        form.addWidget(QtWidgets.QLabel("Reframe mode"), 1, 4)
        form.addWidget(self.reframe_combo, 1, 5)
        form.addWidget(QtWidgets.QLabel("Subtitle language"), 2, 0)
        form.addWidget(self.language_combo, 2, 1)
        form.addWidget(self.drop_silent_check, 2, 2)
        form.addWidget(self.remove_silent_check, 2, 3)
        form.addWidget(QtWidgets.QLabel("Reframe priority"), 2, 4)
        form.addWidget(self.reframe_priority_combo, 2, 5)
        form.addWidget(QtWidgets.QLabel("Growth profile"), 2, 6)
        form.addWidget(self.growth_profile_combo, 2, 7)
        form.addWidget(QtWidgets.QLabel("Selection policy"), 3, 0)
        form.addWidget(self.selection_policy_combo, 3, 1)
        form.addWidget(QtWidgets.QLabel("Story mode"), 3, 2)
        form.addWidget(self.story_mode_combo, 3, 3)
        form.addWidget(QtWidgets.QLabel("Packaging profile"), 3, 4)
        form.addWidget(self.packaging_profile_combo, 3, 5)
        form.addWidget(QtWidgets.QLabel("Min story sec"), 4, 0)
        form.addWidget(self.story_min_spin, 4, 1)
        form.addWidget(QtWidgets.QLabel("Target story sec"), 4, 2)
        form.addWidget(self.story_target_spin, 4, 3)
        form.addWidget(QtWidgets.QLabel("Interestingness"), 4, 4)
        form.addWidget(self.interestingness_spin, 4, 5)
        form.addWidget(QtWidgets.QLabel("Subtitle template"), 5, 0)
        form.addWidget(self.subtitle_template_combo, 5, 1)
        form.addWidget(QtWidgets.QLabel("Subtitle render"), 5, 2)
        form.addWidget(self.subtitle_render_combo, 5, 3)
        form.addWidget(QtWidgets.QLabel("Subtitle display"), 5, 4)
        form.addWidget(self.subtitle_display_combo, 5, 5)
        form.addWidget(QtWidgets.QLabel("Reframe transition"), 6, 0)
        form.addWidget(self.reframe_transition_combo, 6, 1)
        form.addWidget(QtWidgets.QLabel("Reframe anchor"), 6, 2)
        form.addWidget(self.reframe_anchor_combo, 6, 3)
        form.addWidget(QtWidgets.QLabel("Framing mode"), 6, 4)
        form.addWidget(self.framing_mode_combo, 6, 5)
        form.addWidget(QtWidgets.QLabel("Track limit"), 6, 6)
        form.addWidget(self.reframe_track_limit_spin, 6, 7)
        form.addWidget(self.subtitle_compact_check, 7, 0)
        form.addWidget(QtWidgets.QLabel("Subtitle font size"), 7, 1)
        form.addWidget(self.subtitle_fontsize_spin, 7, 2)
        form.addWidget(QtWidgets.QLabel("Chars per subtitle block"), 7, 3)
        form.addWidget(self.subtitle_chars_spin, 7, 4)
        form.addWidget(QtWidgets.QLabel("Highlight color"), 7, 5)
        form.addWidget(self.subtitle_active_color_edit, 7, 6)
        form.addWidget(QtWidgets.QLabel("Words per highlight batch"), 8, 0)
        form.addWidget(self.subtitle_words_spin, 8, 1)
        form.addWidget(QtWidgets.QLabel("Visible words"), 8, 2)
        form.addWidget(self.subtitle_visible_words_spin, 8, 3)
        form.addWidget(QtWidgets.QLabel("Sentence max words"), 8, 4)
        form.addWidget(self.subtitle_sentence_words_spin, 8, 5)
        form.addWidget(self.title_generation_check, 9, 0)
        form.addWidget(QtWidgets.QLabel("Title style"), 9, 1)
        form.addWidget(self.title_style_combo, 9, 2)
        form.addWidget(QtWidgets.QLabel("Title max length"), 9, 3)
        form.addWidget(self.title_max_length_spin, 9, 4)
        form.addWidget(self.title_hashtags_check, 10, 0)
        form.addWidget(self.title_emoji_check, 10, 1)
        form.addWidget(self.keep_short_pauses_check, 10, 2)
        form.addWidget(self.story_continue_check, 10, 3)
        form.addWidget(self.use_visual_asd_check, 10, 4)
        form.addWidget(QtWidgets.QLabel("Switch confirm"), 10, 5)
        form.addWidget(self.reframe_switch_confirm_spin, 10, 6)
        form.addWidget(self.story_stitching_check, 11, 0)
        form.addWidget(self.static_subtitle_frame_check, 11, 1)
        form.addWidget(self.subtitle_correction_check, 11, 2)
        form.addWidget(self.scene_interest_check, 11, 3)
        form.addWidget(self.listener_fallback_check, 11, 4)
        form.addWidget(self.remote_quality_enabled_check, 12, 0)
        form.addWidget(QtWidgets.QLabel("Remote quality fallback"), 12, 1)
        form.addWidget(self.remote_quality_combo, 12, 2)
        form.addWidget(QtWidgets.QLabel("Remote provider"), 12, 3)
        form.addWidget(self.remote_provider_edit, 12, 4)
        form.addWidget(QtWidgets.QLabel("Output root"), 13, 2)
        form.addWidget(self.output_root_edit, 13, 3)
        form.addWidget(self.output_root_btn, 13, 4)
        layout.addWidget(settings_box)

        splitter = QtWidgets.QSplitter()
        splitter.setOrientation(QtCore.Qt.Horizontal)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(QtWidgets.QLabel("Episode Queue"))
        self.file_list = QtWidgets.QListWidget()
        left_layout.addWidget(self.file_list)
        left_buttons = QtWidgets.QHBoxLayout()
        self.open_output_btn = QtWidgets.QPushButton("Open Shorts Folder")
        self.open_output_btn.clicked.connect(self.preview_selected)
        left_buttons.addWidget(self.open_output_btn)
        self.open_short_btn = QtWidgets.QPushButton("Open Selected Short")
        self.open_short_btn.clicked.connect(self.open_selected_short)
        left_buttons.addWidget(self.open_short_btn)
        left_layout.addLayout(left_buttons)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.addWidget(QtWidgets.QLabel("Generated Shorts"))
        self.output_list = QtWidgets.QListWidget()
        right_layout.addWidget(self.output_list)
        self.assisted_ranking_group = QtWidgets.QGroupBox("Assisted Ranking Mode")
        assisted_layout = QtWidgets.QVBoxLayout(self.assisted_ranking_group)
        self.assisted_ranking_enabled_check = QtWidgets.QCheckBox("Enable rating feedback")
        self.assisted_ranking_enabled_check.setChecked(True)
        assisted_layout.addWidget(self.assisted_ranking_enabled_check)
        self.assisted_ranking_hint = QtWidgets.QLabel("Top 5 candidate shorts are ranked from the current episode output.")
        assisted_layout.addWidget(self.assisted_ranking_hint)
        self.assisted_ranking_list = QtWidgets.QListWidget()
        self.assisted_ranking_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        assisted_layout.addWidget(self.assisted_ranking_list)
        assisted_buttons = QtWidgets.QHBoxLayout()
        self.assisted_excellent_btn = QtWidgets.QPushButton("excellent")
        self.assisted_good_btn = QtWidgets.QPushButton("good")
        self.assisted_bad_btn = QtWidgets.QPushButton("bad")
        self.assisted_boring_btn = QtWidgets.QPushButton("boring")
        self.assisted_confusing_btn = QtWidgets.QPushButton("confusing")
        self.assisted_excellent_btn.clicked.connect(lambda: self.record_assisted_rating("excellent"))
        self.assisted_good_btn.clicked.connect(lambda: self.record_assisted_rating("good"))
        self.assisted_bad_btn.clicked.connect(lambda: self.record_assisted_rating("bad"))
        self.assisted_boring_btn.clicked.connect(lambda: self.record_assisted_rating("boring"))
        self.assisted_confusing_btn.clicked.connect(lambda: self.record_assisted_rating("confusing"))
        for button in (
            self.assisted_excellent_btn,
            self.assisted_good_btn,
            self.assisted_bad_btn,
            self.assisted_boring_btn,
            self.assisted_confusing_btn,
        ):
            assisted_buttons.addWidget(button)
        assisted_layout.addLayout(assisted_buttons)
        right_layout.addWidget(self.assisted_ranking_group)
        right_layout.addWidget(QtWidgets.QLabel("Selected File Summary"))
        self.report_summary = QtWidgets.QPlainTextEdit()
        self.report_summary.setReadOnly(True)
        right_layout.addWidget(self.report_summary)
        right_layout.addWidget(QtWidgets.QLabel("Log"))
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        right_layout.addWidget(self.log)
        self.file_list.currentItemChanged.connect(self._render_selected_item_summary)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 820])
        layout.addWidget(splitter, 1)

        footer = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("Ready")
        footer.addWidget(self.status_label)
        footer.addStretch(1)
        self.elapsed_label = QtWidgets.QLabel("")
        footer.addWidget(self.elapsed_label)
        layout.addLayout(footer)

    def _build_diagnostics_tab(self):
        layout = QtWidgets.QVBoxLayout(self.diagnostics_tab)
        row = QtWidgets.QHBoxLayout()
        self.run_diag_btn = QtWidgets.QPushButton("Run Diagnostics")
        self.run_diag_btn.clicked.connect(self.run_diagnostics)
        row.addWidget(self.run_diag_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.diagnostics_output = QtWidgets.QPlainTextEdit()
        self.diagnostics_output.setReadOnly(True)
        layout.addWidget(self.diagnostics_output)

    def _load_config_into_widgets(self):
        for widget_name, key, _, default in CONFIG_BINDINGS:
            _widget_set_value(getattr(self, widget_name), self.cfg.get(key, default))
        self.keep_short_pauses_check.setChecked(float(self.cfg.get("keep_dialogue_gap_seconds", 1.0)) >= 0.95)
        self.story_continue_check.setChecked(bool(self.cfg.get("story_continue_after_silence", True)))
        self.static_subtitle_frame_check.setChecked(str(self.cfg.get("subtitle_vertical_anchor_mode", "fixed_mid_lower")) == "fixed_mid_lower")
        self._apply_ui_language()

    def _clear_assisted_ranking_panel(self):
        if hasattr(self, "assisted_ranking_list"):
            self.assisted_ranking_list.clear()

    def _assisted_ranking_candidates(self, current):
        if not isinstance(current, FileItem):
            return []
        outputs = list(current.generated_outputs or [])
        if not outputs:
            return []
        return rank_assisted_candidates(outputs)[:5]

    def _refresh_assisted_ranking_panel(self, current):
        if not hasattr(self, "assisted_ranking_list"):
            return
        self.assisted_ranking_list.clear()
        if not self.assisted_ranking_enabled_check.isChecked():
            self.assisted_ranking_list.addItem("Assisted ranking is disabled.")
            return
        candidates = self._assisted_ranking_candidates(current)
        if not candidates:
            self.assisted_ranking_list.addItem("No ranked candidates for this episode yet.")
            return
        for candidate in candidates:
            title = candidate.get("generated_title") or Path(str(candidate.get("video") or "")).name
            label = (
                f"#{candidate.get('rank')} {title} | "
                f"score={candidate.get('score', 0.0):.3f} | "
                f"hook={candidate.get('first_second_hook_score', 0.0):.3f} | "
                f"story={candidate.get('story_interest_score', 0.0):.3f} | "
                f"packaging={candidate.get('packaging_quality_score', 0.0):.3f}"
            )
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, candidate)
            self.assisted_ranking_list.addItem(item)
        if self.assisted_ranking_list.count():
            self.assisted_ranking_list.setCurrentRow(0)

    def _selected_assisted_candidate(self):
        item = self.assisted_ranking_list.currentItem()
        if item is None:
            item = self.assisted_ranking_list.item(0)
        if item is None:
            return None
        payload = item.data(QtCore.Qt.UserRole)
        return payload if isinstance(payload, dict) else None

    def record_assisted_rating(self, rating: str):
        if not self.assisted_ranking_enabled_check.isChecked():
            self.append_log("[warning] Assisted ranking mode is disabled")
            return
        current = self.file_list.currentItem()
        if not isinstance(current, FileItem):
            QMessageBox.warning(self, self._tr("No selection"), self._tr("Select a source file first."))
            return
        candidate = self._selected_assisted_candidate()
        if not candidate:
            QMessageBox.information(self, self._tr("No ranked candidate"), "No candidate is available to rate yet.")
            return
        output_path = candidate.get("video")
        metadata_path = candidate.get("metadata")
        event = {
            "mode": "assisted_ranking",
            "rating": rating,
            "source_file": current.path,
            "output_video": output_path,
            "metadata_path": metadata_path,
            "rank": candidate.get("rank"),
            "generated_title": candidate.get("generated_title"),
            "story_unit_type": candidate.get("story_unit_type"),
            "score": candidate.get("score"),
            "recommendation_readiness_score": candidate.get("recommendation_readiness_score"),
            "watchability_score": candidate.get("watchability_score"),
            "packaging_quality_score": candidate.get("packaging_quality_score"),
            "first_second_hook_score": candidate.get("first_second_hook_score"),
            "story_interest_score": candidate.get("story_interest_score"),
            "visible_stakes_score": candidate.get("visible_stakes_score"),
            "first_frame_clarity_score": candidate.get("first_frame_clarity_score"),
            "cold_open_dead_time_penalty": candidate.get("cold_open_dead_time_penalty"),
            "ui_language": self.cfg.get("ui_language", "ru"),
        }
        log_path = append_feedback_event(event, base_dir=Path(self.config_path).resolve().parent)
        self.append_log(f"[feedback] rating={rating} rank={candidate.get('rank')} logged_to={log_path}")

    def _widgets_to_config(self):
        for widget_name, key, cast, _ in CONFIG_BINDINGS:
            self.cfg[key] = cast(_widget_get_value(getattr(self, widget_name)))
        quality_mode = str(self.quality_mode_combo.currentText())
        self.cfg["quality_governor_mode"] = quality_mode
        self.cfg["quality_profile"] = "quality_first"
        self.cfg["growth_profile"] = str(self.growth_profile_combo.currentText() or "youtube_shorts_retention_first")
        self.cfg["packaging_profile"] = str(self.packaging_profile_combo.currentText() or "ru_serial_drama")
        self.cfg["recommendation_readiness_enabled"] = True
        self.cfg["reframe_subject_mode"] = "subject_first"
        self.cfg["scene_interest_fallback_mode"] = "emergency_only"
        self.cfg["dialogue_two_shot_preferred"] = False
        self.cfg["subject_visibility_threshold"] = float(self.cfg.get("subject_visibility_threshold", 0.62) or 0.62)
        if quality_mode == "auto":
            self.cfg["subtitle_processing_mode"] = "balanced_local"
            self.cfg["local_quality_escalation"] = True
            self.cfg["reframe_priority"] = "stability_first"
        elif quality_mode == "balanced":
            self.cfg["subtitle_processing_mode"] = "balanced_local"
            self.cfg["local_quality_escalation"] = False
            self.cfg["reframe_priority"] = "stability_first"
        else:
            self.cfg["subtitle_processing_mode"] = "enhanced_local"
            self.cfg["local_quality_escalation"] = True
            self.cfg["reframe_priority"] = "stability_first"
            self.cfg["reframe_anchor_mode"] = "dialogue_center"
        self.cfg["subtitle_renderer_mode"] = "persistent_sentence_layer"
        self.cfg["speaker_lock_mode"] = "state_machine"
        self.cfg["empty_frame_guard_enabled"] = True
        self.cfg["framing_mode"] = str(self.framing_mode_combo.currentText())
        self.cfg["story_soft_max_seconds"] = max(int(self.story_target_spin.value()), 20)
        self.cfg["story_hard_max_seconds"] = int(self.max_duration_spin.value())
        self.cfg["subtitle_max_visible_lines"] = 2
        self.cfg["subtitle_word_batch_size"] = int(self.subtitle_words_spin.value())
        self.cfg["subtitle_chunk_mode"] = str(self.subtitle_display_combo.currentText())
        keep_gap_seconds = 1.0
        self.cfg["keep_dialogue_gap_seconds"] = keep_gap_seconds
        self.cfg["story_pause_cut_threshold_seconds"] = keep_gap_seconds
        self.cfg["story_pause_keep_max_seconds"] = 1.15
        self.cfg["story_extension_max_pause_seconds"] = 1.15
        self.cfg["story_merge_gap_seconds"] = keep_gap_seconds
        self.cfg["tension_pause_cut_threshold_seconds"] = keep_gap_seconds
        self.cfg["tension_pause_keep_max_seconds"] = 1.15
        self.cfg["speaker_switch_hold_windows"] = int(self.reframe_switch_confirm_spin.value())
        self.cfg["max_short_seconds"] = max(int(self.max_duration_spin.value()), int(self.story_target_spin.value()))
        if str(self.framing_mode_combo.currentText()) == "square_canvas":
            self.cfg["subtitle_vertical_anchor_mode"] = "square_bottom"
        else:
            self.cfg["subtitle_vertical_anchor_mode"] = "fixed_mid_lower" if self.static_subtitle_frame_check.isChecked() else "dynamic"

    def _tr(self, text, **kwargs):
        lang = str(self.cfg.get("ui_language", "ru"))
        translated = UI_TRANSLATIONS.get(lang, {}).get(text, text)
        if kwargs:
            try:
                return translated.format(**kwargs)
            except Exception:
                return translated
        return translated

    def _apply_ui_language(self):
        self.setWindowTitle(self._tr("ShortsFactory"))
        self.tabs.setTabText(0, self._tr("Queue"))
        self.tabs.setTabText(1, self._tr("Diagnostics"))
        for widget_type in (QtWidgets.QPushButton, QtWidgets.QCheckBox, QtWidgets.QGroupBox, QtWidgets.QLabel):
            for widget in self.findChildren(widget_type):
                text = widget.text() if hasattr(widget, "text") else ""
                if not text:
                    continue
                if widget.property("_base_text") is None:
                    widget.setProperty("_base_text", text)
                widget.setText(self._tr(widget.property("_base_text")))
        if self.status_label.property("_base_text") is None:
            self.status_label.setProperty("_base_text", "Ready")
        self.status_label.setText(self._tr(self.status_label.property("_base_text")))

    def _on_ui_language_changed(self, value):
        self.cfg["ui_language"] = str(value)
        self._apply_ui_language()

    def append_log(self, message):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        QtCore.QTimer.singleShot(0, lambda: self.log.appendPlainText(line))

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select episodes", os.getcwd(), "Video Files (*.mp4 *.mkv *.mov *.avi *.webm)")
        for path in files:
            self.file_list.addItem(FileItem(path))

    def clear_queue(self):
        self.file_list.clear()
        self.output_list.clear()
        self.report_summary.clear()
        self._clear_assisted_ranking_panel()

    def pick_output_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output root", self.output_root_edit.text() or os.getcwd())
        if folder:
            self.output_root_edit.setText(folder)

    def save_settings(self):
        self._widgets_to_config()
        save_config(self.config_path, self.cfg)
        self.cfg, self.pipeline = create_pipeline_from_config(self.config_path)
        self._apply_ui_language()
        self.append_log(f"[config] Saved settings to {self.config_path}")

    def request_stop(self):
        self.stop_requested = True
        self.stop_btn.setEnabled(False)
        self.append_log("[warning] Stop requested")

    def _update_elapsed(self):
        if self.processing_start is None:
            self.elapsed_label.setText("")
            return
        elapsed = int(time.time() - self.processing_start)
        prefix = "Прошло" if str(self.cfg.get("ui_language", "ru")) == "ru" else "Elapsed"
        self.elapsed_label.setText(f"{prefix}: {elapsed // 60}m{elapsed % 60:02d}s")

    def start_generation(self):
        if self.pipeline is None:
            QMessageBox.warning(self, self._tr("Pipeline missing"), self._tr("Pipeline is unavailable. Check diagnostics."))
            return
        if self.file_list.count() == 0:
            QMessageBox.warning(self, self._tr("No files"), self._tr("Please add at least one source video."))
            return
        self.save_settings()
        self.output_list.clear()
        self._clear_assisted_ranking_panel()
        self.stop_requested = False
        self.generate_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.processing_start = time.time()
        self.elapsed_timer.start()
        self.status_label.setText(self._tr("Processing"))
        threading.Thread(target=self._run_queue, daemon=True).start()

    def _run_queue(self):
        total = self.file_list.count()
        for index in range(total):
            if self.stop_requested:
                break
            item = self.file_list.item(index)
            if not isinstance(item, FileItem):
                continue
            item.set_state("analyzing")
            path = item.path
            self.append_log(f"[queued] ({index + 1}/{total}) {path}")

            def callback(message, row=index):
                self.append_log(message)
                QtCore.QTimer.singleShot(0, lambda: self._update_item_from_message(row, str(message)))

            report = self.pipeline.process_episode(path, progress_callback=callback, stop_check=lambda: self.stop_requested)
            generated = report.get("generated_outputs", [])
            summary = summarize_report_for_gui(report)
            suffix = f"{summary['outputs']} short(s)"
            if summary["main_rejection_reason"]:
                suffix += f", reason: {summary['main_rejection_reason']}"
            elif report.get("warnings"):
                suffix += f", warnings: {len(report['warnings'])}"
            item.set_state(report.get("status", "failed"), suffix)
            item.set_report_summary(summary, report=report, generated_outputs=generated)
            for line in iter_report_log_lines(report):
                self.append_log(line)
            for output in generated:
                meta = load_report_metadata(output.get("metadata", ""))
                label = Path(output["video"]).name
                if meta.get("generated_title"):
                    label = f"{label} | {meta.get('generated_title')}"
                QtCore.QTimer.singleShot(0, lambda value=output["video"], text=label: self._append_output_item(value, text))
            QtCore.QTimer.singleShot(0, lambda current_item=item: self._refresh_assisted_ranking_panel(current_item))
        QtCore.QTimer.singleShot(0, self._finish_processing)

    def _update_item_from_message(self, index, message):
        item = self.file_list.item(index)
        if not isinstance(item, FileItem):
            return
        stage = "processing"
        if message.startswith("[") and "]" in message:
            stage = message[1 : message.index("]")]
        item.set_state(stage, message.split("] ", 1)[-1][:96])

    def _finish_processing(self):
        self.generate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.processing_start = None
        self.elapsed_timer.stop()
        self.elapsed_label.setText("")
        self.status_label.setText(self._tr("Ready"))
        self.append_log("[done] Queue processing finished")

    def _append_output_item(self, path, text):
        item = QtWidgets.QListWidgetItem(text)
        item.setData(QtCore.Qt.UserRole, path)
        item.setToolTip(path)
        self.output_list.addItem(item)

    def _render_selected_item_summary(self, current, previous=None):
        if not isinstance(current, FileItem):
            self.report_summary.clear()
            self._clear_assisted_ranking_panel()
            return
        summary = dict(current.report_summary or {})
        if not summary:
            self.report_summary.setPlainText(self._tr("No run summary yet for this file."))
            return
        lines = [
            f"{self._tr('Status')}: {summary.get('status')}",
            f"{self._tr('Outputs')}: {summary.get('outputs')}/{summary.get('requested_max')}",
            f"Test mode: {summary.get('test_mode_enabled')}",
            f"Test candidate rank: {summary.get('test_candidate_rank') or '-'}",
            f"{self._tr('Windows')}: {summary.get('windows')}",
            f"{self._tr('Story candidates')}: {summary.get('story_candidates')}",
            f"{self._tr('Publishable candidates')}: {summary.get('publishable_candidates')}",
            f"Publishable pool before final visual gate: {summary.get('publishable_pool_before_final_visual_gate')}",
            f"Story override candidates: {summary.get('story_override_candidates')}",
            f"{self._tr('Main rejection reason')}: {summary.get('main_rejection_reason') or '-'}",
            f"Main rejection bucket: {summary.get('main_rejection_bucket') or '-'}",
            f"Review pass considered: {summary.get('review_pass_considered')}",
            f"{self._tr('Interestingness avg')}: {summary.get('interestingness_avg')}",
            f"Avg hook strength: {summary.get('avg_hook_strength')}",
            f"Avg visual premise strength: {summary.get('avg_visual_premise_strength')}",
            f"Avg visible stakes: {summary.get('avg_visible_stakes_score')}",
            f"Avg first-frame clarity: {summary.get('avg_first_frame_clarity_score')}",
            f"Avg sound-off hook: {summary.get('avg_sound_off_hook_score')}",
            f"Avg sound-off premise: {summary.get('avg_sound_off_premise_score')}",
            f"Avg first-second hook: {summary.get('avg_first_second_hook_score')}",
            f"Avg premise signal: {summary.get('avg_premise_signal_score')}",
            f"Avg dialogue dependency penalty: {summary.get('avg_dialogue_dependency_penalty')}",
            f"Avg watchability: {summary.get('avg_watchability_score')}",
            f"Avg recommendation readiness: {summary.get('avg_recommendation_readiness')}",
            f"Avg packaging quality: {summary.get('avg_packaging_quality')}",
            f"Avg subtitle confidence: {summary.get('avg_subtitle_confidence')}",
            f"Avg subtitle text sanity: {summary.get('avg_subtitle_text_sanity')}",
            f"Avg subtitle language consistency: {summary.get('avg_subtitle_language_consistency')}",
            f"Avg subtitle quality score: {summary.get('avg_subtitle_quality_score')}",
            f"Avg final duration: {summary.get('avg_final_duration')}s",
            f"Duration policy bands: {summary.get('duration_policy_bands')}",
            f"Speaker direct transitions: {summary.get('speaker_transition_direct_total')}",
            f"Speaker switch latency: {summary.get('speaker_switch_latency_total')}",
            f"Handoff glide total: {summary.get('handoff_glide_total')}",
            f"Accent-frame hold total: {summary.get('accent_frame_hold_total')}",
            f"Avg subtitle blackout: {summary.get('avg_subtitle_blackout')}",
            f"Weak premise outputs: {summary.get('weak_premise_outputs')}",
            f"{self._tr('Review required outputs')}: {summary.get('review_required')}",
            f"{self._tr('Titled outputs')}: {summary.get('titled_outputs')}",
            f"Title mojibake outputs: {summary.get('title_mojibake_outputs')}",
            f"{self._tr('Subtitle missing outputs')}: {summary.get('subtitle_missing_outputs')}",
            f"{self._tr('Fallback reframe outputs')}: {summary.get('fallback_reframe_outputs')}",
            f"{self._tr('Stitched outputs')}: {summary.get('stitched_outputs')}",
            f"{self._tr('Dialogue-center outputs')}: {summary.get('dialogue_center_outputs')}",
            f"Scene-interest outputs: {summary.get('scene_interest_outputs')}",
            f"Center-safe fallback outputs: {summary.get('center_safe_fallback_outputs')}",
            f"Face-preserving fallback outputs: {summary.get('face_preserving_fallback_outputs')}",
            f"Subject acquisition states: {summary.get('subject_acquisition_state_counts')}",
            f"Subject acquisition outcomes: {summary.get('subject_acquisition_outcome_counts')}",
            f"Subtitle jitter suspects: {summary.get('subtitle_jitter_suspects')}",
            f"Subtitle overlap outputs: {summary.get('subtitle_overlap_outputs')}",
            f"Subtitle blink outputs: {summary.get('subtitle_blink_outputs')}",
            f"Subtitle blackout outputs: {summary.get('subtitle_blackout_outputs')}",
            f"Subtitle persisted gaps: {summary.get('subtitle_persist_outputs')}",
            f"Listener fallback outputs: {summary.get('listener_fallback_outputs')}",
            f"Speaker->listener switch rate: {summary.get('speaker_to_listener_switch_rate')}",
            f"Speaker->listener switches: {summary.get('speaker_to_listener_switch_outputs')}",
            f"Subtitle remap usage rate: {summary.get('subtitle_remap_usage_rate')}",
            f"Subtitle remap outputs: {summary.get('subtitle_remap_outputs')}",
            f"Compaction integrity failed: {summary.get('compaction_integrity_failed_outputs')}",
            f"Subtitle correction outputs: {summary.get('subtitle_correction_outputs')}",
            f"Subtitle quality retry outputs: {summary.get('subtitle_quality_retry_outputs')}",
            f"Auto quality retries: {summary.get('auto_quality_retry_outputs')}",
            f"Auto reframe retries: {summary.get('auto_reframe_retry_outputs')}",
            f"Square reframe outputs: {summary.get('square_reframe_mode_outputs')}",
            f"End boundary complete outputs: {summary.get('end_boundary_completion_ok_outputs')}",
            f"Incomplete phrase end outputs: {summary.get('incomplete_phrase_end_outputs')}",
            f"Weak cold-open outputs: {summary.get('weak_cold_open_outputs')}",
            f"Weak subject outputs: {summary.get('weak_subject_outputs')}",
            f"Weak packaging outputs: {summary.get('weak_packaging_outputs')}",
            f"Remote retry candidates: {summary.get('remote_retry_candidates')}",
            f"Avg anchor switches: {summary.get('avg_anchor_switches')}",
            f"Avg speaker center offset: {summary.get('avg_speaker_center_offset')}",
            f"Avg speaker center p95: {summary.get('avg_speaker_center_offset_p95')}",
            f"Avg speaker-centered windows: {summary.get('avg_speaker_face_centered_windows')}",
            f"Avg dialogue-center windows: {summary.get('avg_dialogue_center_windows')}",
            f"Avg listener fallback windows: {summary.get('avg_listener_fallback_windows')}",
            f"Avg subject-person fallback windows: {summary.get('avg_subject_person_fallback_windows')}",
            f"Ranking timeouts: {summary.get('ranking_timeouts')}",
            f"Ranking fallback used: {summary.get('ranking_fallback_used')}",
            f"Fast visual ranking used: {summary.get('ranking_fast_fallback_used')}",
            f"Ranking failed: {summary.get('ranking_failed')}",
            f"Semantic preview timeouts: {summary.get('semantic_preview_timeouts')}",
            f"Semantic preview fallback used: {summary.get('semantic_preview_fallback_used')}",
            f"Slow stage events: {summary.get('slow_stage_events')}",
            f"Hard timeouts: {summary.get('hard_timeouts')}",
            f"Deferred candidates: {summary.get('deferred_candidates')}",
            f"Skipped due to timeout: {summary.get('skipped_due_to_timeout')}",
            f"Watchdog fallback used: {summary.get('watchdog_fallback_used')}",
            f"Final visual rejects: {summary.get('final_visual_rejects')}",
            f"Compaction integrity failed total: {summary.get('compaction_integrity_failed_total')}",
            f"Silent parts removed total: {summary.get('silent_parts_removed_total')}",
            f"Pause-policy failed outputs: {summary.get('pause_policy_failed_outputs')}",
            f"Selection starvation reasons: {summary.get('selection_starvation_reasons')}",
            f"Selection starvation visual: {summary.get('selection_starvation_visual')}",
            f"Selection starvation subtitle: {summary.get('selection_starvation_subtitle')}",
            f"Selection starvation boundary: {summary.get('selection_starvation_boundary')}",
            f"Selection starvation vad: {summary.get('selection_starvation_vad')}",
            f"{self._tr('Selection seconds')}: {summary.get('selection_seconds')}",
            f"{self._tr('Median stage seconds')}: {summary.get('median_stage_seconds')}",
        ]
        warnings_list = summary.get("warnings") or []
        if warnings_list:
            lines.append("")
            lines.append(self._tr("Warnings:"))
            lines.extend(f"- {item}" for item in warnings_list[:10])
        self.report_summary.setPlainText("\n".join(lines))
        self._refresh_assisted_ranking_panel(current)

    def _open_path(self, path):
        if not path or not os.path.exists(path):
            QMessageBox.information(self, self._tr("Not found"), self._tr("Path does not exist:\n{path}", path=path))
            return
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform.startswith("darwin"):
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def preview_selected(self):
        item = self.file_list.currentItem()
        if not isinstance(item, FileItem):
            QMessageBox.warning(self, self._tr("No selection"), self._tr("Select a source file first."))
            return
        output_root = self.output_root_edit.text().strip()
        if output_root:
            path = os.path.join(output_root, os.path.splitext(os.path.basename(item.path))[0] + "_shorts")
        else:
            path = os.path.splitext(item.path)[0] + "_shorts"
        self._open_path(path)

    def open_selected_short(self):
        current = self.output_list.currentItem()
        if current is None:
            QMessageBox.warning(self, self._tr("No short selected"), self._tr("Select a generated short first."))
            return
        self._open_path(current.data(QtCore.Qt.UserRole) if current.data(QtCore.Qt.UserRole) else current.text())

    def run_diagnostics(self):
        self.diagnostics_output.setPlainText("Running diagnostics...")

        def worker():
            text = run_diagnostics_text(os.getcwd())
            QtCore.QTimer.singleShot(0, lambda: self.diagnostics_output.setPlainText(text))

        threading.Thread(target=worker, daemon=True).start()


def launch_gui(config_path="settings.yaml"):
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(config_path=config_path)
    window.show()
    try:
        app.exec()
    except KeyboardInterrupt:
        window.close()


if __name__ == "__main__":
    launch_gui()

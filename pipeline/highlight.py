from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import json
import multiprocessing as mp
import os
import queue
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import warnings
import wave
from collections import Counter
from math import ceil
from pathlib import Path

import numpy as np
from moviepy import VideoFileClip

from .active_speaker import sample_face_focus_stats
from .audio_analysis import (
    compute_rms,
    detect_silence_ffmpeg,
    extract_audio_to_wav,
    speech_density,
)
from .config import normalize_config
from .face_crop import create_vertical_crop
from .montage.active_speaker_editor import summarize_reframe_debug
from .montage.dialogue_parser import extract_dialogue_turns
from .montage.silence_rewriter import (
    build_pause_timeline as _build_pause_timeline_module,
)
from .montage.silence_rewriter import (
    build_silence_rewrite_plan,
)
from .montage.silence_rewriter import (
    classify_silence_pause as _classify_silence_pause_module,
)
from .montage.silence_rewriter import (
    pacing_score_from_pause_timeline as _pacing_score_from_pause_timeline_module,
)
from .montage.story_builder import build_story_plan as _build_story_plan_montage
from .montage.story_chain_builder import (
    build_story_chain,
    build_story_summary_from_turns,
)
from .montage.story_fragments import build_story_fragments, fragments_to_dicts
from .montage.story_pipeline import (
    build_story_chains_for_episode,
    story_chain_to_candidate,
)
from .remote_enhancer import enhance_clip_metadata, should_use_remote_fallback
from .scene_detect import detect_scenes
from .subtitle import remap_subtitle_info_after_cuts, transcribe_segment
from .text_utils import _clean_text, _tokenize
from .titling import generate_context_title, maybe_rename_output
from .versioning import build_pipeline_identity

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
    module="webrtcvad",
)

try:
    import webrtcvad

    WEBRTC_AVAILABLE = True
except Exception:
    webrtcvad = None
    WEBRTC_AVAILABLE = False


TERMINAL_PUNCTUATION = (".", "!", "?")
INCOMPLETE_ENDINGS_RU = (
    "и",
    "а",
    "но",
    "или",
    "что",
    "чтобы",
    "потому",
    "если",
    "когда",
)
INCOMPLETE_ENDINGS_EN = ("and", "but", "or", "because", "if", "when", "that")


def run_ffmpeg(cmd, timeout=300):
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:
        return 1, "", str(exc)


def probe_video(path: str):
    ok, duration, _width, _height = probe_video_geometry(path)
    return ok, duration


def probe_video_geometry(path: str):
    rc, out, _ = run_ffmpeg(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            path,
        ],
        timeout=30,
    )
    if rc != 0:
        return False, 0.0, 0, 0
    try:
        data = json.loads(out or "{}")
        duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)
        streams = data.get("streams", [])
        stream = streams[0] if streams else {}
        width = int(stream.get("width", 0) or 0)
        height = int(stream.get("height", 0) or 0)
        has_video = bool(width > 0 and height > 0) or any(
            item.get("codec_type") == "video" for item in streams
        )
        return has_video, duration, width, height
    except Exception:
        return False, 0.0, 0, 0


def _story_debug_segments(
    subtitle_info: dict | None, story_window_plan: dict | None = None
) -> dict:
    subtitle_info = dict(subtitle_info or {})
    summary = dict(subtitle_info.get("summary") or {})
    segments = list(subtitle_info.get("segments") or [])
    story_window_plan = dict(story_window_plan or {})
    story_summary = dict(
        story_window_plan.get("story_summary")
        or subtitle_info.get("story_summary")
        or {}
    )
    texts = [
        _clean_text(segment.get("text", "") or segment.get("caption_text", ""))
        for segment in segments
        if _clean_text(segment.get("text", "") or segment.get("caption_text", ""))
    ]
    if not texts and summary.get("summary_text"):
        texts = [_clean_text(summary.get("summary_text", ""))]
    if not texts:
        texts = ["", "", "", ""]
    if len(texts) < 4:
        texts = texts + [""] * (4 - len(texts))
    hook = _clean_text(story_summary.get("hook") or texts[0])
    setup = _clean_text(
        story_summary.get("setup") or (texts[1] if len(texts) > 1 else "")
    )
    escalation = _clean_text(
        story_summary.get("escalation")
        or (
            " ".join(item for item in texts[2:-1] if item).strip()
            if len(texts) > 3
            else (texts[2] if len(texts) > 2 else "")
        )
    )
    payoff = _clean_text(
        story_summary.get("payoff") or (texts[-1] if len(texts) > 1 else "")
    )
    story_deficient = not all([hook, setup, escalation, payoff])
    return {
        "hook": hook,
        "setup": setup,
        "escalation": escalation,
        "payoff": payoff,
        "conversation_id": str(
            story_window_plan.get(
                "conversation_id", subtitle_info.get("conversation_id", "")
            )
            or ""
        ),
        "story_arc_shape": str(
            story_window_plan.get(
                "story_arc_shape", story_summary.get("story_arc_shape", "")
            )
            or ""
        ),
        "story_deficient": bool(story_deficient),
        "summary_text": summary.get("summary_text", ""),
        "keywords": list(summary.get("keywords") or []),
        "story_window_segments": list(
            story_window_plan.get(
                "story_window_segments", story_window_plan.get("segments", [])
            )
            or []
        ),
    }


def _build_story_assets(
    subtitle_info: dict | None,
    *,
    conversation_id: str = "",
    source_text: str = "",
    language: str = "auto",
) -> dict:
    subtitle_info = dict(subtitle_info or {})
    segments = list(subtitle_info.get("segments") or [])
    turns = extract_dialogue_turns(segments)
    fragments = build_story_fragments(turns)
    story_chain = build_story_chain(fragments, conversation_id=conversation_id)
    story_summary = build_story_summary_from_turns(
        turns,
        conversation_id=conversation_id,
        source_text=source_text
        or " ".join(
            _clean_text(item.get("text", "") or item.get("caption_text", ""))
            for item in segments
        ),
        language=language,
    )
    return {
        "story_fragments": fragments_to_dicts(fragments),
        "story_chain": story_chain.to_dict(),
        "story_summary": story_summary.to_dict(),
    }


def _summarize_reject_paths(report: dict) -> dict:
    reason_counts = Counter()
    impact_by_reason = {
        "trim_failed": "output_loss",
        "low speech density after trim": "subtitle_loss",
        "subtitle_timeout": "subtitle_loss",
        "no subtitles": "subtitle_loss",
        "low subtitle turns": "subtitle_loss",
        "insufficient_duration": "duration_loss",
        "insufficient_context": "story_loss",
        "low_story_quality": "story_loss",
        "low_story_interest": "story_loss",
        "low_story_completeness": "story_loss",
        "starts_mid_phrase": "story_loss",
        "dialogue_not_complete": "story_loss",
        "reframe_timeout": "framing_loss",
        "face_preserving_fallback": "framing_loss",
        "center_safe_fallback": "framing_loss",
    }
    samples = {}
    for item in report.get("rejected_candidates", []) or []:
        reason = str(item.get("reason", "unknown") or "unknown")
        reason_counts[reason] += 1
        samples.setdefault(
            reason,
            {
                "reason": reason,
                "count": 0,
                "impact": impact_by_reason.get(reason, "unknown"),
                "examples": [],
            },
        )
        samples[reason]["count"] += 1
        if len(samples[reason]["examples"]) < 3:
            candidate = item.get("candidate", {}) or {}
            samples[reason]["examples"].append(
                {
                    "start": round(float(candidate.get("start", 0.0) or 0.0), 3),
                    "end": round(float(candidate.get("end", 0.0) or 0.0), 3),
                    "source": str(candidate.get("source", "") or ""),
                }
            )
    warning_pattern = re.compile(r"Candidate\s+\d+\s+(?:rejected|downgraded):\s+(.+)$")
    seen_warning_keys = set()
    for warning in report.get("warnings", []) or []:
        match = warning_pattern.search(str(warning))
        if not match:
            continue
        reason = match.group(1).strip()
        key = reason.lower()
        if key in seen_warning_keys:
            continue
        seen_warning_keys.add(key)
        reason_counts[reason] += 1
        samples.setdefault(
            reason,
            {
                "reason": reason,
                "count": 0,
                "impact": impact_by_reason.get(reason, "unknown"),
                "examples": [],
            },
        )
        samples[reason]["count"] += 1
    return {
        "paths": sorted(
            samples.values(),
            key=lambda item: (
                -int(item.get("count", 0) or 0),
                str(item.get("reason", "")),
            ),
        ),
        "reason_counts": dict(reason_counts),
    }


def _emit(cb, stage, msg):
    if cb:
        cb(f"[{stage}] {msg}")


def _dump_json(path: str, payload: dict):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _now():
    return time.perf_counter()


def _looks_like_direct_candidate(video_path: str) -> bool:
    name = Path(video_path).stem.lower()
    return any(token in name for token in ("cand_", "short_", "trimmed", "candidate"))


def _subprocess_worker(queue, task_name: str, payload: dict):
    try:
        if task_name == "score_story":
            pipeline = Pipeline(payload["cfg"])
            result = pipeline._score_story_candidate(
                payload["video_path"], payload["candidate"]
            )
        elif task_name == "score_story_fallback":
            pipeline = Pipeline(payload["cfg"])
            result = pipeline._score_story_candidate_timeout_fallback(
                payload["candidate"]
            )
        elif task_name == "semantic_preview":
            pipeline = Pipeline(payload["cfg"])
            result = pipeline._semantic_preview_single(
                payload["video_path"], payload["candidate"]
            )
        elif task_name == "transcribe_auto_quality":
            pipeline = Pipeline(payload["cfg"])
            result = pipeline._transcribe_with_auto_quality(
                payload["wav_path"],
                payload["out_dir"],
                int(payload["idx"]),
                candidate=payload.get("candidate"),
            )
        elif task_name == "create_vertical_crop":
            debug_info = {}
            kwargs = dict(payload.get("kwargs") or {})
            kwargs["debug_info"] = debug_info
            result = {
                "ok": bool(create_vertical_crop(**kwargs)),
                "debug_info": debug_info,
            }
        else:
            raise RuntimeError(f"Unknown watchdog task: {task_name}")
        queue.put(("ok", result))
    except Exception as exc:
        queue.put(("error", repr(exc)))


def _run_in_subprocess_with_timeout(
    task_name: str,
    payload: dict,
    *,
    soft_timeout_seconds: float,
    hard_timeout_seconds: float,
    default=None,
    heartbeat_seconds: float | None = None,
    on_soft_timeout=None,
    on_hard_timeout=None,
    on_heartbeat=None,
):
    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()
    process = ctx.Process(
        target=_subprocess_worker, args=(result_queue, task_name, payload)
    )
    process.daemon = True
    process.start()
    start = time.perf_counter()
    soft_timeout_seconds = max(0.0, float(soft_timeout_seconds or 0.0))
    hard_timeout_seconds = max(
        soft_timeout_seconds + 0.01, float(hard_timeout_seconds or 0.0)
    )
    heartbeat_seconds = float(heartbeat_seconds or 0.0)
    soft_fired = False
    hard_fired = False
    last_heartbeat_bucket = -1
    result = default
    try:
        while True:
            elapsed = time.perf_counter() - start
            try:
                status, payload_out = result_queue.get(timeout=0.2)
                if status == "ok":
                    result = payload_out
                break
            except queue.Empty:
                pass
            if heartbeat_seconds > 0 and on_heartbeat:
                bucket = int(elapsed // heartbeat_seconds)
                if bucket > last_heartbeat_bucket:
                    last_heartbeat_bucket = bucket
                    on_heartbeat(elapsed)
            if (
                not soft_fired
                and soft_timeout_seconds > 0
                and elapsed >= soft_timeout_seconds
            ):
                soft_fired = True
                if on_soft_timeout:
                    on_soft_timeout(elapsed)
            if elapsed >= hard_timeout_seconds:
                hard_fired = True
                if on_hard_timeout:
                    on_hard_timeout(elapsed)
                if process.is_alive():
                    process.terminate()
                break
            if not process.is_alive():
                with contextlib.suppress(queue.Empty):
                    status, payload_out = result_queue.get_nowait()
                    if status == "ok":
                        result = payload_out
                break
    finally:
        with contextlib.suppress(Exception):
            if process.is_alive():
                process.terminate()
        with contextlib.suppress(Exception):
            process.join(timeout=1.0)
        with contextlib.suppress(Exception):
            result_queue.close()
    return {
        "result": result,
        "soft_timeout": soft_fired,
        "hard_timeout": hard_fired,
    }


def _run_with_timeout(
    fn,
    *args,
    timeout_seconds: float | None = None,
    default=None,
    on_timeout=None,
    on_error=None,
    heartbeat_seconds: float | None = None,
    on_heartbeat=None,
    **kwargs,
):
    timeout_seconds = float(timeout_seconds or 0.0)
    if timeout_seconds <= 0:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if on_error:
                on_error(exc)
            return default
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn, *args, **kwargs)
    try:
        start = time.perf_counter()
        heartbeat_seconds = float(heartbeat_seconds or 0.0)
        deadline = start + timeout_seconds
        while True:
            now = time.perf_counter()
            remaining = deadline - now
            if remaining <= 0:
                raise concurrent.futures.TimeoutError()
            wait_chunk = remaining
            if heartbeat_seconds > 0:
                wait_chunk = min(wait_chunk, heartbeat_seconds)
            try:
                return future.result(timeout=wait_chunk)
            except concurrent.futures.TimeoutError:
                now = time.perf_counter()
                if now >= deadline:
                    raise
                if heartbeat_seconds > 0 and on_heartbeat:
                    on_heartbeat(now - start)
    except Exception as exc:
        if isinstance(exc, concurrent.futures.TimeoutError):
            future.cancel()
            if on_timeout:
                on_timeout()
            executor.shutdown(wait=False, cancel_futures=True)
            return default
        if on_error:
            on_error(exc)
        executor.shutdown(wait=False, cancel_futures=True)
        return default
    finally:
        with contextlib.suppress(Exception):
            executor.shutdown(wait=False, cancel_futures=True)


def _hex_to_ass_color(value: str, default: str = "&H4FD5FF&") -> str:
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return default
    rr, gg, bb = text[0:2], text[2:4], text[4:6]
    return f"&H{bb}{gg}{rr}&"


def _subtitle_style(cfg: dict):
    template = str(cfg.get("subtitle_template", "classic_bold")).lower()
    fontsize = int(cfg.get("subtitle_fontsize", 38))
    margin_v = int(cfg.get("subtitle_margin_v", 360))
    active_color = _hex_to_ass_color(
        str(cfg.get("subtitle_active_word_color", "#FFD54F"))
    )
    anchor_mode = str(
        cfg.get("subtitle_vertical_anchor_mode", "fixed_mid_lower")
    ).lower()
    if anchor_mode == "square_bottom":
        margin_v = max(margin_v, 980)
        if fontsize < 40:
            fontsize = 40
    styles = {
        "classic_bold": {
            "fontsize": max(fontsize, 40),
            "bold": -1,
            "outline": 3,
            "shadow": 0,
            "margin_v": margin_v,
            "active_color": active_color,
        },
        "shorts_clean": {
            "fontsize": max(fontsize, 38),
            "bold": 0,
            "outline": 2,
            "shadow": 0,
            "margin_v": max(320, margin_v - 20),
            "active_color": active_color,
        },
        "focus_word_highlight": {
            "fontsize": max(fontsize, 40),
            "bold": -1,
            "outline": 2,
            "shadow": 0,
            "margin_v": margin_v,
            "active_color": active_color,
        },
        "drama_focus": {
            "fontsize": max(fontsize, 42),
            "bold": -1,
            "outline": 4,
            "shadow": 0,
            "margin_v": max(340, margin_v),
            "active_color": active_color,
        },
    }
    return styles.get(template, styles["classic_bold"])


def _median_or_zero(values):
    values = [item for item in values if isinstance(item, (int, float))]
    return round(statistics.median(values), 3) if values else 0.0


def _to_ass_time(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hh = int(total // 3600)
    mm = int((total % 3600) // 60)
    ss = int(total % 60)
    cs = int(round((total - int(total)) * 100))
    return f"{hh}:{mm:02d}:{ss:02d}.{cs:02d}"


def _build_ass_from_events(events: list[dict], ass_path: str, cfg: dict):
    style = _subtitle_style(cfg)
    anchor_mode = str(
        cfg.get("subtitle_vertical_anchor_mode", "fixed_mid_lower")
    ).lower()
    alignment = 2
    margin_v = int(style["margin_v"])
    fixed_position_tag = ""
    if anchor_mode == "fixed_mid_lower":
        alignment = 8
        margin_v = 760
        fixed_position_tag = r"{\an8\pos(360,760)}"
    elif anchor_mode == "square_bottom":
        alignment = 8
        margin_v = 980
        fixed_position_tag = r"{\an8\pos(360,980)}"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{style['fontsize']},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,{style['bold']},0,0,0,100,100,0,0,1,{style['outline']},{style['shadow']},{alignment},0,0,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for event in events:
        text = str(event.get("text", "")).replace("\n", "\\N")
        text = text.replace("&H4FD5FF&", style["active_color"])
        if anchor_mode == "fixed_mid_lower" and "\\N" not in text:
            text = r"{\alpha&HFF&.}\N" + text
        if fixed_position_tag:
            text = fixed_position_tag + text
        lines.append(
            f"Dialogue: 0,{_to_ass_time(event.get('start', 0.0))},{_to_ass_time(event.get('end', 0.0))},Default,,0,0,0,,{text}"
        )
    Path(ass_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def burn_subtitles_safe(
    video_path: str,
    subtitle_payload,
    final_path: str,
    cfg: dict,
    progress_callback=None,
):
    srt_path = (
        subtitle_payload
        if isinstance(subtitle_payload, str)
        else subtitle_payload.get("srt_path")
    )
    if not srt_path or not os.path.exists(srt_path):
        return False
    fd, ass_path = tempfile.mkstemp(suffix=".ass")
    os.close(fd)
    try:
        if (
            str(cfg.get("subtitle_render_mode", "ass_word_highlight"))
            == "ass_word_highlight"
        ):
            events = list((subtitle_payload or {}).get("ass_word_events") or [])
            if not events:
                events = list((subtitle_payload or {}).get("segments") or [])
            _build_ass_from_events(events, ass_path, cfg)
        else:
            blocks = [
                block.strip()
                for block in re.split(
                    r"\n\s*\n",
                    Path(srt_path).read_text(encoding="utf-8", errors="ignore"),
                )
                if block.strip()
            ]
            events = []
            for block in blocks:
                rows = block.splitlines()
                if len(rows) < 3 or "-->" not in rows[1]:
                    continue
                start, end = [part.strip() for part in rows[1].split("-->")]
                hhmmss, ms = (start.replace(".", ",").split(",") + ["0"])[:2]
                sh, sm, ss = [int(part) for part in hhmmss.split(":")]
                start_sec = sh * 3600 + sm * 60 + ss + int(ms[:3]) / 1000.0
                hhmmss, ms = (end.replace(".", ",").split(",") + ["0"])[:2]
                eh, em, es = [int(part) for part in hhmmss.split(":")]
                end_sec = eh * 3600 + em * 60 + es + int(ms[:3]) / 1000.0
                events.append(
                    {"start": start_sec, "end": end_sec, "text": "\n".join(rows[2:])}
                )
            _build_ass_from_events(events, ass_path, cfg)
        escaped = os.path.abspath(ass_path).replace("\\", "/").replace(":", r"\:")
        rc, _, err = run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                f"subtitles='{escaped}'",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-ac",
                "2",
                "-c:a",
                "aac",
                final_path,
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            timeout=300,
        )
        if rc != 0:
            _emit(progress_callback, "warning", f"Subtitle burn failed: {err[:220]}")
        return (
            rc == 0
            and os.path.exists(final_path)
            and os.path.getsize(final_path) > 1024
        )
    except Exception as exc:
        _emit(progress_callback, "warning", f"Subtitle conversion failed: {exc}")
        return False
    finally:
        try:
            os.remove(ass_path)
        except Exception:
            pass


def _wav_to_pcm16_mono(src: str, dst: str):
    rc, _, _ = run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            src,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            dst,
            "-hide_banner",
            "-loglevel",
            "error",
        ],
        timeout=180,
    )
    return rc == 0


def _read_wave(path: str):
    with contextlib.closing(wave.open(path, "rb")) as wf:
        return wf.readframes(wf.getnframes()), wf.getframerate()


def _frame_iter(frame_ms, audio_bytes, sr):
    size = int(sr * (frame_ms / 1000.0) * 2)
    offset = 0
    ts = 0.0
    while offset + size <= len(audio_bytes):
        yield audio_bytes[offset : offset + size], ts
        ts += frame_ms / 1000.0
        offset += size


def get_voiced_intervals_webrtc(wav_path: str, cfg: dict):
    if not WEBRTC_AVAILABLE:
        return []
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        if not _wav_to_pcm16_mono(wav_path, tmp):
            return []
        pcm, sr = _read_wave(tmp)
        vad = webrtcvad.Vad(int(cfg.get("vad_aggressiveness", 2)))
        voiced, start = [], None
        frame_ms = int(cfg.get("frame_ms", 30))
        frames = list(_frame_iter(frame_ms, pcm, sr))
        for chunk, ts in frames:
            try:
                speech = vad.is_speech(chunk, sr)
            except Exception:
                speech = False
            if speech and start is None:
                start = ts
            if not speech and start is not None:
                voiced.append((start, ts + frame_ms / 1000.0))
                start = None
        if start is not None and frames:
            voiced.append((start, frames[-1][1] + frame_ms / 1000.0))
        return voiced
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _merge_intervals(intervals, max_gap=0.35):
    if not intervals:
        return []
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        prev = merged[-1]
        if start - prev[1] <= max_gap:
            prev[1] = max(prev[1], end)
        else:
            merged.append([start, end])
    return [
        (round(item[0], 3), round(item[1], 3)) for item in merged if item[1] > item[0]
    ]


def _safe_voiced_intervals(wav_path: str, cfg: dict):
    voiced = _merge_intervals(get_voiced_intervals_webrtc(wav_path, cfg), max_gap=0.25)
    if voiced:
        return voiced
    silences = detect_silence_ffmpeg(
        wav_path, silence_thresh_db=-40, min_silence_len=0.55
    )
    cursor, voiced = 0.0, []
    for start, end in silences:
        if start - cursor > 0.35:
            voiced.append((cursor, start))
        cursor = end
    try:
        with contextlib.closing(wave.open(wav_path, "rb")) as wf:
            total = wf.getnframes() / float(wf.getframerate())
    except Exception:
        total = 0.0
    if total - cursor > 0.35:
        voiced.append((cursor, total))
    return _merge_intervals(voiced, max_gap=0.25)


def _pause_energy(
    pcm: np.ndarray, sample_rate: int, gap_start: float, gap_end: float
) -> float:
    if pcm.size == 0:
        return 0.0
    left = max(0, int(gap_start * sample_rate))
    right = min(len(pcm), int(gap_end * sample_rate))
    if right - left <= sample_rate * 0.1:
        return 0.0
    gap_pcm = pcm[left:right].astype(np.float32)
    return (
        float(np.sqrt(np.mean(np.square(gap_pcm)))) / 32768.0 if gap_pcm.size else 0.0
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _classify_silence_pause(
    gap_dur: float,
    energy: float,
    prev_dur: float,
    next_dur: float,
    continuation_bonus: float,
    cfg: dict,
):
    event_threshold = float(cfg.get("min_non_silent_event_energy", 0.16))
    soft_threshold = float(cfg.get("pause_soft_keep_min_energy", 0.11))
    story_keep_energy = max(
        event_threshold,
        float(cfg.get("pause_story_keep_min_energy", event_threshold + 0.02)),
    )
    max_normal = 1.5
    max_emotional = 2.5
    max_comedic = 3.0
    low_energy = energy <= max(0.03, soft_threshold * 0.45)
    medium_energy = energy >= max(0.06, soft_threshold * 0.85)
    short_turn = min(prev_dur or 0.0, next_dur or 0.0) <= 0.65
    strong_turn = min(prev_dur or 0.0, next_dur or 0.0) >= 0.85
    asymmetric_turn = abs((prev_dur or 0.0) - (next_dur or 0.0)) >= 0.55

    if gap_dur >= 2.0 and low_energy and continuation_bonus < 0.45:
        return {
            "silence_type": "dead_air",
            "silence_confidence": 0.92,
            "trim_allowed": True,
            "max_allowed_silence": max_normal,
            "reason": "low_energy_long_gap",
        }
    if gap_dur <= 0.75:
        if continuation_bonus >= 0.72 and (short_turn or asymmetric_turn):
            return {
                "silence_type": "reaction_pause",
                "silence_confidence": 0.68,
                "trim_allowed": False,
                "max_allowed_silence": max_normal,
                "reason": "reaction_hold",
            }
        return {
            "silence_type": "unknown",
            "silence_confidence": 0.44,
            "trim_allowed": False,
            "max_allowed_silence": max_normal,
            "reason": "short_gap",
        }
    if gap_dur <= max_comedic:
        if continuation_bonus >= 0.84 and strong_turn and medium_energy:
            return {
                "silence_type": "emotional_pause",
                "silence_confidence": 0.74,
                "trim_allowed": False,
                "max_allowed_silence": max_emotional,
                "reason": "emotional_hold",
            }
        if continuation_bonus >= 0.82 and short_turn and gap_dur <= max_comedic:
            return {
                "silence_type": "comedic_pause",
                "silence_confidence": 0.72,
                "trim_allowed": False,
                "max_allowed_silence": max_comedic,
                "reason": "comic_timing",
            }
        if continuation_bonus >= 0.76 and strong_turn and gap_dur <= max_emotional:
            return {
                "silence_type": "tension_pause",
                "silence_confidence": 0.70,
                "trim_allowed": False,
                "max_allowed_silence": max_emotional,
                "reason": "tension_bridge",
            }
        if continuation_bonus >= 0.62 and medium_energy:
            return {
                "silence_type": "reaction_pause",
                "silence_confidence": 0.61,
                "trim_allowed": False,
                "max_allowed_silence": max_normal,
                "reason": "reaction_flow",
            }
    if gap_dur > 2.0 and low_energy and continuation_bonus < 0.58:
        return {
            "silence_type": "unknown",
            "silence_confidence": 0.48,
            "trim_allowed": True,
            "max_allowed_silence": max_normal,
            "reason": "uncertain_low_energy",
        }
    if energy >= story_keep_energy and continuation_bonus >= 0.55:
        return {
            "silence_type": "emotional_pause",
            "silence_confidence": 0.58,
            "trim_allowed": False,
            "max_allowed_silence": max_emotional,
            "reason": "energetic_hold",
        }
    return {
        "silence_type": "unknown",
        "silence_confidence": 0.52 if low_energy else 0.57,
        "trim_allowed": False
        if gap_dur <= max_comedic
        else low_energy and continuation_bonus < 0.40,
        "max_allowed_silence": max_normal if gap_dur <= max_emotional else max_comedic,
        "reason": "uncertain",
    }


def _pacing_score_from_pause_timeline(
    pause_timeline: list[dict],
    *,
    original_duration: float = 0.0,
    output_duration: float = 0.0,
    subtitle_signals: dict | None = None,
) -> float:
    subtitle_signals = dict(subtitle_signals or {})
    original_duration = max(0.0, float(original_duration))
    output_duration = max(0.0, float(output_duration))
    meaningful_pause_kept_seconds = 0.0
    dead_air_cut_seconds = 0.0
    unknown_cut_seconds = 0.0
    trim_events = 0
    for item in pause_timeline or []:
        duration = max(0.0, float(item.get("duration", 0.0) or 0.0))
        decision = str(item.get("decision", ""))
        silence_type = str(item.get("silence_type", "unknown") or "unknown")
        if decision == "cut":
            trim_events += 1
            if silence_type == "dead_air":
                dead_air_cut_seconds += duration
            elif silence_type == "unknown":
                unknown_cut_seconds += duration
        elif decision in {"soft_keep", "keep_for_story"} and silence_type in {
            "comedic_pause",
            "emotional_pause",
            "reaction_pause",
            "tension_pause",
        }:
            meaningful_pause_kept_seconds += duration
    dialogue_flow = _clamp01(
        float(subtitle_signals.get("dialogue_exchange_score", 0.0) or 0.0)
    )
    interestingness = _clamp01(
        float(subtitle_signals.get("interestingness_score", 0.0) or 0.0)
    )
    hook_score = _clamp01(float(subtitle_signals.get("hook_score", 0.0) or 0.0))
    context_score = _clamp01(
        max(
            float(subtitle_signals.get("story_context_score", 0.0) or 0.0),
            float(subtitle_signals.get("closure_score", 0.0) or 0.0),
        )
    )
    subtitle_quality = _clamp01(
        float(subtitle_signals.get("subtitle_quality_score", 0.0) or 0.0)
    )
    trimmed_ratio = 0.0
    if original_duration > 0.0:
        trimmed_ratio = max(0.0, 1.0 - min(1.0, output_duration / original_duration))
    dead_air_ratio = dead_air_cut_seconds / max(1.0, original_duration)
    unknown_ratio = unknown_cut_seconds / max(1.0, original_duration)
    meaningful_keep_ratio = meaningful_pause_kept_seconds / max(1.0, original_duration)
    flow_score = (
        dialogue_flow * 0.26
        + interestingness * 0.18
        + hook_score * 0.12
        + context_score * 0.12
        + subtitle_quality * 0.08
        + min(1.0, meaningful_keep_ratio * 2.0) * 0.08
        + min(1.0, trimmed_ratio * 1.2) * 0.08
        + min(1.0, 1.0 - max(0.0, dead_air_ratio * 1.5 + unknown_ratio * 0.75)) * 0.08
    )
    penalty = min(
        0.40,
        dead_air_ratio * 1.7 + unknown_ratio * 0.75 + max(0, trim_events - 1) * 0.025,
    )
    pacing = _clamp01(flow_score - penalty + 0.16)
    return round(pacing, 4)


def _build_pause_timeline(
    voiced: list[tuple[float, float]],
    pcm: np.ndarray,
    sample_rate: int,
    cfg: dict,
    detected_silences: list[tuple[float, float]] | None = None,
    total_duration: float | None = None,
):
    keep_short_gaps = max(
        1.0,
        float(
            cfg.get(
                "story_pause_cut_threshold_seconds",
                cfg.get("keep_dialogue_gap_seconds", 1.0),
            )
        ),
    )
    max_story_gap = max(
        keep_short_gaps,
        float(
            cfg.get(
                "story_pause_keep_max_seconds",
                cfg.get("story_extension_max_pause_seconds", 1.15),
            )
        ),
    )
    story_gap_keep_limit = min(max_story_gap, keep_short_gaps + 0.15)
    event_threshold = float(cfg.get("min_non_silent_event_energy", 0.16))
    soft_threshold = float(cfg.get("pause_soft_keep_min_energy", 0.11))
    story_keep_energy = max(
        event_threshold,
        float(cfg.get("pause_story_keep_min_energy", event_threshold + 0.02)),
    )
    timeline = []
    candidate_gaps = []
    for index in range(1, len(voiced)):
        prev_start, prev_end = voiced[index - 1]
        next_start, _next_end = voiced[index]
        gap_start = float(prev_end)
        gap_end = float(next_start)
        if gap_end - gap_start > 0.0:
            candidate_gaps.append((gap_start, gap_end))
    for start, end in list(detected_silences or []):
        start = max(0.0, float(start))
        end = max(start, float(end))
        if total_duration is not None:
            end = min(float(total_duration), end)
        if end - start >= max(0.45, keep_short_gaps * 0.8):
            candidate_gaps.append((start, end))
    if not candidate_gaps:
        return timeline
    merged_gaps = []
    for gap_start, gap_end in sorted(
        candidate_gaps, key=lambda item: (item[0], item[1])
    ):
        if not merged_gaps:
            merged_gaps.append([gap_start, gap_end])
            continue
        prev = merged_gaps[-1]
        if gap_start <= prev[1] + 0.08:
            prev[1] = max(prev[1], gap_end)
        else:
            merged_gaps.append([gap_start, gap_end])
    for gap_start, gap_end in merged_gaps:
        gap_dur = max(0.0, gap_end - gap_start)
        if gap_dur <= 0.0:
            continue
        energy = _pause_energy(pcm, sample_rate, gap_start, gap_end)
        prev_turn = None
        next_turn = None
        for start, end in voiced:
            if float(end) <= gap_start + 0.02:
                prev_turn = (start, end)
            elif float(start) >= gap_end - 0.02:
                next_turn = (start, end)
                break
        prev_dur = max(0.0, float(prev_turn[1] - prev_turn[0])) if prev_turn else 0.0
        next_dur = max(0.0, float(next_turn[1] - next_turn[0])) if next_turn else 0.0
        continuation_bonus = min(1.0, (min(prev_dur, next_dur) / 1.15))
        event_sensitive = energy >= event_threshold
        soft_context = energy >= soft_threshold or continuation_bonus >= 0.62
        strong_story_context = (
            energy >= story_keep_energy
            or (energy >= soft_threshold and continuation_bonus >= 0.78)
            or continuation_bonus >= 0.92
        )
        classification = _classify_silence_pause(
            gap_dur, energy, prev_dur, next_dur, continuation_bonus, cfg
        )
        silence_type = str(classification.get("silence_type", "unknown"))
        silence_confidence = round(
            float(classification.get("silence_confidence", 0.0) or 0.0), 4
        )
        trim_allowed = bool(classification.get("trim_allowed", False))
        if gap_dur <= keep_short_gaps:
            decision = "soft_keep"
            reason = "short_gap"
        elif silence_type == "dead_air" or (
            silence_type == "unknown" and silence_confidence < 0.50 and trim_allowed
        ):
            decision = "cut"
            reason = classification.get(
                "reason",
                "dead_air" if silence_type == "dead_air" else "unknown_low_confidence",
            )
        elif gap_dur <= story_gap_keep_limit and (
            strong_story_context
            or soft_context
            or event_sensitive
            or continuation_bonus >= 0.50
            or not trim_allowed
        ):
            decision = "keep_for_story"
            if silence_type == "comedic_pause":
                reason = "comedic_timing"
            elif silence_type == "emotional_pause":
                reason = "emotional_hold"
            elif silence_type == "reaction_pause":
                reason = "reaction_hold"
            elif silence_type == "tension_pause":
                reason = "tension_hold"
            elif energy >= story_keep_energy:
                reason = "event_energy"
            elif continuation_bonus >= 0.92:
                reason = "continuation_bonus"
            elif soft_context:
                reason = "soft_context"
            else:
                reason = "story_gap"
        else:
            if (
                silence_type == "unknown"
                and silence_confidence >= 0.50
                and not trim_allowed
            ):
                decision = "keep_for_story"
                reason = "uncertain_preserve"
            else:
                decision = "cut"
                reason = (
                    "long_silence" if gap_dur > max_story_gap else "over_2s_silence"
                )
        timeline.append(
            {
                "start": round(gap_start, 3),
                "end": round(gap_end, 3),
                "duration": round(gap_dur, 3),
                "energy": round(energy, 4),
                "continuation_bonus": round(continuation_bonus, 4),
                "silence_type": silence_type,
                "silence_confidence": silence_confidence,
                "max_allowed_silence": round(
                    float(classification.get("max_allowed_silence", max_story_gap)), 3
                ),
                "trim_allowed": trim_allowed,
                "decision": decision,
                "reason": reason,
            }
        )
    return timeline


def _pause_timeline_stats(timeline: list[dict]):
    pause_cut = [item for item in timeline if item.get("decision") == "cut"]
    pause_soft_keep = [item for item in timeline if item.get("decision") == "soft_keep"]
    pause_story_keep = [
        item for item in timeline if item.get("decision") == "keep_for_story"
    ]
    silence_type_counts = Counter(
        str(item.get("silence_type", "unknown") or "unknown") for item in timeline
    )
    silence_trim_events = [
        {
            "start": round(float(item.get("start", 0.0) or 0.0), 3),
            "end": round(float(item.get("end", 0.0) or 0.0), 3),
            "duration": round(float(item.get("duration", 0.0) or 0.0), 3),
            "silence_type": str(item.get("silence_type", "unknown") or "unknown"),
            "silence_confidence": round(
                float(item.get("silence_confidence", 0.0) or 0.0), 4
            ),
            "reason": str(item.get("reason", "")),
        }
        for item in pause_cut
    ]
    return {
        "pause_cut_count": len(pause_cut),
        "pause_soft_keep_count": len(pause_soft_keep),
        "pause_story_keep_count": len(pause_story_keep),
        "long_pause_cut_seconds_total": round(
            sum(float(item.get("duration", 0.0) or 0.0) for item in pause_cut), 3
        ),
        "story_sensitive_pause_kept_seconds_total": round(
            sum(float(item.get("duration", 0.0) or 0.0) for item in pause_story_keep), 3
        ),
        "trimmed_silence_seconds": round(
            sum(float(item.get("duration", 0.0) or 0.0) for item in pause_cut), 3
        ),
        "silence_type_counts": dict(silence_type_counts),
        "silence_trim_events": silence_trim_events,
    }


def trim_silence_in_candidate_ms(
    video_src: str,
    seg_start: float,
    seg_end: float,
    out_path: str,
    cfg: dict,
    progress_callback=None,
):
    tmp_dir = tempfile.mkdtemp(prefix="sf_trim_")
    try:
        segment = os.path.join(tmp_dir, "segment.mp4")
        wav = os.path.join(tmp_dir, "segment.wav")
        rc, _, _ = run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_src,
                "-ss",
                str(seg_start),
                "-to",
                str(seg_end),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                segment,
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            timeout=300,
        )
        if rc != 0:
            return False
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                segment,
                "-ac",
                "1",
                "-ar",
                "16000",
                "-vn",
                wav,
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            timeout=180,
        )
        voiced = _safe_voiced_intervals(wav, cfg)
        if not voiced:
            return False
        parts, kept, max_len = [], 0.0, float(cfg.get("max_short_seconds", 60))
        pause_removed_segments = []
        pause_kept_for_context = []
        pcm = np.array([], dtype=np.int16)
        sample_rate = 16000
        total_duration = max(0.0, float(seg_end) - float(seg_start))
        try:
            with wave.open(wav, "rb") as handle:
                sample_rate = int(handle.getframerate() or 16000)
                pcm = np.frombuffer(
                    handle.readframes(handle.getnframes()), dtype=np.int16
                )
                total_duration = max(
                    total_duration, handle.getnframes() / float(sample_rate or 16000)
                )
        except Exception:
            pcm = np.array([], dtype=np.int16)
        silences = detect_silence_ffmpeg(
            wav,
            silence_thresh_db=float(cfg.get("silence_thresh_db", -40.0)),
            min_silence_len=max(
                0.9, float(cfg.get("story_pause_cut_threshold_seconds", 1.0)) * 0.9
            ),
        )
        pause_timeline = _build_pause_timeline(
            voiced,
            pcm,
            sample_rate,
            cfg,
            detected_silences=silences,
            total_duration=total_duration,
        )
        silence_rewrite_plan = build_silence_rewrite_plan(pause_timeline)
        pause_by_key = {
            (
                round(float(item.get("start", 0.0)), 3),
                round(float(item.get("end", 0.0)), 3),
            ): item
            for item in pause_timeline
        }
        cut_intervals = []
        for item in pause_timeline:
            if str(item.get("decision", "")) == "cut":
                cut_intervals.append([float(item["start"]), float(item["end"])])
                pause_removed_segments.append(
                    [round(float(item["start"]), 3), round(float(item["end"]), 3)]
                )
            elif str(item.get("decision", "")) in {"soft_keep", "keep_for_story"}:
                pause_kept_for_context.append(
                    [round(float(item["start"]), 3), round(float(item["end"]), 3)]
                )
        merged = []
        if cut_intervals:
            cursor = 0.0
            for cut_start, cut_end in cut_intervals:
                if cut_start - cursor > 0.20:
                    merged.append([cursor, cut_start])
                cursor = max(cursor, cut_end)
            if total_duration - cursor > 0.20:
                merged.append([cursor, total_duration])
        else:
            for start, end in voiced:
                if not merged:
                    merged.append([start, end])
                else:
                    gap_start = float(merged[-1][1])
                    gap_end = float(start)
                    pause_info = pause_by_key.get(
                        (round(gap_start, 3), round(gap_end, 3)), {}
                    )
                    pause_decision = str(pause_info.get("decision", "cut"))
                    if pause_decision in {"soft_keep", "keep_for_story"}:
                        merged[-1][1] = end
                    else:
                        merged.append([start, end])
        for index, (start, end) in enumerate(merged):
            take = min(end - start, max_len - kept)
            if take <= 0.25:
                continue
            part = os.path.join(tmp_dir, f"part_{index:03d}.mp4")
            rc, _, _ = run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    segment,
                    "-ss",
                    str(round(start, 3)),
                    "-t",
                    str(round(take, 3)),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    "-b:a",
                    "160k",
                    "-movflags",
                    "+faststart",
                    part,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                ],
                timeout=180,
            )
            if rc == 0 and os.path.exists(part) and os.path.getsize(part) > 1024:
                parts.append(part)
                kept += take
            elif os.path.exists(part):
                with contextlib.suppress(Exception):
                    os.remove(part)
        if not parts:
            return False
        concat = os.path.join(tmp_dir, "concat.txt")
        with open(concat, "w", encoding="utf-8") as handle:
            for part in parts:
                handle.write(f"file '{os.path.abspath(part)}'\n")
        rc, _, err = run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                out_path,
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            timeout=300,
        )
        if rc != 0:
            _emit(progress_callback, "warning", f"Trim concat failed: {err[:160]}")
        pause_stats = _pause_timeline_stats(pause_timeline)
        pause_durations_before = [
            float(item.get("duration", 0.0) or 0.0)
            for item in pause_timeline
            if float(item.get("duration", 0.0) or 0.0) > 0.0
        ]
        pause_durations_after = [
            float(item.get("duration", 0.0) or 0.0)
            for item in pause_timeline
            if str(item.get("decision", "")) != "cut"
            and float(item.get("duration", 0.0) or 0.0) > 0.0
        ]
        avg_pause_duration_before = (
            round(sum(pause_durations_before) / len(pause_durations_before), 4)
            if pause_durations_before
            else 0.0
        )
        avg_pause_duration_after = (
            round(sum(pause_durations_after) / len(pause_durations_after), 4)
            if pause_durations_after
            else 0.0
        )
        pause_cut_seconds_total = round(
            sum(max(0.0, end - start) for start, end in pause_removed_segments), 3
        )
        output_duration = 0.0
        with contextlib.suppress(Exception):
            has_video, output_duration = probe_video(out_path)
            if not has_video:
                output_duration = 0.0
        original_duration = max(0.0, total_duration)
        trimmed_delta = max(0.0, original_duration - float(output_duration or 0.0))
        pause_policy_failed = bool(
            pause_stats.get("pause_cut_count", 0)
            and (
                not pause_removed_segments
                or trimmed_delta < min(0.35, max(0.10, pause_cut_seconds_total * 0.18))
            )
        )
        pacing_score = _pacing_score_from_pause_timeline(
            pause_timeline,
            original_duration=original_duration,
            output_duration=float(output_duration or 0.0),
            subtitle_signals=None,
        )
        trimmed_silence_seconds = float(
            pause_stats.get("trimmed_silence_seconds", pause_cut_seconds_total)
            or pause_cut_seconds_total
        )
        trim_silence_in_candidate_ms.last_stats = {
            "pause_removed_segments": pause_removed_segments,
            "pause_kept_for_context": pause_kept_for_context,
            "pause_timeline": pause_timeline,
            "silence_rewrite_plan": silence_rewrite_plan,
            **pause_stats,
            "silence_removed_seconds": float(
                silence_rewrite_plan.get(
                    "trimmed_silence_seconds", pause_cut_seconds_total
                )
                or pause_cut_seconds_total
            ),
            "silence_removed_segments": int(
                silence_rewrite_plan.get("pause_cut_count", len(pause_removed_segments))
                or len(pause_removed_segments)
            ),
            "avg_pause_duration_before": avg_pause_duration_before,
            "avg_pause_duration_after": avg_pause_duration_after,
            "pacing_score": pacing_score,
            "trimmed_silence_seconds": round(trimmed_silence_seconds, 3),
            "pause_policy_applied": bool(pause_timeline),
            "pause_policy_failed": pause_policy_failed,
            "pause_cut_segments_count": len(pause_removed_segments),
            "pause_cut_seconds_total": pause_cut_seconds_total,
            "pause_output_trim_delta_seconds": round(trimmed_delta, 3),
            "pause_story_keep_reasons": sorted(
                {
                    str(item.get("reason", ""))
                    for item in pause_timeline
                    if str(item.get("decision", "")) == "keep_for_story"
                }
            ),
        }
        if pause_cut_seconds_total > 0.0:
            _emit(
                progress_callback,
                "pacing",
                f"trimmed_dead_air={pause_cut_seconds_total:.1f}s",
            )
        meaningful_kept = float(
            pause_stats.get("story_sensitive_pause_kept_seconds_total", 0.0) or 0.0
        )
        if meaningful_kept > 0.0:
            _emit(
                progress_callback,
                "pacing",
                f"preserved_comedic_pause={meaningful_kept:.1f}s",
            )
        if avg_pause_duration_before > 0.0:
            _emit(
                progress_callback,
                "pacing",
                f"avg_pause_duration_before={avg_pause_duration_before:.2f}s avg_pause_duration_after={avg_pause_duration_after:.2f}s",
            )
        _emit(progress_callback, "pacing", f"pacing_score={pacing_score:.2f}")
        return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1024
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


trim_silence_in_candidate_ms.last_stats = {
    "pause_removed_segments": [],
    "pause_kept_for_context": [],
    "pause_timeline": [],
    "pause_cut_count": 0,
    "pause_soft_keep_count": 0,
    "pause_story_keep_count": 0,
    "long_pause_cut_seconds_total": 0.0,
    "story_sensitive_pause_kept_seconds_total": 0.0,
    "trimmed_silence_seconds": 0.0,
    "silence_type_counts": {},
    "silence_trim_events": [],
    "pacing_score": 0.0,
    "pause_policy_applied": False,
    "pause_policy_failed": False,
    "pause_cut_segments_count": 0,
    "pause_cut_seconds_total": 0.0,
    "pause_output_trim_delta_seconds": 0.0,
    "pause_story_keep_reasons": [],
}


def _max_story_duration(cfg: dict) -> float:
    return min(
        float(cfg.get("allow_story_extension_seconds", 60)),
        float(cfg.get("max_short_seconds", 60)),
    )


def _subtitle_dialogue_keep_segments(subtitle_info: dict, duration: float, cfg: dict):
    segments = []
    lead_pad = max(0.0, float(cfg.get("dialogue_compact_lead_pad_seconds", 0.08)))
    tail_pad = max(0.0, float(cfg.get("dialogue_compact_tail_pad_seconds", 0.12)))
    cut_threshold = max(1.0, float(cfg.get("story_pause_cut_threshold_seconds", 1.0)))
    max_story_gap = min(
        max(
            cut_threshold,
            float(
                cfg.get(
                    "story_pause_keep_max_seconds",
                    cfg.get("story_extension_max_pause_seconds", 1.15),
                )
            ),
        ),
        cut_threshold + 0.15,
    )

    def _looks_like_continuation(prev_text: str, next_text: str) -> bool:
        prev_clean = _clean_text(prev_text)
        next_clean = _clean_text(next_text)
        if not prev_clean or not next_clean:
            return False
        prev_tokens = _tokenize(prev_clean)
        next_tokens = _tokenize(next_clean)
        if not prev_tokens or not next_tokens:
            return False
        if not prev_clean.endswith(TERMINAL_PUNCTUATION):
            return True
        if prev_clean.endswith((",", ";", ":", "—", "-")):
            return True
        if next_clean[:1].islower():
            return True
        if len(prev_tokens) <= 4 or len(next_tokens) <= 4:
            return True
        return False

    raw_segments = list(subtitle_info.get("segments", []) or [])
    for item in raw_segments:
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        start = max(0.0, float(item.get("start", 0.0) or 0.0) - lead_pad)
        end = min(float(duration), float(item.get("end", 0.0) or 0.0) + tail_pad)
        if end - start >= 0.14:
            segments.append({"start": start, "end": end, "text": text})
    if not segments:
        return [], []
    merged = []
    removed = []
    for item in sorted(segments, key=lambda pair: (pair["start"], pair["end"])):
        start = float(item["start"])
        end = float(item["end"])
        text = str(item.get("text", "") or "")
        if not merged:
            if start > cut_threshold:
                removed.append([0.0, round(start, 3)])
            merged.append({"start": start, "end": end, "text": text})
            continue
        gap = max(0.0, start - float(merged[-1]["end"]))
        prev_text = str(merged[-1].get("text", "") or "")
        if gap <= cut_threshold:
            merged[-1]["end"] = max(float(merged[-1]["end"]), end)
            if text and text not in prev_text:
                merged[-1]["text"] = f"{prev_text} {text}".strip()
        elif gap <= max_story_gap and _looks_like_continuation(prev_text, text):
            merged[-1]["end"] = max(float(merged[-1]["end"]), end)
            if text and text not in prev_text:
                merged[-1]["text"] = f"{prev_text} {text}".strip()
        else:
            removed.append([round(float(merged[-1]["end"]), 3), round(start, 3)])
            merged.append({"start": start, "end": end, "text": text})
    if merged and duration - float(merged[-1]["end"]) > cut_threshold:
        removed.append([round(float(merged[-1]["end"]), 3), round(duration, 3)])
    return [
        (round(float(item["start"]), 3), round(float(item["end"]), 3))
        for item in merged
    ], removed


def _concat_video_segments(
    video_src: str, keep_segments: list[tuple[float, float]], out_path: str
):
    tmp_dir = tempfile.mkdtemp(prefix="sf_dialogue_compact_")
    try:
        parts = []
        for index, (start, end) in enumerate(keep_segments):
            take = max(0.0, float(end) - float(start))
            if take <= 0.12:
                continue
            part = os.path.join(tmp_dir, f"part_{index:03d}.mp4")
            rc, _, _ = run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    video_src,
                    "-ss",
                    str(round(float(start), 3)),
                    "-t",
                    str(round(take, 3)),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    "-b:a",
                    "160k",
                    "-movflags",
                    "+faststart",
                    part,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                ],
                timeout=240,
            )
            if rc == 0 and os.path.exists(part) and os.path.getsize(part) > 1024:
                parts.append(part)
            elif os.path.exists(part):
                with contextlib.suppress(Exception):
                    os.remove(part)
        if not parts:
            return False, 0.0
        concat = os.path.join(tmp_dir, "concat.txt")
        with open(concat, "w", encoding="utf-8") as handle:
            for part in parts:
                handle.write(f"file '{os.path.abspath(part)}'\n")
        rc, _, _ = run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                out_path,
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            timeout=300,
        )
        output_duration = 0.0
        with contextlib.suppress(Exception):
            has_video, output_duration = probe_video(out_path)
            if not has_video:
                output_duration = 0.0
        return rc == 0 and os.path.exists(out_path) and os.path.getsize(
            out_path
        ) > 1024, float(output_duration or 0.0)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _sanitize_compacted_video(video_path: str, cfg=None) -> tuple[bool, float]:
    cfg = cfg or {}
    if not os.path.exists(video_path):
        return False, 0.0
    sanitized = video_path + ".sanitized.mp4"
    rc, _, _ = run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            video_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vsync",
            "cfr",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            sanitized,
            "-hide_banner",
            "-loglevel",
            "error",
        ],
        timeout=300,
    )
    if rc == 0 and os.path.exists(sanitized) and os.path.getsize(sanitized) > 1024:
        try:
            os.replace(sanitized, video_path)
        except Exception:
            with contextlib.suppress(Exception):
                os.remove(sanitized)
            return False, 0.0
        with contextlib.suppress(Exception):
            if os.path.exists(sanitized):
                os.remove(sanitized)
        has_video, duration = probe_video(video_path)
        return bool(has_video and duration > 0.0), float(duration or 0.0)
    with contextlib.suppress(Exception):
        if os.path.exists(sanitized):
            os.remove(sanitized)
    return False, 0.0


def _validate_compacted_video_integrity(video_path: str) -> tuple[bool, float]:
    clip = None
    try:
        clip = VideoFileClip(video_path)
        duration = float(clip.duration or 0.0)
        if duration <= 0.2:
            clip.close()
            return False, duration
        probe_points = sorted(
            {
                max(0.0, duration - 0.05),
                max(0.0, duration * 0.5),
                max(0.0, duration - 0.01),
            }
        )
        for ts in probe_points:
            frame = clip.get_frame(max(0.0, min(max(0.0, duration - 0.01), ts)))
            if frame is None or getattr(frame, "size", 0) == 0:
                clip.close()
                return False, duration
        clip.close()
        return True, duration
    except Exception:
        if clip is not None:
            with contextlib.suppress(Exception):
                clip.close()
        return False, 0.0


def _video_metrics(video_path: str, start: float, end: float):
    motion, brightness = [], []
    try:
        clip = VideoFileClip(video_path)
        times = np.linspace(
            start, min(end, clip.duration or end), num=4, endpoint=False
        ).tolist()
        prev = None
        for ts in times:
            frame = clip.get_frame(ts).astype(np.float32)
            brightness.append(float(frame.mean() / 255.0))
            if prev is not None:
                motion.append(float(np.mean(np.abs(frame - prev)) / 255.0))
            prev = frame
        clip.close()
    except Exception:
        pass
    return {
        "motion": float(sum(motion) / len(motion)) if motion else 0.0,
        "brightness": float(sum(brightness) / len(brightness)) if brightness else 0.5,
    }


_AUDIO_CACHE_ROOT = Path(tempfile.gettempdir()) / "shorts_factory_audio_cache"
_AUDIO_SUMMARY_CACHE_VERSION = 1


def _audio_cache_key(video_path: str) -> str:
    try:
        stat = Path(video_path).stat()
        payload = f"{Path(video_path).resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except Exception:
        payload = f"{Path(video_path)}|0|0"
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()


def _audio_cache_dir(video_path: str) -> Path:
    return _AUDIO_CACHE_ROOT / _audio_cache_key(video_path)


def _audio_summary_cache_key(start: float, end: float, cfg: dict) -> str:
    payload = {
        "version": _AUDIO_SUMMARY_CACHE_VERSION,
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "frame_ms": int(cfg.get("frame_ms", 30) or 30),
        "vad_aggressiveness": int(cfg.get("vad_aggressiveness", 2) or 2),
        "keep_dialogue_gap_seconds": round(
            float(cfg.get("keep_dialogue_gap_seconds", 1.0) or 1.0), 3
        ),
        "story_merge_gap_seconds": round(
            float(cfg.get("story_merge_gap_seconds", 1.0) or 1.0), 3
        ),
        "story_pause_cut_threshold_seconds": round(
            float(cfg.get("story_pause_cut_threshold_seconds", 1.0) or 1.0), 3
        ),
        "story_pause_keep_max_seconds": round(
            float(cfg.get("story_pause_keep_max_seconds", 1.15) or 1.15), 3
        ),
        "story_extension_max_pause_seconds": round(
            float(cfg.get("story_extension_max_pause_seconds", 1.15) or 1.15), 3
        ),
        "silence_thresh_db": round(
            float(cfg.get("silence_thresh_db", -40.0) or -40.0), 3
        ),
        "min_non_silent_event_energy": round(
            float(cfg.get("min_non_silent_event_energy", 0.16) or 0.16), 3
        ),
        "pause_soft_keep_min_energy": round(
            float(cfg.get("pause_soft_keep_min_energy", 0.11) or 0.11), 3
        ),
        "pause_story_keep_min_energy": round(
            float(cfg.get("pause_story_keep_min_energy", 0.18) or 0.18), 3
        ),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _audio_summary_cache_paths(
    video_path: str, start: float, end: float, cfg: dict
) -> tuple[Path, Path, Path]:
    cache_dir = _audio_cache_dir(video_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    episode_wav = cache_dir / "episode.wav"
    digest = _audio_summary_cache_key(start, end, cfg)
    segment_wav = cache_dir / f"segment_{digest}.wav"
    summary_json = cache_dir / f"summary_{digest}.json"
    return episode_wav, segment_wav, summary_json


def _write_wav_segment(
    source_wav: str, start: float, end: float, target_wav: str
) -> bool:
    try:
        with contextlib.closing(wave.open(source_wav, "rb")) as src:
            sr = int(src.getframerate() or 16000)
            nch = int(src.getnchannels() or 1)
            sampwidth = int(src.getsampwidth() or 2)
            total_frames = int(src.getnframes() or 0)
            start_frame = max(0, min(total_frames, int(max(0.0, float(start)) * sr)))
            end_frame = max(
                start_frame, min(total_frames, int(max(0.0, float(end)) * sr))
            )
            src.setpos(start_frame)
            frames = src.readframes(max(0, end_frame - start_frame))
        with contextlib.closing(wave.open(target_wav, "wb")) as dst:
            dst.setnchannels(nch)
            dst.setsampwidth(sampwidth)
            dst.setframerate(sr)
            dst.writeframes(frames)
        return os.path.exists(target_wav) and os.path.getsize(target_wav) > 0
    except Exception:
        return False


class Pipeline:
    def __init__(self, cfg):
        self.cfg = normalize_config(cfg)
        self._pipeline_identity = build_pipeline_identity(
            self.cfg, Path(__file__).resolve().parents[1]
        )
        self.pipeline_version = self._pipeline_identity["pipeline_version"]
        self.config_hash = self._pipeline_identity["config_hash"]
        self.git_commit = self._pipeline_identity["git_commit"]
        self._last_selection_stats = {}
        self._watchdog_stats = {}
        self._episode_audio_cache = {}
        self._audio_summary_cache = {}
        self._audio_cache_stats = {
            "episode_audio_cache_hits": 0,
            "episode_audio_cache_misses": 0,
            "audio_summary_cache_hits": 0,
            "audio_summary_cache_misses": 0,
        }
        self._visual_precheck_cache = {}

    def _reset_watchdog_stats(self):
        self._watchdog_stats = {
            "ranking_timeouts": 0,
            "ranking_fallback_used": 0,
            "ranking_fast_fallback_used": 0,
            "ranking_failed": 0,
            "semantic_preview_timeouts": 0,
            "semantic_preview_fallback_used": 0,
            "slow_stage_events": 0,
            "hard_timeouts": 0,
            "deferred_candidates": 0,
            "skipped_due_to_timeout": 0,
            "watchdog_fallback_used": 0,
            "_slow_stage_markers": set(),
        }

    def _heartbeat_callback(self, progress_callback, stage: str, label: str):
        def _callback(elapsed_seconds: float):
            _emit(progress_callback, stage, f"{label}... {elapsed_seconds:.0f}s")
            if elapsed_seconds >= 45:
                marker = f"{stage}:{label}"
                markers = self._watchdog_stats.setdefault("_slow_stage_markers", set())
                if marker not in markers:
                    markers.add(marker)
                    self._watchdog_stats["slow_stage_events"] = (
                        self._watchdog_stats.get("slow_stage_events", 0) + 1
                    )
                    _emit(
                        progress_callback,
                        "warning",
                        f"slow_stage_detected stage={stage} elapsed={elapsed_seconds:.0f}s",
                    )

        return _callback

    def _ranking_visual_precheck(self, video_path: str, candidate: dict) -> dict:
        if not bool(self.cfg.get("ranking_visual_precheck_enabled", True)):
            return candidate
        start = float(candidate.get("start", 0.0) or 0.0)
        end = float(candidate.get("end", start) or start)
        if end <= start:
            return candidate
        sample_seconds = float(
            self.cfg.get("ranking_visual_precheck_seconds", 8.0) or 8.0
        )
        sample_end = min(end, start + max(3.0, sample_seconds))
        profile = str(self.cfg.get("active_speaker_scan_profile", "light") or "light")
        if profile.lower() == "episode_light":
            profile = "light"
        key = (str(video_path), round(start, 3), round(sample_end, 3), profile)
        stats = self._visual_precheck_cache.get(key)
        if stats is None:
            try:
                stats = sample_face_focus_stats(
                    video_path,
                    start,
                    sample_end,
                    sample_fps=float(
                        self.cfg.get("ranking_visual_precheck_fps", 1.0) or 1.0
                    ),
                    detector_profile=profile,
                )
            except Exception:
                stats = {}
            self._visual_precheck_cache[key] = dict(stats or {})
        if not stats:
            return candidate
        candidate = dict(candidate)
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        face_presence = float(stats.get("face_presence", 0.0) or 0.0)
        person_presence = float(stats.get("person_presence", 0.0) or 0.0)
        subject_presence = float(
            stats.get("subject_presence", max(face_presence, person_presence * 0.88))
            or 0.0
        )
        avg_face_size = float(stats.get("avg_face_size", 0.0) or 0.0)
        avg_person_size = float(stats.get("avg_person_size", 0.0) or 0.0)
        face_evidence_score = max(
            float(breakdown.get("face_evidence_score", 0.0) or 0.0),
            min(
                1.0,
                face_presence * 0.62 + person_presence * 0.22 + subject_presence * 0.16,
            ),
        )
        visual_subject_score = max(
            float(breakdown.get("visual_subject_score", 0.0) or 0.0),
            min(
                1.0,
                face_presence * 0.56
                + person_presence * 0.28
                + min(1.0, avg_face_size / 0.035) * 0.10
                + min(1.0, avg_person_size / 0.09) * 0.06,
            ),
        )
        audio_energy = float(breakdown.get("audio_energy", 0.0) or 0.0)
        reframe_feasibility_score = max(
            float(breakdown.get("reframe_feasibility_score", 0.0) or 0.0),
            min(
                1.0,
                subject_presence * 0.52
                + visual_subject_score * 0.24
                + min(1.0, audio_energy / 0.40) * 0.10,
            ),
        )
        empty_frame_risk = min(
            float(breakdown.get("empty_frame_risk", 1.0) or 1.0),
            max(0.0, 1.0 - (subject_presence * 0.9 + visual_subject_score * 0.45)),
        )
        premise_scores = self._premise_signal_scores(
            faces={
                "face_presence": face_presence,
                "person_presence": person_presence,
                "subject_presence": subject_presence,
                "avg_face_size": avg_face_size,
                "avg_person_size": avg_person_size,
            },
            video={
                "motion": float(breakdown.get("motion", 0.0) or 0.0),
                "brightness": float(breakdown.get("brightness", 0.0) or 0.0),
            },
            summary={
                "silence_ratio": float(breakdown.get("silence_ratio", 0.0) or 0.0),
                "audio_energy": audio_energy,
            },
            hook_score=float(breakdown.get("hook_score", 0.0) or 0.0),
            story_context_score=float(breakdown.get("story_context_score", 0.0) or 0.0),
            curiosity_gap_score=float(breakdown.get("curiosity_gap_score", 0.0) or 0.0),
            payoff_strength=float(
                breakdown.get("payoff_strength", breakdown.get("closure_score", 0.0))
                or 0.0
            ),
            cold_open_dead_time_penalty=float(
                breakdown.get("cold_open_dead_time_penalty", 0.0) or 0.0
            ),
            subtitle_quality_score=float(
                breakdown.get("subtitle_quality_score", 0.0) or 0.0
            ),
            visual_subject_score=visual_subject_score,
            reframe_feasibility_score=reframe_feasibility_score,
            empty_frame_risk=empty_frame_risk,
        )
        breakdown.update(
            {
                "face_presence": round(
                    max(
                        float(breakdown.get("face_presence", 0.0) or 0.0), face_presence
                    ),
                    4,
                ),
                "person_presence": round(
                    max(
                        float(breakdown.get("person_presence", 0.0) or 0.0),
                        person_presence,
                    ),
                    4,
                ),
                "subject_presence": round(
                    max(
                        float(breakdown.get("subject_presence", 0.0) or 0.0),
                        subject_presence,
                    ),
                    4,
                ),
                "avg_face_size": round(
                    max(
                        float(breakdown.get("avg_face_size", 0.0) or 0.0), avg_face_size
                    ),
                    4,
                ),
                "avg_person_size": round(
                    max(
                        float(breakdown.get("avg_person_size", 0.0) or 0.0),
                        avg_person_size,
                    ),
                    4,
                ),
                "face_evidence_score": round(face_evidence_score, 4),
                "visual_subject_score": round(visual_subject_score, 4),
                "reframe_feasibility_score": round(reframe_feasibility_score, 4),
                "empty_frame_risk": round(empty_frame_risk, 4),
                "subject_detector_pass": profile,
                "ranking_visual_precheck_used": True,
            }
        )
        for key_name, value in premise_scores.items():
            breakdown[key_name] = round(
                max(float(breakdown.get(key_name, 0.0) or 0.0), float(value or 0.0)), 4
            )
        candidate["score_breakdown"] = breakdown
        return candidate

    def _probe_final_crop_visual(
        self, crop_path: str, candidate: dict, reframe_debug: dict
    ) -> dict:
        if not bool(self.cfg.get("final_crop_visual_probe_enabled", True)):
            return dict(reframe_debug or {})
        if not crop_path or not os.path.exists(crop_path):
            return dict(reframe_debug or {})
        ok, duration = probe_video(crop_path)
        if not ok or duration <= 0.0:
            return dict(reframe_debug or {})
        debug = dict(reframe_debug or {})
        sample_seconds = float(
            self.cfg.get("final_crop_visual_probe_seconds", 8.0) or 8.0
        )
        profile = str(self.cfg.get("active_speaker_scan_profile", "light") or "light")
        if profile.lower() == "episode_light":
            profile = "light"
        probe_len = max(1.8, min(float(duration), max(2.0, sample_seconds * 0.45)))
        probe_ranges = []
        if duration <= probe_len * 1.5:
            probe_ranges.append((0.0, min(float(duration), probe_len)))
        else:
            mid = max(probe_len * 0.5, float(duration) * 0.5)
            end_start = max(0.0, float(duration) - probe_len)
            probe_ranges.extend(
                [
                    (0.0, min(float(duration), probe_len)),
                    (
                        max(0.0, mid - probe_len * 0.5),
                        min(float(duration), mid + probe_len * 0.5),
                    ),
                    (end_start, float(duration)),
                ]
            )
        collected = []
        for sample_start, sample_end in probe_ranges:
            try:
                stats = sample_face_focus_stats(
                    crop_path,
                    float(sample_start),
                    float(sample_end),
                    sample_fps=float(
                        self.cfg.get("final_crop_visual_probe_fps", 1.0) or 1.0
                    ),
                    detector_profile=profile,
                )
            except Exception:
                stats = {}
            if stats:
                collected.append(dict(stats))
        if not collected:
            return debug
        face_presence_values = [
            float(item.get("face_presence", 0.0) or 0.0) for item in collected
        ]
        person_presence_values = [
            float(item.get("person_presence", 0.0) or 0.0) for item in collected
        ]
        subject_presence_values = [
            float(
                item.get(
                    "subject_presence",
                    max(
                        float(item.get("face_presence", 0.0) or 0.0),
                        float(item.get("person_presence", 0.0) or 0.0) * 0.88,
                    ),
                )
                or 0.0
            )
            for item in collected
        ]
        face_presence = max(face_presence_values) if face_presence_values else 0.0
        person_presence = max(person_presence_values) if person_presence_values else 0.0
        subject_presence = (
            max(subject_presence_values) if subject_presence_values else 0.0
        )
        face_presence_min = min(face_presence_values) if face_presence_values else 0.0
        person_presence_min = (
            min(person_presence_values) if person_presence_values else 0.0
        )
        subject_presence_min = (
            min(subject_presence_values) if subject_presence_values else 0.0
        )
        face_presence_avg = float(
            sum(face_presence_values) / max(1, len(face_presence_values))
        )
        person_presence_avg = float(
            sum(person_presence_values) / max(1, len(person_presence_values))
        )
        subject_presence_avg = float(
            sum(subject_presence_values) / max(1, len(subject_presence_values))
        )
        visual_presence = max(face_presence, person_presence, subject_presence)
        stability_ratio = float(
            sum(1 for value in subject_presence_values if value >= 0.08)
            / max(1, len(subject_presence_values))
        )
        debug["final_crop_visual_probe_used"] = True
        debug["final_crop_face_presence"] = round(face_presence, 4)
        debug["final_crop_person_presence"] = round(person_presence, 4)
        debug["final_crop_subject_presence"] = round(subject_presence, 4)
        debug["final_crop_face_presence_min"] = round(face_presence_min, 4)
        debug["final_crop_person_presence_min"] = round(person_presence_min, 4)
        debug["final_crop_subject_presence_min"] = round(subject_presence_min, 4)
        debug["final_crop_face_presence_avg"] = round(face_presence_avg, 4)
        debug["final_crop_person_presence_avg"] = round(person_presence_avg, 4)
        debug["final_crop_subject_presence_avg"] = round(subject_presence_avg, 4)
        debug["final_crop_visual_stability_ratio"] = round(stability_ratio, 4)
        first_stats = collected[0]
        debug["final_crop_avg_face_size"] = float(
            first_stats.get("avg_face_size", 0.0) or 0.0
        )
        debug["final_crop_subject_detector_pass"] = str(
            first_stats.get("subject_detector_pass", profile) or profile
        )
        if visual_presence > 0.0:
            debug["evidence_visible_faces_peak"] = max(
                int(debug.get("evidence_visible_faces_peak", 0) or 0),
                1 if face_presence >= 0.08 else 0,
            )
            debug["evidence_visible_persons_peak"] = max(
                int(debug.get("evidence_visible_persons_peak", 0) or 0),
                1 if person_presence >= 0.08 else 0,
            )
            debug["subject_visibility_ratio"] = max(
                float(debug.get("subject_visibility_ratio", 0.0) or 0.0),
                subject_presence,
            )
            if face_presence_avg >= 0.18 and face_presence_min >= 0.08:
                debug["speaker_face_centered_windows"] = max(
                    int(debug.get("speaker_face_centered_windows", 0) or 0), 1
                )
                debug["speaker_centered_rate"] = max(
                    float(debug.get("speaker_centered_rate", 0.0) or 0.0),
                    min(1.0, face_presence_avg),
                )
        return debug

    def _output_dir(self, video_path: str):
        root = str(self.cfg.get("output_root", "") or "").strip()
        name = Path(video_path).stem + "_shorts"
        if root:
            os.makedirs(root, exist_ok=True)
            return os.path.join(root, name)
        return os.path.splitext(video_path)[0] + "_shorts"

    def _clean_test_mode_outputs(self, out_dir: str):
        for pattern in (
            "short_*.mp4",
            "short_*.json",
            "cand_*.wav",
            "cand_*.srt",
            "cand_*_crop.mp4",
        ):
            for path in Path(out_dir).glob(pattern):
                with contextlib.suppress(Exception):
                    path.unlink()

    def _ensure_episode_audio_wav(self, video_path: str) -> str | None:
        cache_key = _audio_cache_key(video_path)
        cached = self._episode_audio_cache.get(cache_key)
        if cached and os.path.exists(cached):
            self._audio_cache_stats["episode_audio_cache_hits"] = (
                self._audio_cache_stats.get("episode_audio_cache_hits", 0) + 1
            )
            return cached
        self._audio_cache_stats["episode_audio_cache_misses"] = (
            self._audio_cache_stats.get("episode_audio_cache_misses", 0) + 1
        )
        episode_wav = _audio_cache_dir(video_path) / "episode.wav"
        if not episode_wav.exists() or episode_wav.stat().st_size <= 0:
            try:
                extract_audio_to_wav(video_path, str(episode_wav))
            except Exception:
                return None
        if episode_wav.exists() and episode_wav.stat().st_size > 0:
            self._episode_audio_cache[cache_key] = str(episode_wav)
            return str(episode_wav)
        return None

    def _load_cached_audio_summary(self, summary_json: Path) -> dict | None:
        try:
            if not summary_json.exists():
                return None
            self._audio_cache_stats["audio_summary_cache_hits"] = (
                self._audio_cache_stats.get("audio_summary_cache_hits", 0) + 1
            )
            payload = json.loads(summary_json.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            for key in ("voiced_intervals", "turns"):
                if isinstance(payload.get(key), list):
                    payload[key] = [
                        tuple(item)
                        for item in payload[key]
                        if isinstance(item, (list, tuple)) and len(item) >= 2
                    ]
            if isinstance(payload.get("pause_timeline"), list):
                payload["pause_timeline"] = [
                    dict(item)
                    for item in payload["pause_timeline"]
                    if isinstance(item, dict)
                ]
            payload["cached"] = True
            return payload
        except Exception:
            return None

    def _store_cached_audio_summary(self, summary_json: Path, summary: dict) -> None:
        try:
            self._audio_cache_stats["audio_summary_cache_misses"] = (
                self._audio_cache_stats.get("audio_summary_cache_misses", 0) + 1
            )
            payload = dict(summary)
            payload.pop("cached", None)
            summary_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _candidate_cfg(self, candidate=None, stage="default"):
        cfg = dict(self.cfg)
        score_breakdown = dict((candidate or {}).get("score_breakdown", {}) or {})
        candidate_duration = float(
            (candidate or {}).get("duration", score_breakdown.get("duration", 0.0))
            or 0.0
        )
        clarity = float(score_breakdown.get("story_clarity_score", 0.0) or 0.0)
        story_interest = float(score_breakdown.get("story_interest_score", 0.0) or 0.0)
        story_completeness = float(
            score_breakdown.get("story_completeness_score", 0.0) or 0.0
        )
        visual_subject = float(score_breakdown.get("visual_subject_score", 0.0) or 0.0)
        hook = float(score_breakdown.get("hook_score", 0.0) or 0.0)
        dialogue = float(score_breakdown.get("dialogue_exchange_score", 0.0) or 0.0)
        duration_policy = (
            self._candidate_duration_policy(candidate)
            if candidate
            else {
                "band": "hook_first_short",
                "target_seconds": float(cfg.get("target_story_seconds", 45)),
                "soft_max_seconds": float(cfg.get("story_soft_max_seconds", 45)),
                "hard_max_seconds": min(
                    float(cfg.get("story_soft_max_seconds", 45)),
                    float(cfg.get("max_short_seconds", 60)),
                ),
                "extension_reason": "hook_first_default",
                "exceptional_duration_used": False,
                "strong_duration_used": False,
            }
        )
        cfg["target_story_seconds"] = float(
            duration_policy.get("target_seconds", cfg.get("target_story_seconds", 45))
        )
        cfg["story_soft_max_seconds"] = float(
            duration_policy.get(
                "soft_max_seconds", cfg.get("story_soft_max_seconds", 45)
            )
        )
        cfg["story_hard_max_seconds"] = float(
            duration_policy.get(
                "hard_max_seconds", cfg.get("story_hard_max_seconds", 60)
            )
        )
        cfg["story_duration_band"] = str(
            duration_policy.get("band", "hook_first_short")
        )
        cfg["story_duration_extension_reason"] = str(
            duration_policy.get("extension_reason", "hook_first_default")
        )
        cfg["story_duration_exceptional_used"] = bool(
            duration_policy.get("exceptional_duration_used", False)
        )
        cfg["story_mode_active"] = str(
            duration_policy.get("story_mode", self._story_mode())
        )
        cfg["story_min_publishable_seconds_active"] = float(
            duration_policy.get(
                "min_publishable_seconds", cfg.get("min_publishable_seconds", 35)
            )
        )
        cfg["tension_context_score_active"] = float(
            duration_policy.get("tension_context_score", 0.0) or 0.0
        )
        if str(cfg.get("story_mode_active", "standard")) == "tension":
            cfg["story_pause_cut_threshold_seconds"] = float(
                duration_policy.get(
                    "pause_cut_threshold_seconds",
                    cfg.get(
                        "tension_pause_cut_threshold_seconds",
                        cfg.get("story_pause_cut_threshold_seconds", 1.0),
                    ),
                )
            )
            cfg["story_pause_keep_max_seconds"] = float(
                duration_policy.get(
                    "pause_keep_max_seconds",
                    cfg.get(
                        "tension_pause_keep_max_seconds",
                        cfg.get("story_pause_keep_max_seconds", 1.15),
                    ),
                )
            )
            cfg["dialogue_compact_lead_pad_seconds"] = float(
                cfg.get(
                    "tension_dialogue_compact_lead_pad_seconds",
                    cfg.get("dialogue_compact_lead_pad_seconds", 0.08),
                )
            )
            cfg["dialogue_compact_tail_pad_seconds"] = float(
                cfg.get(
                    "tension_dialogue_compact_tail_pad_seconds",
                    cfg.get("dialogue_compact_tail_pad_seconds", 0.12),
                )
            )
        quality_mode = str(
            cfg.get("quality_mode", cfg.get("quality_governor_mode", "auto")) or "auto"
        ).lower()
        escalation = bool(cfg.get("local_quality_escalation", True))
        mode = str(
            cfg.get("subtitle_processing_mode", "balanced_local") or "balanced_local"
        )
        if stage == "subtitle":
            if quality_mode == "max_quality":
                cfg["subtitle_processing_mode"] = "enhanced_local"
            elif quality_mode == "balanced":
                cfg["subtitle_processing_mode"] = "balanced_local"
            elif escalation and (clarity >= 0.72 or hook >= 0.56 or dialogue >= 0.46):
                cfg["subtitle_processing_mode"] = "enhanced_local"
            else:
                cfg["subtitle_processing_mode"] = mode
        if stage == "reframe":
            cfg["reframe_priority"] = "stability_first"
            cfg["speaker_selection_mode"] = "evidence_scored"
            cfg["speaker_lock_mode"] = "state_machine"
            cfg["empty_frame_guard_enabled"] = True
            cfg["subject_detector_pass"] = "light"
            strict_speaker_only = bool(
                cfg.get(
                    "speaker_center_strict_mode",
                    self.cfg.get("speaker_center_strict_mode", True),
                )
            )
            premise_first = (
                max(
                    float(score_breakdown.get("visual_premise_strength", 0.0) or 0.0),
                    float(score_breakdown.get("sound_off_hook_score", 0.0) or 0.0),
                    float(score_breakdown.get("first_second_hook_score", 0.0) or 0.0),
                )
                >= 0.60
            )
            cfg["reframe_allow_wide_dialogue_center"] = False
            cfg["listener_fallback_max_hold_seconds"] = min(
                float(cfg.get("listener_fallback_max_hold_seconds", 0.35)), 0.35
            )
            cfg["listener_fallback_speech_hold_max_seconds"] = min(
                float(cfg.get("listener_fallback_speech_hold_max_seconds", 0.22)), 0.22
            )
            cfg["dialogue_center_use_threshold"] = max(
                float(cfg.get("dialogue_center_use_threshold", 0.82)), 0.82
            )
            cfg["dialogue_center_min_likelihood"] = max(
                float(cfg.get("dialogue_center_min_likelihood", 0.78)), 0.78
            )
            if (
                str(cfg.get("active_speaker_mode", "hybrid_subject_first"))
                == "hybrid_subject_first"
                and bool(cfg.get("subject_detector_final_pass_enabled", True))
                and (
                    quality_mode == "max_quality"
                    or (
                        escalation
                        and (
                            clarity >= 0.68
                            or story_interest >= 0.64
                            or story_completeness >= 0.60
                            or visual_subject >= 0.42
                            or dialogue >= 0.42
                        )
                    )
                )
            ):
                cfg["subject_detector_pass"] = str(
                    cfg.get("active_speaker_refine_profile", "final_clip_strong")
                    or "final_clip_strong"
                )
                cfg["face_detection_fps"] = max(
                    int(cfg.get("face_detection_fps", 3)), 4
                )
                cfg["crop_window_sec"] = min(
                    float(cfg.get("crop_window_sec", 0.8)), 0.72
                )
                cfg["reframe_track_count_limit"] = max(
                    int(cfg.get("reframe_track_count_limit", 3)), 4
                )
            else:
                cfg["subject_detector_pass"] = str(
                    cfg.get("active_speaker_scan_profile", "light") or "light"
                )
            if candidate_duration >= 24.0:
                cfg["crop_window_sec"] = max(
                    1.10, min(float(cfg.get("crop_window_sec", 0.8)) * 1.35, 1.30)
                )
                cfg["face_detection_fps"] = min(
                    int(cfg.get("face_detection_fps", 3)), 3
                )
                cfg["reframe_track_count_limit"] = max(
                    3, min(int(cfg.get("reframe_track_count_limit", 3)), 3)
                )
                cfg["speaker_switch_hold_windows"] = 0 if strict_speaker_only else 1
            if candidate_duration >= 38.0:
                cfg["crop_window_sec"] = max(
                    float(cfg.get("crop_window_sec", 0.8)), 1.20
                )
                cfg["face_detection_fps"] = min(
                    int(cfg.get("face_detection_fps", 3)), 2
                )
                cfg["speaker_switch_hold_windows"] = 0 if strict_speaker_only else 1
            if quality_mode == "max_quality":
                cfg["reframe_transition_mode"] = "smooth"
                cfg["reframe_anchor_mode"] = "stable_primary"
            if premise_first:
                cfg["framing_mode"] = (
                    "scene_lock" if visual_subject < 0.24 else "face_locked"
                )
                cfg["reframe_anchor_mode"] = "stable_primary"
            if str(cfg.get("story_mode_active", "standard")) == "tension":
                cfg["reframe_allow_wide_dialogue_center"] = False
                cfg["listener_fallback_max_hold_seconds"] = min(
                    float(cfg.get("listener_fallback_max_hold_seconds", 0.35)), 0.28
                )
                cfg["listener_fallback_speech_hold_max_seconds"] = min(
                    float(cfg.get("listener_fallback_speech_hold_max_seconds", 0.22)),
                    0.18,
                )
                cfg["dialogue_center_use_threshold"] = max(
                    float(cfg.get("dialogue_center_use_threshold", 0.82)), 0.84
                )
                cfg["dialogue_center_min_likelihood"] = max(
                    float(cfg.get("dialogue_center_min_likelihood", 0.78)), 0.80
                )
        return normalize_config(cfg)

    def _premise_signal_scores(
        self,
        *,
        faces: dict,
        video: dict,
        summary: dict,
        hook_score: float,
        story_context_score: float,
        curiosity_gap_score: float,
        payoff_strength: float,
        cold_open_dead_time_penalty: float,
        subtitle_quality_score: float,
        visual_subject_score: float | None = None,
        reframe_feasibility_score: float | None = None,
        empty_frame_risk: float | None = None,
    ) -> dict:
        source_face_presence = float(faces.get("face_presence", 0.0) or 0.0)
        source_person_presence = float(faces.get("person_presence", 0.0) or 0.0)
        source_subject_presence = float(
            faces.get("subject_presence", source_face_presence) or 0.0
        )
        avg_face_size = float(faces.get("avg_face_size", 0.0) or 0.0)
        avg_person_size = float(faces.get("avg_person_size", 0.0) or 0.0)
        motion = float(video.get("motion", 0.0) or 0.0)
        brightness = float(video.get("brightness", 0.0) or 0.0)
        silence_ratio = float(summary.get("silence_ratio", 0.0) or 0.0)
        audio_energy = float(summary.get("audio_energy", 0.0) or 0.0)
        if visual_subject_score is None:
            visual_subject_score = min(
                1.0,
                source_face_presence * 0.56
                + source_person_presence * 0.28
                + min(1.0, avg_face_size / 0.035) * 0.10
                + min(1.0, avg_person_size / 0.09) * 0.06,
            )
        if reframe_feasibility_score is None:
            reframe_feasibility_score = min(
                1.0,
                source_subject_presence * 0.52
                + visual_subject_score * 0.24
                + min(1.0, motion / 0.18) * 0.08
                + min(1.0, brightness / 0.18) * 0.06
                + min(1.0, audio_energy / 0.40) * 0.10,
            )
        if empty_frame_risk is None:
            empty_frame_risk = max(
                0.0, 1.0 - (source_subject_presence * 0.9 + visual_subject_score * 0.45)
            )
        visual_premise_strength = max(
            0.0,
            min(
                1.0,
                visual_subject_score * 0.42
                + reframe_feasibility_score * 0.24
                + max(0.0, 1.0 - empty_frame_risk) * 0.18
                + min(1.0, source_subject_presence) * 0.08
                + min(1.0, source_face_presence) * 0.08,
            ),
        )
        first_second_hook_score = max(
            0.0,
            min(
                1.0,
                hook_score * 0.38
                + visual_premise_strength * 0.28
                + story_context_score * 0.12
                + max(0.0, 1.0 - silence_ratio) * 0.10
                + max(0.0, 1.0 - cold_open_dead_time_penalty) * 0.14,
            ),
        )
        sound_off_hook_score = max(
            0.0,
            min(
                1.0,
                visual_premise_strength * 0.52
                + first_second_hook_score * 0.18
                + hook_score * 0.10
                + curiosity_gap_score * 0.10
                + payoff_strength * 0.06
                + max(0.0, 1.0 - cold_open_dead_time_penalty) * 0.04,
            ),
        )
        premise_signal_score = max(
            0.0,
            min(
                1.0,
                visual_premise_strength * 0.34
                + first_second_hook_score * 0.33
                + sound_off_hook_score * 0.33,
            ),
        )
        return {
            "visual_subject_score": round(float(visual_subject_score), 4),
            "reframe_feasibility_score": round(float(reframe_feasibility_score), 4),
            "empty_frame_risk": round(float(empty_frame_risk), 4),
            "visual_premise_strength": round(visual_premise_strength, 4),
            "first_second_hook_score": round(first_second_hook_score, 4),
            "sound_off_hook_score": round(sound_off_hook_score, 4),
            "premise_signal_score": round(premise_signal_score, 4),
        }

    def _select_framing_plan(
        self,
        candidate: dict,
        subtitle_turns: int,
        subtitle_signals: dict,
        reframe_cfg: dict,
        direct_candidate_mode: bool = False,
    ):
        framing_mode = str(
            reframe_cfg.get("framing_mode", "face_locked") or "face_locked"
        ).lower()
        anchor_mode = str(
            reframe_cfg.get("reframe_anchor_mode", "stable_primary") or "stable_primary"
        )
        if framing_mode in {
            "tight_crop",
            "context_padded",
            "wide_subject",
            "face_locked",
            "shot_lock",
            "scene_lock",
            "human_handoff",
        }:
            breakdown = dict(candidate.get("score_breakdown", {}) or {})
            dialogue_exchange = float(
                subtitle_signals.get("dialogue_exchange_score", 0.0) or 0.0
            )
            dialogue_scene = float(
                subtitle_signals.get("dialogue_scene_likelihood", 0.0) or 0.0
            )
            visual_subject = float(breakdown.get("visual_subject_score", 0.0) or 0.0)
            subject_presence = max(
                float(breakdown.get("subject_presence", 0.0) or 0.0),
                float(breakdown.get("face_presence", 0.0) or 0.0),
                float(breakdown.get("person_presence", 0.0) or 0.0),
            )
            visual_premise = float(breakdown.get("visual_premise_strength", 0.0) or 0.0)
            first_second_hook = float(
                breakdown.get("first_second_hook_score", 0.0) or 0.0
            )
            sound_off_hook = float(breakdown.get("sound_off_hook_score", 0.0) or 0.0)
            visible_stakes = float(breakdown.get("visible_stakes_score", 0.0) or 0.0)
            cold_open_penalty = float(
                breakdown.get("cold_open_dead_time_penalty", 0.0) or 0.0
            )
            story_unit_type = str(
                breakdown.get(
                    "story_unit_type",
                    candidate.get("story_unit_type", "dialogue_cluster"),
                )
                or "dialogue_cluster"
            ).lower()
            story_mode_active = str(
                reframe_cfg.get("story_mode_active", "standard") or "standard"
            ).lower()
            turn_boost = 0.14 if direct_candidate_mode else 0.0
            face_scene = subject_presence >= 0.24 and visual_subject >= 0.24
            strong_visual_hook = (
                max(visual_premise, sound_off_hook, first_second_hook, visible_stakes)
                >= 0.62
                and cold_open_penalty <= 0.0
            )
            stakes_scene = (
                story_unit_type
                in {
                    "rescue_urgency",
                    "reveal_discovery",
                    "investigation_clue",
                    "danger_escape",
                }
                or visible_stakes >= 0.64
                or (
                    visual_premise >= 0.60
                    and sound_off_hook >= 0.60
                    and dialogue_exchange < 0.42
                )
            )
            dialogue_ready = (
                bool(reframe_cfg.get("reframe_allow_wide_dialogue_center", True))
                and visual_subject >= 0.22
                and (
                    dialogue_exchange >= 0.46
                    or dialogue_scene >= 0.42
                    or (subtitle_turns >= 4 and dialogue_exchange >= 0.30 + turn_boost)
                )
            )
            if strong_visual_hook and stakes_scene:
                framing_mode = "scene_lock"
                anchor_mode = "stable_primary"
                if (
                    story_mode_active == "tension"
                    and bool(
                        reframe_cfg.get("tension_square_canvas_conflict_only", True)
                    )
                    and dialogue_scene >= 0.42
                    and subject_presence < 0.24
                ):
                    framing_mode = "square_canvas"
            elif face_scene:
                framing_mode = "face_locked"
                anchor_mode = "stable_primary"
            elif dialogue_ready and not strong_visual_hook and subject_presence < 0.22:
                framing_mode = "dialogue_dual"
                anchor_mode = "dialogue_center"
        return framing_mode, anchor_mode

    def _transcribe_with_auto_quality(
        self, wav_path: str, out_dir: str, idx: int, candidate=None
    ):
        candidate_cfg = self._candidate_cfg(candidate, stage="subtitle")
        subtitle_info = transcribe_segment(wav_path, out_dir, idx, cfg=candidate_cfg)
        confidence = float(subtitle_info.get("confidence", 0.0) or 0.0)
        signals = dict(subtitle_info.get("signals", {}) or {})
        text_sanity = float(signals.get("subtitle_text_sanity_score", 1.0) or 0.0)
        starts_mid_phrase_risk = bool(signals.get("starts_mid_phrase", False))
        visual_drop_count = int(signals.get("subtitle_visual_drop_count", 0) or 0)
        phrase_clear_count = int(signals.get("subtitle_phrase_clear_count", 0) or 0)
        hold_too_long = bool(
            (signals.get("subtitle_visible_block_stats") or {}).get(
                "subtitle_hold_too_long", signals.get("subtitle_hold_too_long", False)
            )
        )
        quality_first = self._quality_profile() == "quality_first"
        should_retry = str(
            candidate_cfg.get("subtitle_processing_mode", "balanced_local")
        ) != "enhanced_local" and (
            confidence < 0.42
            or text_sanity
            < float(candidate_cfg.get("subtitle_text_sanity_threshold", 0.62))
            or (quality_first and starts_mid_phrase_risk and confidence < 0.72)
            or visual_drop_count > 0
            or phrase_clear_count > 0
            or hold_too_long
        )
        if should_retry:
            retry_cfg = dict(candidate_cfg)
            retry_cfg["subtitle_processing_mode"] = "enhanced_local"
            retry = transcribe_segment(wav_path, out_dir, idx, cfg=retry_cfg)
            retry_signals = dict(retry.get("signals", {}) or {})
            retry_conf = float(retry.get("confidence", 0.0) or 0.0)
            retry_sanity = float(
                retry_signals.get("subtitle_text_sanity_score", 0.0) or 0.0
            )
            if retry_conf >= confidence or retry_sanity >= text_sanity:
                subtitle_info = retry
                subtitle_info["auto_quality_retry_used"] = True
            else:
                subtitle_info["auto_quality_retry_used"] = False
        else:
            subtitle_info["auto_quality_retry_used"] = False
        return subtitle_info

    def _semantic_preview_single(self, video_path: str, candidate: dict):
        preview_profile = str(self.cfg.get("transcription_profile", "balanced")).lower()
        temp_dir = tempfile.mkdtemp(prefix="sf_preview_")
        try:
            wav_path = os.path.join(temp_dir, "preview.wav")
            rc, _, _ = run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    video_path,
                    "-ss",
                    str(candidate["start"]),
                    "-to",
                    str(candidate["end"]),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-vn",
                    wav_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                ],
                timeout=180,
            )
            if rc != 0 or not os.path.exists(wav_path):
                return candidate
            preview_cfg = dict(self.cfg)
            preview_cfg["transcription_profile"] = (
                "balanced" if preview_profile == "quality" else preview_profile
            )
            preview_cfg["subtitle_display_mode"] = "sentence_highlight"
            subtitle_info = transcribe_segment(wav_path, temp_dir, 0, cfg=preview_cfg)
            signals = dict(subtitle_info.get("signals") or {})
            updated = dict(candidate)
            updated["score_breakdown"] = dict(updated.get("score_breakdown", {}))
            updated["score_breakdown"].update(
                {
                    "hook_score": round(
                        float(
                            signals.get(
                                "hook_score",
                                updated["score_breakdown"].get("hook_score", 0.0),
                            )
                        ),
                        4,
                    ),
                    "hook_strength": round(
                        float(
                            signals.get(
                                "hook_score",
                                updated["score_breakdown"].get(
                                    "hook_strength",
                                    updated["score_breakdown"].get("hook_score", 0.0),
                                ),
                            )
                        ),
                        4,
                    ),
                    "development_score": round(
                        float(
                            signals.get(
                                "development_score",
                                updated["score_breakdown"].get(
                                    "development_score", 0.0
                                ),
                            )
                        ),
                        4,
                    ),
                    "closure_score": round(
                        float(
                            signals.get(
                                "closure_score",
                                updated["score_breakdown"].get("closure_score", 0.0),
                            )
                        ),
                        4,
                    ),
                    "payoff_strength": round(
                        float(
                            signals.get(
                                "closure_score",
                                updated["score_breakdown"].get(
                                    "payoff_strength",
                                    updated["score_breakdown"].get(
                                        "closure_score", 0.0
                                    ),
                                ),
                            )
                        ),
                        4,
                    ),
                    "dialogue_exchange_score": round(
                        float(signals.get("dialogue_exchange_score", 0.0)), 4
                    ),
                    "preview_interestingness_score": round(
                        float(signals.get("interestingness_score", 0.0)), 4
                    ),
                    "subtitle_confidence": round(
                        float(
                            signals.get(
                                "subtitle_confidence",
                                subtitle_info.get("confidence", 0.0),
                            )
                            or 0.0
                        ),
                        4,
                    ),
                    "subtitle_text_sanity_score": round(
                        float(signals.get("subtitle_text_sanity_score", 0.0)), 4
                    ),
                    "subtitle_language_consistency": round(
                        float(signals.get("subtitle_language_consistency", 0.0)), 4
                    ),
                    "subtitle_quality_score": round(
                        float(signals.get("subtitle_quality_score", 0.0)), 4
                    ),
                    "story_interest_score": round(
                        float(
                            signals.get(
                                "interestingness_score",
                                updated["score_breakdown"].get(
                                    "story_interest_score", 0.0
                                ),
                            )
                        ),
                        4,
                    ),
                    "story_completeness_score": round(
                        max(
                            float(signals.get("closure_score", 0.0)),
                            float(
                                updated["score_breakdown"].get(
                                    "story_completeness_score", 0.0
                                )
                            ),
                        ),
                        4,
                    ),
                    "story_context_score": round(
                        max(
                            float(
                                updated["score_breakdown"].get(
                                    "story_context_score", 0.0
                                )
                            ),
                            float(signals.get("dialogue_exchange_score", 0.0)) * 0.42,
                        ),
                        4,
                    ),
                    "story_has_payoff": bool(signals.get("story_has_payoff", False)),
                }
            )
            updated["score_breakdown"]["curiosity_gap_score"] = round(
                max(
                    float(
                        updated["score_breakdown"].get("curiosity_gap_score", 0.0)
                        or 0.0
                    ),
                    float(updated["score_breakdown"].get("hook_strength", 0.0) or 0.0)
                    * 0.62
                    + float(
                        updated["score_breakdown"].get("dialogue_exchange_score", 0.0)
                        or 0.0
                    )
                    * 0.24
                    + float(
                        updated["score_breakdown"].get("story_context_score", 0.0)
                        or 0.0
                    )
                    * 0.14,
                ),
                4,
            )
            updated["score_breakdown"]["watchability_score"] = round(
                max(
                    float(
                        updated["score_breakdown"].get("watchability_score", 0.0) or 0.0
                    ),
                    float(updated["score_breakdown"].get("hook_strength", 0.0) or 0.0)
                    * 0.20
                    + float(
                        updated["score_breakdown"].get("payoff_strength", 0.0) or 0.0
                    )
                    * 0.18
                    + float(
                        updated["score_breakdown"].get("story_interest_score", 0.0)
                        or 0.0
                    )
                    * 0.24
                    + float(
                        updated["score_breakdown"].get("story_completeness_score", 0.0)
                        or 0.0
                    )
                    * 0.20
                    + float(
                        updated["score_breakdown"].get("story_context_score", 0.0)
                        or 0.0
                    )
                    * 0.10
                    + float(
                        updated["score_breakdown"].get("subtitle_quality_score", 0.0)
                        or 0.0
                    )
                    * 0.06
                    + float(
                        updated["score_breakdown"].get("premise_signal_score", 0.0)
                        or 0.0
                    )
                    * 0.08
                    + float(
                        updated["score_breakdown"].get("sound_off_hook_score", 0.0)
                        or 0.0
                    )
                    * 0.06
                    + float(
                        updated["score_breakdown"].get("visual_premise_strength", 0.0)
                        or 0.0
                    )
                    * 0.04,
                ),
                4,
            )
            updated["score_breakdown"]["packaging_quality_score"] = round(
                max(
                    float(
                        updated["score_breakdown"].get("packaging_quality_score", 0.0)
                        or 0.0
                    ),
                    float(updated["score_breakdown"].get("hook_strength", 0.0) or 0.0)
                    * 0.28
                    + float(
                        updated["score_breakdown"].get("payoff_strength", 0.0) or 0.0
                    )
                    * 0.18
                    + float(
                        updated["score_breakdown"].get("story_clarity_score", 0.0)
                        or 0.0
                    )
                    * 0.18
                    + float(
                        updated["score_breakdown"].get("story_interest_score", 0.0)
                        or 0.0
                    )
                    * 0.16
                    + float(
                        updated["score_breakdown"].get("subtitle_quality_score", 0.0)
                        or 0.0
                    )
                    * 0.04
                    + float(
                        updated["score_breakdown"].get("premise_signal_score", 0.0)
                        or 0.0
                    )
                    * 0.10
                    + float(
                        updated["score_breakdown"].get("first_second_hook_score", 0.0)
                        or 0.0
                    )
                    * 0.06,
                ),
                4,
            )
            updated["score_breakdown"]["recommendation_readiness_score"] = round(
                max(
                    float(
                        updated["score_breakdown"].get(
                            "recommendation_readiness_score", 0.0
                        )
                        or 0.0
                    ),
                    float(
                        updated["score_breakdown"].get("watchability_score", 0.0) or 0.0
                    )
                    * 0.28
                    + float(updated["score_breakdown"].get("hook_strength", 0.0) or 0.0)
                    * 0.14
                    + float(
                        updated["score_breakdown"].get("curiosity_gap_score", 0.0)
                        or 0.0
                    )
                    * 0.10
                    + float(
                        updated["score_breakdown"].get("payoff_strength", 0.0) or 0.0
                    )
                    * 0.12
                    + float(
                        updated["score_breakdown"].get("packaging_quality_score", 0.0)
                        or 0.0
                    )
                    * 0.08
                    + float(
                        updated["score_breakdown"].get(
                            "subtitle_text_sanity_score", 0.0
                        )
                        or 0.0
                    )
                    * 0.04
                    + float(
                        updated["score_breakdown"].get(
                            "subtitle_language_consistency", 0.0
                        )
                        or 0.0
                    )
                    * 0.04
                    + float(
                        updated["score_breakdown"].get("premise_signal_score", 0.0)
                        or 0.0
                    )
                    * 0.10
                    + float(
                        updated["score_breakdown"].get("sound_off_hook_score", 0.0)
                        or 0.0
                    )
                    * 0.08,
                ),
                4,
            )
            premise_scores = self._premise_signal_scores(
                faces={
                    "face_presence": float(
                        updated["score_breakdown"].get("face_presence", 0.0) or 0.0
                    ),
                    "person_presence": float(
                        updated["score_breakdown"].get("person_presence", 0.0) or 0.0
                    ),
                    "subject_presence": float(
                        updated["score_breakdown"].get("subject_presence", 0.0) or 0.0
                    ),
                    "avg_face_size": float(
                        updated["score_breakdown"].get("avg_face_size", 0.0) or 0.0
                    ),
                    "avg_person_size": float(
                        updated["score_breakdown"].get("avg_person_size", 0.0) or 0.0
                    ),
                },
                video={
                    "motion": float(
                        updated["score_breakdown"].get("motion", 0.0) or 0.0
                    ),
                    "brightness": float(
                        updated["score_breakdown"].get("brightness", 0.0) or 0.0
                    ),
                },
                summary={
                    "silence_ratio": float(
                        updated["score_breakdown"].get("silence_ratio", 0.0) or 0.0
                    ),
                    "audio_energy": float(
                        updated["score_breakdown"].get("audio_energy", 0.0) or 0.0
                    ),
                },
                hook_score=float(
                    updated["score_breakdown"].get(
                        "hook_strength",
                        updated["score_breakdown"].get("hook_score", 0.0),
                    )
                    or 0.0
                ),
                story_context_score=float(
                    updated["score_breakdown"].get("story_context_score", 0.0) or 0.0
                ),
                curiosity_gap_score=float(
                    updated["score_breakdown"].get("curiosity_gap_score", 0.0) or 0.0
                ),
                payoff_strength=float(
                    updated["score_breakdown"].get("payoff_strength", 0.0) or 0.0
                ),
                cold_open_dead_time_penalty=float(
                    updated["score_breakdown"].get("cold_open_dead_time_penalty", 0.0)
                    or 0.0
                ),
                subtitle_quality_score=float(
                    updated["score_breakdown"].get("subtitle_quality_score", 0.0) or 0.0
                ),
                visual_subject_score=float(
                    updated["score_breakdown"].get("visual_subject_score", 0.0) or 0.0
                ),
                reframe_feasibility_score=float(
                    updated["score_breakdown"].get("reframe_feasibility_score", 0.0)
                    or 0.0
                ),
                empty_frame_risk=float(
                    updated["score_breakdown"].get("empty_frame_risk", 0.0) or 0.0
                ),
            )
            for key in (
                "visual_premise_strength",
                "first_second_hook_score",
                "sound_off_hook_score",
                "premise_signal_score",
            ):
                updated["score_breakdown"][key] = round(
                    max(
                        float(updated["score_breakdown"].get(key, 0.0) or 0.0),
                        float(premise_scores.get(key, 0.0) or 0.0),
                    ),
                    4,
                )
            semantic_bonus = (
                float(signals.get("hook_score", 0.0)) * 0.16
                + float(signals.get("development_score", 0.0)) * 0.10
                + float(signals.get("closure_score", 0.0)) * 0.14
                + float(signals.get("interestingness_score", 0.0)) * 0.18
                + float(signals.get("dialogue_exchange_score", 0.0)) * 0.06
                + float(signals.get("subtitle_confidence", 0.0)) * 0.04
                + float(signals.get("subtitle_text_sanity_score", 0.0)) * 0.05
                + float(signals.get("subtitle_quality_score", 0.0)) * 0.06
                + float(
                    updated["score_breakdown"].get(
                        "recommendation_readiness_score", 0.0
                    )
                )
                * 0.12
                + float(updated["score_breakdown"].get("premise_signal_score", 0.0))
                * 0.08
                + float(updated["score_breakdown"].get("sound_off_hook_score", 0.0))
                * 0.05
                + float(updated["score_breakdown"].get("first_second_hook_score", 0.0))
                * 0.05
            )
            if updated.get("story_continued_after_pause") and bool(
                self.cfg.get("payoff_after_pause_bonus_enabled", True)
            ):
                semantic_bonus += 0.05
            if bool(signals.get("starts_mid_phrase")):
                semantic_bonus -= 0.10
            if not bool(signals.get("sentence_end_safe", True)):
                semantic_bonus -= 0.08
            updated["score"] = round(
                float(updated.get("score", 0.0)) + semantic_bonus, 4
            )
            return updated
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _score_story_candidate_timeout_fallback(self, candidate: dict):
        baseline = dict(candidate.get("score_breakdown", {}) or {})
        duration = max(
            0.1,
            float(candidate.get("end", 0.0) or 0.0)
            - float(candidate.get("start", 0.0) or 0.0),
        )
        duration_floor = max(35.0, float(self.cfg.get("min_publishable_seconds", 35)))
        duration_penalty = max(
            0.0, (duration_floor - duration) / max(1.0, duration_floor)
        )
        speech_density = float(
            baseline.get("speech_density", candidate.get("speech_coverage", 0.0)) or 0.0
        )
        silence_ratio = float(
            baseline.get("silence_ratio", 1.0 - min(1.0, speech_density)) or 0.0
        )
        audio_energy = float(
            baseline.get("audio_energy", min(1.0, speech_density * 0.9 + 0.1)) or 0.0
        )
        hook_score = float(
            baseline.get(
                "hook_score",
                max(
                    0.0,
                    1.0
                    - (
                        float(candidate.get("hook_gap", 0.0) or 0.0)
                        / max(0.5, float(self.cfg.get("hook_max_lead_seconds", 4.5)))
                    ),
                ),
            )
            or 0.0
        )
        development_score = float(
            baseline.get(
                "development_score",
                min(1.0, float(candidate.get("speech_coverage", 0.0) or 0.0) / 0.55)
                * 0.55
                + min(1.0, float(candidate.get("estimated_turns", 0) or 0.0) / 4.0)
                * 0.45,
            )
            or 0.0
        )
        closure_score = float(
            baseline.get(
                "closure_score",
                min(
                    1.0,
                    max(
                        0.0, 1.0 - (float(candidate.get("tail_gap", 0.0) or 0.0) / 2.1)
                    ),
                ),
            )
            or 0.0
        )
        story_clarity_score = float(
            baseline.get(
                "story_clarity_score",
                speech_density * 0.34
                + development_score * 0.28
                + hook_score * 0.20
                + closure_score * 0.18,
            )
            or 0.0
        )
        source_face_presence = float(baseline.get("face_presence", 0.0) or 0.0)
        source_person_presence = float(baseline.get("person_presence", 0.0) or 0.0)
        source_subject_presence = float(baseline.get("subject_presence", 0.0) or 0.0)
        face_evidence_score = max(
            0.0,
            min(
                1.0,
                source_face_presence * 0.62
                + source_person_presence * 0.22
                + source_subject_presence * 0.16,
            ),
        )
        visual_subject_score = float(
            baseline.get(
                "visual_subject_score",
                max(
                    0.18,
                    face_evidence_score * 0.85
                    + (0.18 if speech_density >= 0.24 else 0.08),
                ),
            )
            or 0.0
        )
        reframe_feasibility_score = float(
            baseline.get(
                "reframe_feasibility_score",
                min(
                    1.0,
                    visual_subject_score * 0.72
                    + story_clarity_score * 0.18
                    + audio_energy * 0.10,
                ),
            )
            or 0.0
        )
        empty_frame_risk = float(
            baseline.get(
                "empty_frame_risk",
                max(
                    0.0,
                    1.0
                    - (visual_subject_score * 0.75 + reframe_feasibility_score * 0.35),
                ),
            )
            or 0.0
        )
        if face_evidence_score <= 0.06:
            visual_subject_score = min(
                visual_subject_score, 0.18 if speech_density < 0.40 else 0.22
            )
            reframe_feasibility_score = min(reframe_feasibility_score, 0.24)
            empty_frame_risk = max(empty_frame_risk, 0.84)
        premise_scores = self._premise_signal_scores(
            faces={
                "face_presence": source_face_presence,
                "person_presence": source_person_presence,
                "subject_presence": source_subject_presence,
                "avg_face_size": float(baseline.get("avg_face_size", 0.0) or 0.0),
                "avg_person_size": float(baseline.get("avg_person_size", 0.0) or 0.0),
            },
            video={
                "motion": float(baseline.get("motion", 0.0) or 0.0),
                "brightness": float(baseline.get("brightness", 0.0) or 0.0),
            },
            summary={
                "silence_ratio": silence_ratio,
                "audio_energy": audio_energy,
            },
            hook_score=hook_score,
            story_context_score=float(baseline.get("story_context_score", 0.0) or 0.0),
            curiosity_gap_score=float(baseline.get("curiosity_gap_score", 0.0) or 0.0),
            payoff_strength=closure_score,
            cold_open_dead_time_penalty=0.0,
            subtitle_quality_score=float(
                baseline.get("subtitle_quality_score", 0.0) or 0.0
            ),
            visual_subject_score=visual_subject_score,
            reframe_feasibility_score=reframe_feasibility_score,
            empty_frame_risk=empty_frame_risk,
        )
        score = round(
            speech_density * 0.26
            + max(0.0, 1.0 - silence_ratio) * 0.10
            + story_clarity_score * 0.22
            + hook_score * 0.12
            + closure_score * 0.10
            + reframe_feasibility_score * 0.08
            + visual_subject_score * 0.06
            + audio_energy * 0.06
            + face_evidence_score * 0.14
            + premise_scores["premise_signal_score"] * 0.10
            - empty_frame_risk * 0.08
            - max(0.0, 0.08 - face_evidence_score) * 0.10,
            4,
        )
        face_visibility_multiplier = max(
            0.35, min(1.0, 0.35 + face_evidence_score * 0.65)
        )
        face_evidence_penalty = 0.0
        if face_evidence_score <= 0.06:
            face_evidence_penalty = 0.14
        elif face_evidence_score < 0.18:
            face_evidence_penalty = (0.18 - face_evidence_score) * 0.22
        score = round(
            max(0.0, score * face_visibility_multiplier - face_evidence_penalty), 4
        )
        fallback_breakdown = dict(baseline)
        hook_strength = round(hook_score, 4)
        payoff_strength = round(closure_score, 4)
        curiosity_gap_score = round(
            max(
                0.0,
                min(
                    1.0,
                    hook_score * 0.58
                    + max(0.0, 1.0 - silence_ratio) * 0.14
                    + min(1.0, float(candidate.get("estimated_turns", 0) or 0.0) / 4.0)
                    * 0.18
                    + min(
                        1.0, float(candidate.get("speech_coverage", 0.0) or 0.0) / 0.60
                    )
                    * 0.10,
                ),
            ),
            4,
        )
        cold_open_dead_time_penalty = round(
            max(
                0.0,
                min(
                    1.0,
                    float(candidate.get("hook_gap", 0.0) or 0.0)
                    / max(
                        0.15,
                        float(
                            self.cfg.get("cold_open_dead_time_threshold_seconds", 0.45)
                            or 0.45
                        ),
                    )
                    - 1.0,
                ),
            ),
            4,
        )
        watchability_score = round(
            max(
                0.0,
                min(
                    1.0,
                    hook_score * 0.22
                    + closure_score * 0.18
                    + story_clarity_score * 0.22
                    + reframe_feasibility_score * 0.14
                    + visual_subject_score * 0.08
                    + audio_energy * 0.08
                    + premise_scores["visual_premise_strength"] * 0.10
                    + premise_scores["first_second_hook_score"] * 0.05
                    + premise_scores["sound_off_hook_score"] * 0.05
                    + max(0.0, 1.0 - silence_ratio) * 0.12
                    - cold_open_dead_time_penalty * 0.18,
                ),
            ),
            4,
        )
        score = round(max(0.0, score - duration_penalty * 0.22), 4)
        recommendation_readiness_score = round(
            max(
                0.0,
                min(
                    1.0,
                    watchability_score * 0.34
                    + hook_strength * 0.20
                    + curiosity_gap_score * 0.14
                    + payoff_strength * 0.14
                    + visual_subject_score * 0.08
                    + reframe_feasibility_score * 0.06
                    + story_clarity_score * 0.08
                    + premise_scores["premise_signal_score"] * 0.08,
                ),
            ),
            4,
        )
        packaging_quality_score = round(
            max(
                0.0,
                min(
                    1.0,
                    hook_strength * 0.28
                    + payoff_strength * 0.18
                    + story_clarity_score * 0.18
                    + max(0.0, 1.0 - silence_ratio) * 0.14
                    + premise_scores["visual_premise_strength"] * 0.12
                    + premise_scores["first_second_hook_score"] * 0.10,
                ),
            ),
            4,
        )
        if face_evidence_score <= 0.06:
            visual_subject_score = min(
                visual_subject_score, 0.18 if speech_density < 0.40 else 0.22
            )
            reframe_feasibility_score = min(reframe_feasibility_score, 0.24)
            empty_frame_risk = max(empty_frame_risk, 0.88)
            watchability_score = round(min(watchability_score, 0.30), 4)
            recommendation_readiness_score = round(
                min(recommendation_readiness_score, 0.28), 4
            )
            packaging_quality_score = round(min(packaging_quality_score, 0.32), 4)
        elif face_evidence_score < 0.18:
            damp = 0.72 + face_evidence_score * 1.5
            watchability_score = round(max(0.0, watchability_score * damp), 4)
            recommendation_readiness_score = round(
                max(0.0, recommendation_readiness_score * damp), 4
            )
            packaging_quality_score = round(max(0.0, packaging_quality_score * damp), 4)
        story_unit_type = str(
            candidate.get("story_unit_type", "dialogue_cluster") or "dialogue_cluster"
        )
        story_profile = self._story_arc_profile(candidate)
        fallback_breakdown.update(
            {
                "speech_density": round(speech_density, 4),
                "silence_ratio": round(silence_ratio, 4),
                "audio_energy": round(audio_energy, 4),
                "hook_score": round(hook_score, 4),
                "development_score": round(development_score, 4),
                "closure_score": round(closure_score, 4),
                "story_clarity_score": round(story_clarity_score, 4),
                "clarity_score": round(story_clarity_score, 4),
                "duration_penalty": round(duration_penalty, 4),
                "visual_subject_score": round(visual_subject_score, 4),
                "reframe_feasibility_score": round(reframe_feasibility_score, 4),
                "empty_frame_risk": round(empty_frame_risk, 4),
                "face_evidence_score": round(face_evidence_score, 4),
                "source_face_presence": round(source_face_presence, 4),
                "source_person_presence": round(source_person_presence, 4),
                "source_subject_presence": round(source_subject_presence, 4),
                "hook_strength": hook_strength,
                "curiosity_gap_score": curiosity_gap_score,
                "payoff_strength": payoff_strength,
                "watchability_score": watchability_score,
                "recommendation_readiness_score": recommendation_readiness_score,
                "cold_open_dead_time_penalty": cold_open_dead_time_penalty,
                "visual_premise_strength": premise_scores["visual_premise_strength"],
                "first_second_hook_score": premise_scores["first_second_hook_score"],
                "sound_off_hook_score": premise_scores["sound_off_hook_score"],
                "premise_signal_score": premise_scores["premise_signal_score"],
                "packaging_quality_score": packaging_quality_score,
                "story_unit_type": story_unit_type,
                "story_completion_score": round(
                    float(story_profile["story_completion_score"]), 4
                ),
                "context_completeness_score": round(
                    float(story_profile["context_completeness_score"]), 4
                ),
                "hook_type": story_profile["hook_type"],
                "payoff_type": story_profile["payoff_type"],
                "story_arc_shape": story_profile["story_arc_shape"],
                "conversation_id": story_profile["conversation_id"],
                "topic_shift_events": int(story_profile["topic_shift_events"]),
                "ranking_mode_used": "timeout_fallback",
                "timeout_fallback_used": True,
                "timeout_fallback_reason": "ranking_timeout",
            }
        )
        return score, fallback_breakdown

    def _classify_story_archetype(self, candidate: dict, breakdown: dict) -> str:
        if not bool(self.cfg.get("story_archetype_detection", True)):
            return str(
                candidate.get("story_unit_type", "dialogue_cluster")
                or "dialogue_cluster"
            )
        source_type = str(
            candidate.get("story_unit_type", "dialogue_cluster") or "dialogue_cluster"
        ).lower()
        hook = float(breakdown.get("hook_score", 0.0) or 0.0)
        closure = float(breakdown.get("closure_score", 0.0) or 0.0)
        dialogue = float(breakdown.get("dialogue_exchange_score", 0.0) or 0.0)
        context = float(breakdown.get("story_context_score", 0.0) or 0.0)
        speech_density = float(breakdown.get("speech_density", 0.0) or 0.0)
        visible_stakes = float(breakdown.get("visible_stakes_score", 0.0) or 0.0)
        first_frame_clarity = float(
            breakdown.get("first_frame_clarity_score", 0.0) or 0.0
        )
        sound_off_premise = float(
            breakdown.get(
                "sound_off_premise_score", breakdown.get("sound_off_hook_score", 0.0)
            )
            or 0.0
        )
        dialogue_dependency_penalty = float(
            breakdown.get("dialogue_dependency_penalty", 0.0) or 0.0
        )
        if "stitched" in source_type:
            return "reveal_discovery"
        if (
            visible_stakes >= 0.76
            and first_frame_clarity >= 0.68
            and sound_off_premise >= 0.64
            and dialogue_dependency_penalty <= 0.36
        ):
            return "rescue_urgency"
        if (
            visible_stakes >= 0.70
            and sound_off_premise >= 0.62
            and hook >= 0.56
            and dialogue_dependency_penalty <= 0.42
        ):
            return "danger_escape"
        if (
            visible_stakes >= 0.66
            and hook >= 0.54
            and closure >= 0.46
            and dialogue >= 0.36
        ):
            return "impossible_choice"
        if dialogue >= 0.58 and hook >= 0.56 and closure >= 0.52:
            return "confrontation"
        if dialogue >= 0.52 and context >= 0.34 and 0.46 <= closure <= 0.72:
            return "accusation_denial"
        if hook >= 0.64 and closure >= 0.44:
            return "reveal_discovery"
        if closure >= 0.72 and speech_density >= 0.46:
            return "emotional_confession"
        if hook >= 0.56 and speech_density >= 0.42:
            return "threat_tension"
        if context >= 0.26 or (hook >= 0.46 and closure >= 0.38):
            return "investigation_clue"
        return "dialogue_cluster"

    def _should_retry_reframe(self, reframe_debug: dict, reframed: bool) -> bool:
        if not reframed:
            return True
        anchor_switches = int(reframe_debug.get("anchor_switches", 0) or 0)
        dialogue_center_used = bool(reframe_debug.get("dialogue_center_used", False))
        listener_used = bool(reframe_debug.get("listener_face_fallback_used", False))
        subject_person_used = bool(
            reframe_debug.get("subject_person_fallback_used", False)
        )
        scene_interest_used = bool(
            reframe_debug.get("scene_interest_fallback_used", False)
        )
        no_subject_windows = int(reframe_debug.get("no_subject_windows", 0) or 0)
        subject_visibility_ratio = float(
            reframe_debug.get("subject_visibility_ratio", 1.0) or 0.0
        )
        face_edge_clip_rate = float(
            reframe_debug.get("face_edge_clip_rate", 0.0) or 0.0
        )
        scene_interest_windows = int(
            reframe_debug.get("scene_interest_windows", 0) or 0
        )
        speaker_center_offset_avg = float(
            reframe_debug.get("speaker_center_offset_avg", 0.0) or 0.0
        )
        speaker_center_offset_p95 = float(
            reframe_debug.get("speaker_center_offset_p95", 0.0) or 0.0
        )
        speaker_centered_rate = float(
            reframe_debug.get("speaker_centered_rate", 0.0) or 0.0
        )
        speaker_face_centered_windows = int(
            reframe_debug.get("speaker_face_centered_windows", 0) or 0
        )
        speaker_center_max_offset = float(
            self.cfg.get("speaker_center_max_offset", 0.18)
        )
        dialogue_center_windows = int(
            reframe_debug.get("dialogue_center_windows", 0) or 0
        )
        listener_fallback_windows = int(
            reframe_debug.get("listener_fallback_windows", 0) or 0
        )
        subject_person_fallback_windows = int(
            reframe_debug.get("subject_person_fallback_windows", 0) or 0
        )
        strong_speaker_center = (
            speaker_face_centered_windows >= 3
            and speaker_centered_rate >= 0.32
            and face_edge_clip_rate <= 0.18
        )
        return (
            anchor_switches >= 10
            or (
                scene_interest_used
                and not listener_used
                and not dialogue_center_used
                and not subject_person_used
            )
            or no_subject_windows >= 2
            or subject_visibility_ratio
            < float(self.cfg.get("subject_visibility_threshold", 0.46)) * 0.82
            or face_edge_clip_rate > 0.24
            or scene_interest_windows >= 3
            or (
                speaker_center_offset_p95 > speaker_center_max_offset * 1.18
                and not strong_speaker_center
            )
            or (
                speaker_center_offset_avg > speaker_center_max_offset * 0.92
                and not strong_speaker_center
            )
            or (
                speaker_centered_rate > 0.0
                and speaker_centered_rate < 0.52
                and (
                    dialogue_center_windows > 0
                    or listener_fallback_windows > 0
                    or subject_person_fallback_windows > 0
                )
            )
        )

    def _quality_profile(self) -> str:
        return str(
            self.cfg.get(
                "quality_profile", self.cfg.get("selection_policy", "quality_first")
            )
            or "quality_first"
        ).lower()

    def _story_mode(self) -> str:
        mode = str(self.cfg.get("story_mode", "standard") or "standard").lower()
        return mode if mode in {"standard", "auto", "tension"} else "standard"

    def _dialogue_flow_admission(self, summary: dict) -> dict:
        turns = list(summary.get("turns", []) or [])
        voiced_intervals = list(summary.get("voiced_intervals", []) or [])
        speech_density_value = float(summary.get("speech_density", 0.0) or 0.0)
        silence_ratio = float(summary.get("silence_ratio", 1.0) or 1.0)
        audio_energy = float(summary.get("audio_energy", 0.0) or 0.0)

        if (
            speech_density_value < 0.12
            and audio_energy < 0.08
            and not turns
            and not voiced_intervals
        ):
            return {
                "admit": False,
                "reason": "audio_starvation",
                "dialogue_shape": "silent_or_empty",
            }
        if len(turns) >= 2:
            return {
                "admit": True,
                "reason": "multi_turn_dialogue",
                "dialogue_shape": "multi_turn",
            }
        if len(turns) == 1:
            if (
                speech_density_value >= 0.16
                or audio_energy >= 0.10
                or silence_ratio <= 0.78
            ):
                return {
                    "admit": True,
                    "reason": "single_turn_dialogue_soft",
                    "dialogue_shape": "single_block",
                }
            return {
                "admit": True,
                "reason": "single_turn_dialogue_soft",
                "dialogue_shape": "single_block_sparse",
            }
        if speech_density_value >= 0.22 and audio_energy >= 0.10 and voiced_intervals:
            return {
                "admit": True,
                "reason": "dense_voiced_dialogue",
                "dialogue_shape": "dense_voiced",
            }
        if speech_density_value >= 0.18 and audio_energy >= 0.12:
            return {
                "admit": True,
                "reason": "dialogue_proxy",
                "dialogue_shape": "audio_proxy",
            }
        return {
            "admit": False,
            "reason": "low_dialogue_flow",
            "dialogue_shape": "sparse",
        }

    def _dialogue_flow_is_sufficient(self, summary: dict) -> bool:
        return bool(self._dialogue_flow_admission(summary).get("admit", False))

    def _episode_story_policy(
        self, story_candidates: list[dict], ranked_candidates: list[dict] | None = None
    ) -> dict:
        configured_mode = self._story_mode()
        safety_cap = max(1, int(self.cfg.get("max_shorts", 50)))
        admission_fraction = float(self.cfg.get("selection_admission_fraction", 0.20))
        if configured_mode == "tension":
            admission_fraction = max(
                admission_fraction,
                float(self.cfg.get("tension_admission_fraction", 0.24)),
            )

        source_candidates = list(ranked_candidates or story_candidates or [])
        scored_candidates = sorted(
            source_candidates,
            key=lambda item: (
                float(
                    item.get("score_breakdown", {}).get("visible_stakes_score", 0.0)
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get(
                        "first_frame_clarity_score", 0.0
                    )
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get(
                        "sound_off_premise_score",
                        item.get("score_breakdown", {}).get(
                            "sound_off_hook_score", 0.0
                        ),
                    )
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get("first_second_hook_score", 0.0)
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get("story_interest_score", 0.0)
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get("story_completeness_score", 0.0)
                    or 0.0
                ),
                float(item.get("score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        top_sample = scored_candidates[: min(12, len(scored_candidates))]
        focus_sample = top_sample[: min(5, len(top_sample))]

        def _avg(items: list[dict], key: str, fallback_key: str | None = None) -> float:
            if not items:
                return 0.0
            values = []
            for item in items:
                breakdown = dict(item.get("score_breakdown", {}) or {})
                if key in breakdown:
                    values.append(float(breakdown.get(key, 0.0) or 0.0))
                elif fallback_key and fallback_key in breakdown:
                    values.append(float(breakdown.get(fallback_key, 0.0) or 0.0))
                else:
                    values.append(float(item.get(key, 0.0) or 0.0))
            return sum(values) / max(1, len(values))

        tension_density = sum(
            self._candidate_tension_context_score(item) for item in focus_sample
        ) / max(1, len(focus_sample))
        visible_stakes = _avg(focus_sample, "visible_stakes_score")
        first_frame_clarity = _avg(focus_sample, "first_frame_clarity_score")
        sound_off_premise = _avg(
            focus_sample, "sound_off_premise_score", "sound_off_hook_score"
        )
        first_second_hook = _avg(focus_sample, "first_second_hook_score")
        story_interest = _avg(focus_sample, "story_interest_score")
        story_completeness = _avg(focus_sample, "story_completeness_score")
        story_context = _avg(focus_sample, "story_context_score")
        silence_ratio = _avg(focus_sample, "silence_ratio")
        dialogue_dependency_penalty = _avg(focus_sample, "dialogue_dependency_penalty")

        if configured_mode == "standard":
            episode_mode = "standard"
        elif configured_mode == "tension":
            episode_mode = "tension"
        else:
            episode_mode = "standard"
            if tension_density >= 0.54 and (
                visible_stakes >= 0.46
                or sound_off_premise >= 0.56
                or first_second_hook >= 0.54
                or story_context >= 0.22
                or dialogue_dependency_penalty >= 0.28
                or silence_ratio >= 0.32
            ):
                episode_mode = "tension"

        if (
            len(story_candidates) >= 6
            and story_interest >= 0.66
            and story_completeness >= 0.58
            and first_frame_clarity >= 0.52
        ):
            admission_fraction = min(max(admission_fraction, 0.22), 0.35)
        elif episode_mode == "standard":
            admission_fraction = min(admission_fraction, 0.24)

        admission_min_pool = int(self.cfg.get("selection_admission_min_pool", 6))
        admission_max_pool = int(self.cfg.get("selection_admission_max_pool", 48))
        admission_target = (
            ceil(len(story_candidates) * admission_fraction) if story_candidates else 0
        )
        admission_cap = (
            min(
                len(story_candidates),
                max(admission_min_pool, min(admission_target, admission_max_pool)),
            )
            if story_candidates
            else 0
        )

        output_budget = safety_cap
        quality_floor = 0.50
        arc_count = 0
        if scored_candidates:
            quality_sample = top_sample[: min(8, len(top_sample))]
            score_values = [
                float(item.get("score", 0.0) or 0.0) for item in quality_sample
            ]
            mean_score = sum(score_values) / max(1, len(score_values))
            top_score = max(score_values) if score_values else 0.0
            story_type_keys = set()
            strong_count = 0
            for item in quality_sample:
                breakdown = dict(item.get("score_breakdown", {}) or {})
                item_score = float(item.get("score", 0.0) or 0.0)
                item_interest = float(breakdown.get("story_interest_score", 0.0) or 0.0)
                item_completeness = float(
                    breakdown.get("story_completeness_score", 0.0) or 0.0
                )
                item_hook = max(
                    float(breakdown.get("visual_premise_strength", 0.0) or 0.0),
                    float(
                        breakdown.get(
                            "sound_off_premise_score",
                            breakdown.get("sound_off_hook_score", 0.0),
                        )
                        or 0.0
                    ),
                    float(breakdown.get("first_second_hook_score", 0.0) or 0.0),
                    float(breakdown.get("visible_stakes_score", 0.0) or 0.0),
                )
                macro_index = int(
                    item.get("score_breakdown", {}).get(
                        "macro_context_index", item.get("macro_context_index", 0)
                    )
                    or 0
                )
                if (
                    item_score >= 0.62
                    and item_interest >= 0.60
                    and item_completeness >= 0.54
                    and item_hook >= 0.50
                ):
                    strong_count += 1
                    story_type_keys.add(
                        (
                            str(
                                breakdown.get(
                                    "story_unit_type",
                                    item.get("story_unit_type", "dialogue_cluster"),
                                )
                                or "dialogue_cluster"
                            ).lower(),
                            macro_index,
                        )
                    )
            arc_count = (
                max(1, len(story_type_keys))
                if strong_count
                else max(
                    1,
                    len(
                        {
                            str(
                                dict(item.get("score_breakdown", {}) or {}).get(
                                    "story_unit_type",
                                    item.get("story_unit_type", "dialogue_cluster"),
                                )
                                or "dialogue_cluster"
                            ).lower()
                            for item in quality_sample
                        }
                    ),
                )
            )
            density_budget = max(1, int(round(len(scored_candidates) * 0.30)))
            base_budget = max(1, strong_count, arc_count, density_budget)
            if top_score >= 0.80 and mean_score >= 0.70:
                base_budget += 2
            if episode_mode == "tension" and base_budget > 1:
                base_budget = min(base_budget + 1, safety_cap)
            output_floor = (
                5 if len(scored_candidates) >= 5 else max(1, len(scored_candidates))
            )
            output_budget = min(safety_cap, max(output_floor, base_budget))
            quality_floor = max(
                0.46,
                min(0.72, mean_score - (0.04 if episode_mode == "standard" else 0.03)),
            )

        return {
            "configured_mode": configured_mode,
            "story_mode": episode_mode,
            "tension_density": round(tension_density, 4),
            "visible_stakes_score": round(visible_stakes, 4),
            "first_frame_clarity_score": round(first_frame_clarity, 4),
            "sound_off_premise_score": round(sound_off_premise, 4),
            "first_second_hook_score": round(first_second_hook, 4),
            "story_interest_score": round(story_interest, 4),
            "story_completeness_score": round(story_completeness, 4),
            "story_context_score": round(story_context, 4),
            "silence_ratio": round(silence_ratio, 4),
            "dialogue_dependency_penalty": round(dialogue_dependency_penalty, 4),
            "selection_admission_fraction": round(admission_fraction, 4),
            "selection_admission_target": int(admission_target),
            "selection_admission_cap": int(admission_cap),
            "output_budget": int(output_budget),
            "quality_floor": round(quality_floor, 4),
            "arc_count": int(arc_count),
            "safety_cap": int(safety_cap),
        }

    def _candidate_macro_context(self, candidate: dict) -> dict:
        window_seconds = max(
            300.0, float(self.cfg.get("tension_context_window_seconds", 1200) or 1200)
        )
        start = max(0.0, float(candidate.get("start", 0.0) or 0.0))
        macro_index = int(start // window_seconds)
        macro_start = round(macro_index * window_seconds, 3)
        macro_end = round(macro_start + window_seconds, 3)
        return {
            "macro_context_index": macro_index,
            "macro_context_start": macro_start,
            "macro_context_end": macro_end,
            "macro_context_window_seconds": round(window_seconds, 3),
        }

    def _candidate_tension_context_score(
        self, candidate: dict, subtitle_info: dict | None = None
    ) -> float:
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        signals = dict((subtitle_info or {}).get("signals", {}) or {})
        speech_coverage = max(
            0.0,
            min(
                1.0,
                float(
                    breakdown.get(
                        "speech_coverage", candidate.get("speech_coverage", 0.0)
                    )
                    or 0.0
                ),
            ),
        )
        silence_ratio = max(
            0.0,
            min(
                1.0,
                float(
                    breakdown.get("silence_ratio", candidate.get("silence_ratio", 1.0))
                    or 0.0
                ),
            ),
        )
        story_context = max(
            float(breakdown.get("story_context_score", 0.0) or 0.0),
            float(signals.get("dialogue_flow_score", 0.0) or 0.0),
            float(signals.get("dialogue_exchange_score", 0.0) or 0.0) * 0.25,
        )
        story_interest = max(
            float(breakdown.get("story_interest_score", 0.0) or 0.0),
            float(signals.get("interestingness_score", 0.0) or 0.0),
        )
        payoff = max(
            float(
                breakdown.get("payoff_strength", breakdown.get("closure_score", 0.0))
                or 0.0
            ),
            float(signals.get("closure_score", 0.0) or 0.0),
        )
        hook_strength = max(
            float(
                breakdown.get("hook_strength", breakdown.get("hook_score", 0.0)) or 0.0
            ),
            float(signals.get("hook_score", 0.0) or 0.0),
        )
        visible_stakes = float(breakdown.get("visible_stakes_score", 0.0) or 0.0)
        first_second_hook = float(breakdown.get("first_second_hook_score", 0.0) or 0.0)
        sound_off_hook = float(breakdown.get("sound_off_hook_score", 0.0) or 0.0)
        dialogue_dependency_penalty = float(
            breakdown.get("dialogue_dependency_penalty", 0.0) or 0.0
        )
        cold_open_penalty = float(
            breakdown.get("cold_open_dead_time_penalty", 0.0) or 0.0
        )
        return round(
            max(
                0.0,
                min(
                    1.0,
                    hook_strength * 0.20
                    + visible_stakes * 0.18
                    + max(first_second_hook, sound_off_hook) * 0.12
                    + story_context * 0.15
                    + story_interest * 0.12
                    + payoff * 0.08
                    + speech_coverage * 0.07
                    + max(0.0, 1.0 - silence_ratio) * 0.04
                    + dialogue_dependency_penalty * 0.04
                    + max(0.0, 1.0 - cold_open_penalty) * 0.04,
                ),
            ),
            4,
        )

    def _effective_story_mode(
        self,
        candidate: dict,
        subtitle_info: dict | None = None,
        base_band: str | None = None,
    ) -> str:
        configured_mode = self._story_mode()
        if configured_mode in {"standard", "tension"}:
            return configured_mode
        effective_band = str(
            base_band or candidate.get("duration_policy", {}).get("band", "") or ""
        ).lower()
        if effective_band in {"strong_story", "exceptional_high_interest"}:
            return "standard"
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        tension_score = self._candidate_tension_context_score(candidate, subtitle_info)
        visible_stakes = float(breakdown.get("visible_stakes_score", 0.0) or 0.0)
        hook_strength = float(
            breakdown.get("hook_strength", breakdown.get("hook_score", 0.0)) or 0.0
        )
        story_context = float(breakdown.get("story_context_score", 0.0) or 0.0)
        dialogue_dependency_penalty = float(
            breakdown.get("dialogue_dependency_penalty", 0.0) or 0.0
        )
        if tension_score >= 0.54 and (
            visible_stakes >= 0.48
            or hook_strength >= float(self.cfg.get("hook_score_threshold", 0.34)) + 0.06
            or story_context >= 0.22
            or dialogue_dependency_penalty >= 0.28
        ):
            return "tension"
        return "standard"

    def _candidate_duration_policy(
        self, candidate: dict, subtitle_info: dict | None = None
    ) -> dict:
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        signals = dict((subtitle_info or {}).get("signals", {}) or {})
        duration = max(
            0.0, float(candidate.get("duration", breakdown.get("duration", 0.0)) or 0.0)
        )
        interestingness_threshold = float(
            self.cfg.get("interestingness_threshold", 0.52)
        )
        watchability_threshold = float(self.cfg.get("watchability_threshold", 0.54))
        recommendation_threshold = float(
            self.cfg.get("recommendation_readiness_threshold", 0.56)
        )
        payoff_threshold = float(self.cfg.get("min_story_payoff_score", 0.40))
        subtitle_threshold = float(
            self.cfg.get("subtitle_quality_score_threshold", 0.66)
        )
        boundary_threshold = float(
            self.cfg.get("story_boundary_confidence_threshold", 0.58)
        )
        hook_threshold = float(self.cfg.get("hook_score_threshold", 0.34))
        duration_floor = max(35.0, float(self.cfg.get("min_publishable_seconds", 35)))

        story_interest = max(
            float(breakdown.get("story_interest_score", 0.0) or 0.0),
            float(breakdown.get("preview_interestingness_score", 0.0) or 0.0),
            float(signals.get("interestingness_score", 0.0) or 0.0),
        )
        story_completeness = max(
            float(breakdown.get("story_completeness_score", 0.0) or 0.0),
            float(signals.get("closure_score", 0.0) or 0.0),
            float(signals.get("story_boundary_confidence", 0.0) or 0.0),
        )
        watchability = float(breakdown.get("watchability_score", 0.0) or 0.0)
        recommendation = float(
            breakdown.get("recommendation_readiness_score", 0.0) or 0.0
        )
        payoff = max(
            float(breakdown.get("payoff_strength", 0.0) or 0.0),
            float(breakdown.get("closure_score", 0.0) or 0.0),
            float(signals.get("closure_score", 0.0) or 0.0),
        )
        subtitle_quality = max(
            float(breakdown.get("subtitle_quality_score", 0.0) or 0.0),
            float(signals.get("subtitle_quality_score", 0.0) or 0.0),
        )
        boundary_confidence = max(
            float(breakdown.get("story_boundary_confidence", 0.0) or 0.0),
            float(signals.get("story_boundary_confidence", 0.0) or 0.0),
            float(signals.get("dialogue_flow_score", 0.0) or 0.0),
        )
        hook_strength = max(
            float(
                breakdown.get("hook_strength", breakdown.get("hook_score", 0.0)) or 0.0
            ),
            float(signals.get("hook_score", 0.0) or 0.0),
        )
        speech_coverage = max(
            0.0, min(1.0, float(candidate.get("speech_coverage", 0.0) or 0.0))
        )
        dialogue_density = max(0.0, min(1.0, speech_coverage / 0.55))
        estimated_turns = max(0.0, float(candidate.get("estimated_turns", 0.0) or 0.0))
        silence_ratio = max(
            0.0,
            min(
                1.0,
                float(
                    breakdown.get("silence_ratio", candidate.get("silence_ratio", 1.0))
                    or 0.0
                ),
            ),
        )
        hook_gap = max(0.0, float(candidate.get("hook_gap", 0.0) or 0.0))
        tail_gap = max(0.0, float(candidate.get("tail_gap", 0.0) or 0.0))
        story_continued = bool(candidate.get("story_continued_after_pause", False))
        context = max(
            float(breakdown.get("story_context_score", 0.0) or 0.0),
            float(signals.get("dialogue_flow_score", 0.0) or 0.0),
            float(signals.get("dialogue_exchange_score", 0.0) or 0.0) * 0.25,
        )
        visual_premise_strength = float(
            breakdown.get("visual_premise_strength", 0.0) or 0.0
        )
        sound_off_hook_score = float(breakdown.get("sound_off_hook_score", 0.0) or 0.0)
        first_second_hook_score = float(
            breakdown.get("first_second_hook_score", 0.0) or 0.0
        )
        cold_open_dead_time_penalty = float(
            breakdown.get("cold_open_dead_time_penalty", 0.0) or 0.0
        )
        visible_stakes_score = float(
            breakdown.get(
                "visible_stakes_score",
                max(
                    visual_premise_strength * 0.70,
                    sound_off_hook_score * 0.64,
                    first_second_hook_score * 0.60,
                ),
            )
            or 0.0
        )
        first_frame_clarity_score = float(
            breakdown.get(
                "first_frame_clarity_score",
                max(
                    0.0,
                    min(
                        1.0,
                        first_second_hook_score * 0.45
                        + visual_premise_strength * 0.35
                        + max(0.0, 1.0 - cold_open_dead_time_penalty) * 0.20,
                    ),
                ),
            )
            or 0.0
        )
        sound_off_premise_score = float(
            breakdown.get(
                "sound_off_premise_score",
                max(
                    sound_off_hook_score,
                    visual_premise_strength * 0.18 + first_second_hook_score * 0.12,
                ),
            )
            or 0.0
        )
        dialogue_audio_mismatch = 0.0
        subtitle_turns = 0
        if subtitle_info is not None:
            subtitle_turns = int(subtitle_info.get("line_count", 0) or 0)
            if estimated_turns <= 1 and subtitle_turns >= max(
                3, int(self.cfg.get("min_subtitle_turns", 3))
            ):
                if (
                    float(signals.get("dialogue_exchange_score", 0.0) or 0.0) >= 0.55
                    or float(signals.get("interestingness_score", 0.0) or 0.0) >= 0.55
                ):
                    dialogue_audio_mismatch = 1.0
        dialogue_dependency_penalty = float(
            breakdown.get(
                "dialogue_dependency_penalty",
                max(
                    0.0,
                    dialogue_density * 0.42
                    + context * 0.10
                    - max(
                        visual_premise_strength,
                        sound_off_hook_score,
                        first_second_hook_score,
                    )
                    * 0.16,
                ),
            )
            or 0.0
        )
        duration_floor_penalty = max(
            0.0, (duration_floor - duration) / max(1.0, duration_floor)
        )
        strong_hook_ready = (
            visual_premise_strength
            >= float(self.cfg.get("visual_premise_threshold", 0.56))
            and sound_off_hook_score
            >= float(self.cfg.get("sound_off_hook_threshold", 0.62))
            and first_second_hook_score
            >= float(self.cfg.get("first_second_hook_threshold", 0.60))
            and visible_stakes_score >= 0.58
            and cold_open_dead_time_penalty <= 0.0
        )
        visual_story_ready = (
            strong_hook_ready
            and visible_stakes_score >= 0.72
            and visual_premise_strength >= 0.78
            and sound_off_hook_score >= 0.70
        )
        if subtitle_info is None:
            raw_proxy = max(
                0.0,
                min(
                    1.0,
                    speech_coverage * 0.28
                    + min(1.0, estimated_turns / 4.0) * 0.12
                    + max(0.0, 1.0 - silence_ratio) * 0.22
                    + max(
                        0.0,
                        1.0
                        - (
                            hook_gap
                            / max(
                                0.25, float(self.cfg.get("hook_max_lead_seconds", 4.5))
                            )
                        ),
                    )
                    * 0.12
                    + max(0.0, 1.0 - min(1.0, tail_gap / 2.1)) * 0.10
                    + (0.08 if story_continued else 0.0)
                    + min(1.0, hook_strength) * 0.08,
                ),
            )
            strong_story = (
                raw_proxy >= 0.56
                and duration >= duration_floor
                and strong_hook_ready
                and visible_stakes_score >= 0.62
            )
            exceptional_story = (
                raw_proxy >= 0.72
                and duration >= 45.0
                and visual_story_ready
                and payoff >= payoff_threshold
            )
        else:
            story_interest_proxy = max(
                story_interest,
                float(breakdown.get("story_interest_score", 0.0) or 0.0),
                float(signals.get("interestingness_score", 0.0) or 0.0),
            )
            story_completeness_proxy = max(
                story_completeness,
                float(breakdown.get("story_completeness_score", 0.0) or 0.0),
                float(signals.get("closure_score", 0.0) or 0.0),
                float(signals.get("story_boundary_confidence", 0.0) or 0.0),
            )
            watchability_proxy = max(
                watchability,
                min(
                    1.0,
                    story_interest_proxy * 0.18
                    + story_completeness_proxy * 0.18
                    + hook_strength * 0.18
                    + payoff * 0.16
                    + subtitle_quality * 0.10
                    + boundary_confidence * 0.08
                    + float(breakdown.get("visual_subject_score", 0.0) or 0.0) * 0.08
                    + max(0.0, 1.0 - silence_ratio) * 0.08,
                ),
            )
            recommendation_proxy = max(
                recommendation,
                min(
                    1.0,
                    watchability_proxy * 0.34
                    + hook_strength * 0.20
                    + payoff * 0.14
                    + story_interest_proxy * 0.12
                    + story_completeness_proxy * 0.10
                    + subtitle_quality * 0.06
                    + boundary_confidence * 0.04,
                ),
            )
            watchability_proxy = max(
                0.0, watchability_proxy - duration_floor_penalty * 0.10
            )
            recommendation_proxy = max(
                0.0, recommendation_proxy - duration_floor_penalty * 0.14
            )
            strong_story = (
                story_interest_proxy >= interestingness_threshold + 0.06
                and story_completeness_proxy >= payoff_threshold + 0.08
                and watchability_proxy >= watchability_threshold + 0.04
                and recommendation_proxy >= recommendation_threshold + 0.04
                and (
                    subtitle_quality >= subtitle_threshold
                    or (
                        visual_story_ready
                        and story_completeness_proxy >= payoff_threshold + 0.12
                    )
                )
                and boundary_confidence >= boundary_threshold - 0.02
                and hook_strength >= hook_threshold + 0.02
                and strong_hook_ready
                and visible_stakes_score >= 0.58
                and duration >= duration_floor
            )
            exceptional_story = (
                story_interest_proxy >= interestingness_threshold + 0.14
                and story_completeness_proxy >= payoff_threshold + 0.14
                and watchability_proxy >= watchability_threshold + 0.10
                and recommendation_proxy >= recommendation_threshold + 0.10
                and (
                    subtitle_quality >= subtitle_threshold + 0.03
                    or (
                        visual_story_ready
                        and story_completeness_proxy >= payoff_threshold + 0.18
                    )
                )
                and boundary_confidence >= boundary_threshold + 0.06
                and hook_strength >= hook_threshold + 0.06
                and strong_hook_ready
                and visible_stakes_score >= 0.70
                and duration >= 45.0
            )

        if exceptional_story:
            base_band = "exceptional_high_interest"
            target_seconds = max(
                float(self.cfg.get("story_exceptional_target_seconds", 60)), 45.0
            )
            soft_max_seconds = max(
                float(self.cfg.get("story_exceptional_max_seconds", 60)), 45.0
            )
            hard_max_seconds = min(
                soft_max_seconds,
                float(self.cfg.get("allow_story_extension_seconds", 60)),
                float(self.cfg.get("max_short_seconds", 60)),
            )
            extension_reason = "exceptional_high_interest"
        elif strong_story:
            base_band = "strong_story"
            target_seconds = max(
                float(self.cfg.get("story_strong_target_seconds", 45)), 45.0
            )
            soft_max_seconds = max(
                float(self.cfg.get("story_strong_max_seconds", 60)), 60.0
            )
            hard_max_seconds = min(
                soft_max_seconds,
                float(self.cfg.get("allow_story_extension_seconds", 60)),
                float(self.cfg.get("max_short_seconds", 60)),
            )
            extension_reason = "strong_story_arc"
        else:
            base_band = "hook_first_short"
            target_seconds = max(float(self.cfg.get("target_story_seconds", 45)), 45.0)
            soft_max_seconds = max(
                float(self.cfg.get("story_soft_max_seconds", 60)), 60.0
            )
            hard_max_seconds = min(
                soft_max_seconds,
                float(self.cfg.get("max_short_seconds", 60)),
            )
            extension_reason = "hook_first_default"

        story_mode = self._effective_story_mode(
            candidate, subtitle_info, base_band=base_band
        )
        tension_context_score = self._candidate_tension_context_score(
            candidate, subtitle_info
        )
        macro_context = self._candidate_macro_context(candidate)
        min_publishable_seconds = float(self.cfg.get("min_publishable_seconds", 35))
        min_exceptional_publishable_seconds = float(
            self.cfg.get("min_exceptional_publishable_seconds", 35)
        )
        pause_cut_threshold_seconds = float(
            self.cfg.get("story_pause_cut_threshold_seconds", 1.0)
        )
        pause_keep_max_seconds = float(
            self.cfg.get("story_pause_keep_max_seconds", 1.15)
        )
        band = base_band
        if story_mode == "tension":
            tension_min_publishable_seconds = float(
                self.cfg.get("tension_min_story_seconds", 35)
            )
            tension_target_seconds = float(
                self.cfg.get("tension_target_story_seconds", 45)
            )
            tension_soft_max_seconds = float(
                self.cfg.get("tension_story_soft_max_seconds", 60)
            )
            tension_hard_max_seconds = min(
                float(self.cfg.get("tension_story_hard_max_seconds", 60)),
                float(self.cfg.get("max_short_seconds", 60)),
            )
            tension_exceptional_target_seconds = float(
                self.cfg.get("tension_exceptional_target_seconds", 60)
            )
            tension_exceptional_max_seconds = min(
                float(self.cfg.get("tension_exceptional_max_seconds", 60)),
                float(self.cfg.get("max_short_seconds", 60)),
            )
            pause_cut_threshold_seconds = float(
                self.cfg.get("tension_pause_cut_threshold_seconds", 1.0)
            )
            pause_keep_max_seconds = float(
                self.cfg.get("tension_pause_keep_max_seconds", 1.15)
            )
            min_publishable_seconds = tension_min_publishable_seconds
            min_exceptional_publishable_seconds = tension_min_publishable_seconds
            if exceptional_story or (
                tension_context_score >= 0.78 and payoff >= payoff_threshold + 0.04
            ):
                band = "tension_exceptional"
                target_seconds = max(
                    tension_exceptional_target_seconds, tension_target_seconds
                )
                soft_max_seconds = max(
                    tension_exceptional_max_seconds, tension_soft_max_seconds
                )
                hard_max_seconds = tension_exceptional_max_seconds
                extension_reason = "tension_exceptional_arc"
            elif strong_story or tension_context_score >= 0.64:
                band = "tension_strong"
                target_seconds = max(
                    tension_target_seconds, min(45.0, tension_target_seconds + 15.0)
                )
                soft_max_seconds = max(
                    tension_soft_max_seconds, min(60.0, tension_soft_max_seconds + 15.0)
                )
                hard_max_seconds = min(
                    tension_hard_max_seconds, max(soft_max_seconds, 60.0)
                )
                extension_reason = "tension_concentrated_arc"
            else:
                band = "tension_short"
                target_seconds = tension_target_seconds
                soft_max_seconds = tension_soft_max_seconds
                hard_max_seconds = tension_hard_max_seconds
                extension_reason = "tension_compression"

        return {
            "band": band,
            "story_mode": story_mode,
            "target_seconds": round(target_seconds, 3),
            "soft_max_seconds": round(soft_max_seconds, 3),
            "hard_max_seconds": round(hard_max_seconds, 3),
            "min_publishable_seconds": round(
                max(duration_floor, min_publishable_seconds), 3
            ),
            "min_exceptional_publishable_seconds": round(
                max(duration_floor, min_exceptional_publishable_seconds), 3
            ),
            "clarity_score": round(
                min(
                    1.0,
                    max(
                        story_interest
                        if subtitle_info is None
                        else story_interest_proxy,
                        story_completeness
                        if subtitle_info is None
                        else story_completeness_proxy,
                    )
                    * 0.42
                    + visible_stakes_score * 0.22
                    + hook_strength * 0.18
                    + boundary_confidence * 0.18,
                ),
                4,
            ),
            "duration_penalty": round(duration_floor_penalty, 4),
            "extension_reason": extension_reason,
            "exceptional_duration_used": band
            in {"exceptional_high_interest", "tension_exceptional"},
            "strong_duration_used": band
            in {
                "strong_story",
                "exceptional_high_interest",
                "tension_strong",
                "tension_exceptional",
            },
            "tension_context_score": round(tension_context_score, 4),
            "pause_cut_threshold_seconds": round(pause_cut_threshold_seconds, 3),
            "pause_keep_max_seconds": round(pause_keep_max_seconds, 3),
            **macro_context,
            "story_interest_score": round(
                story_interest if subtitle_info is None else story_interest_proxy, 4
            ),
            "story_completeness_score": round(
                story_completeness
                if subtitle_info is None
                else story_completeness_proxy,
                4,
            ),
            "watchability_score": round(
                watchability if subtitle_info is None else watchability_proxy, 4
            ),
            "recommendation_readiness_score": round(
                recommendation if subtitle_info is None else recommendation_proxy, 4
            ),
            "first_frame_clarity_score": round(first_frame_clarity_score, 4),
            "visible_stakes_score": round(visible_stakes_score, 4),
            "sound_off_premise_score": round(sound_off_premise_score, 4),
            "dialogue_dependency_penalty": round(dialogue_dependency_penalty, 4),
            "dialogue_audio_mismatch": round(dialogue_audio_mismatch, 4),
            "packaging_quality_score": round(
                min(
                    1.0,
                    hook_strength * 0.34
                    + payoff * 0.24
                    + (
                        story_interest
                        if subtitle_info is None
                        else story_interest_proxy
                    )
                    * 0.16
                    + (
                        story_completeness
                        if subtitle_info is None
                        else story_completeness_proxy
                    )
                    * 0.10
                    + subtitle_quality * 0.06,
                ),
                4,
            ),
        }

    def _resolve_candidate_duration_policy(
        self, candidate: dict, subtitle_info: dict | None = None
    ) -> dict:
        """
        Recompute and cache the subtitle-aware duration policy.

        The pre-ranking pass can attach a coarse policy before subtitles exist,
        but the final publishability gate needs the latest subtitle evidence.
        """
        duration_policy = self._candidate_duration_policy(candidate, subtitle_info)
        candidate["duration_policy"] = dict(duration_policy)
        return duration_policy

    def _is_story_override_candidate(
        self, candidate: dict, subtitle_info: dict | None = None
    ) -> bool:
        if not bool(self.cfg.get("publishable_story_override_enabled", True)):
            return False
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        signals = dict((subtitle_info or {}).get("signals", {}) or {})
        story_unit_type = str(
            breakdown.get(
                "story_unit_type", candidate.get("story_unit_type", "dialogue_cluster")
            )
            or "dialogue_cluster"
        ).lower()
        duration = float(
            breakdown.get(
                "duration", candidate.get("end", 0.0) - candidate.get("start", 0.0)
            )
            or 0.0
        )
        duration_policy = self._resolve_candidate_duration_policy(
            candidate, subtitle_info
        )
        min_publishable_seconds = float(
            duration_policy.get(
                "min_publishable_seconds", self.cfg.get("min_publishable_seconds", 35)
            )
            or self.cfg.get("min_publishable_seconds", 35)
        )
        if duration < min_publishable_seconds:
            return False
        if story_unit_type == "fallback_window" and not bool(
            candidate.get("stitched_story_unit", False)
        ):
            return False
        story_interest = max(
            float(breakdown.get("story_interest_score", 0.0) or 0.0),
            float(breakdown.get("preview_interestingness_score", 0.0) or 0.0),
            float(signals.get("interestingness_score", 0.0) or 0.0),
        )
        story_completeness = max(
            float(breakdown.get("story_completeness_score", 0.0) or 0.0),
            float(signals.get("closure_score", 0.0) or 0.0),
            float(signals.get("story_boundary_confidence", 0.0) or 0.0),
        )
        watchability_score = float(breakdown.get("watchability_score", 0.0) or 0.0)
        recommendation_readiness_score = float(
            breakdown.get("recommendation_readiness_score", 0.0) or 0.0
        )
        hook_strength = float(
            breakdown.get("hook_strength", breakdown.get("hook_score", 0.0)) or 0.0
        )
        payoff_strength = float(
            breakdown.get("payoff_strength", breakdown.get("closure_score", 0.0)) or 0.0
        )
        return bool(
            story_interest
            >= float(self.cfg.get("publishable_story_interest_threshold", 0.60))
            and story_completeness
            >= float(self.cfg.get("publishable_story_completeness_threshold", 0.68))
            and watchability_score
            >= float(self.cfg.get("publishable_story_watchability_threshold", 0.62))
            and recommendation_readiness_score
            >= float(self.cfg.get("publishable_story_recommendation_threshold", 0.64))
            and hook_strength >= float(self.cfg.get("hook_score_threshold", 0.34))
            and payoff_strength >= float(self.cfg.get("closure_score_threshold", 0.32))
        )

    def _quality_governor_decision(
        self, candidate: dict, subtitle_info: dict, reframe_debug: dict
    ) -> str:
        signals = dict(subtitle_info.get("signals", {}) or {})
        text_sanity = float(signals.get("subtitle_text_sanity_score", 1.0) or 0.0)
        subtitle_confidence = float(subtitle_info.get("confidence", 0.0) or 0.0)
        boundary_conf = float(signals.get("story_boundary_confidence", 1.0) or 0.0)
        subtitle_visual_drop_count = int(
            signals.get("subtitle_visual_drop_count", 0) or 0
        )
        subtitle_phrase_clear_count = int(
            signals.get("subtitle_phrase_clear_count", 0) or 0
        )
        subtitle_hold_too_long = bool(
            (signals.get("subtitle_visible_block_stats") or {}).get(
                "subtitle_hold_too_long", signals.get("subtitle_hold_too_long", False)
            )
        )
        subtitle_quality_score = float(
            signals.get("subtitle_quality_score", 0.0) or 0.0
        )
        story_interest = max(
            float(signals.get("interestingness_score", 0.0) or 0.0),
            float(
                candidate.get("score_breakdown", {}).get("story_interest_score", 0.0)
                or 0.0
            ),
        )
        watchability_score = float(
            candidate.get("score_breakdown", {}).get("watchability_score", 0.0) or 0.0
        )
        recommendation_readiness_score = float(
            candidate.get("score_breakdown", {}).get(
                "recommendation_readiness_score", 0.0
            )
            or 0.0
        )
        packaging_quality_score = float(
            candidate.get("score_breakdown", {}).get("packaging_quality_score", 0.0)
            or 0.0
        )
        story_completeness = max(
            float(
                candidate.get("score_breakdown", {}).get(
                    "story_completeness_score", 0.0
                )
                or 0.0
            ),
            float(signals.get("closure_score", 0.0) or 0.0),
            boundary_conf,
        )
        subject_visibility_ratio = float(
            reframe_debug.get("subject_visibility_ratio", 1.0) or 0.0
        )
        scene_interest_windows = int(
            reframe_debug.get("scene_interest_windows", 0) or 0
        )
        no_subject_windows = int(reframe_debug.get("no_subject_windows", 0) or 0)
        face_edge_clip_rate = float(
            reframe_debug.get("face_edge_clip_rate", 0.0) or 0.0
        )
        dialogue_mode_windows = int(reframe_debug.get("dialogue_mode_windows", 0) or 0)
        speaker_center_offset_avg = float(
            reframe_debug.get("speaker_center_offset_avg", 0.0) or 0.0
        )
        speaker_center_offset_p95 = float(
            reframe_debug.get("speaker_center_offset_p95", 0.0) or 0.0
        )
        speaker_centered_rate = float(
            reframe_debug.get("speaker_centered_rate", 0.0) or 0.0
        )
        speaker_face_centered_windows = int(
            reframe_debug.get("speaker_face_centered_windows", 0) or 0
        )
        dialogue_center_windows = int(
            reframe_debug.get("dialogue_center_windows", 0) or 0
        )
        listener_fallback_windows = int(
            reframe_debug.get("listener_fallback_windows", 0) or 0
        )
        subject_person_fallback_windows = int(
            reframe_debug.get("subject_person_fallback_windows", 0) or 0
        )
        evidence_visible_faces_peak = int(
            reframe_debug.get("evidence_visible_faces_peak", 0) or 0
        )
        evidence_visible_persons_peak = int(
            reframe_debug.get("evidence_visible_persons_peak", 0) or 0
        )
        speaker_to_listener_switches = int(
            reframe_debug.get("speaker_to_listener_switches", 0) or 0
        )
        subject_person_fallback_used = bool(
            reframe_debug.get("subject_person_fallback_used", False)
        )
        subtitle_blackout_count = int(signals.get("subtitle_blackout_count", 0) or 0)
        pause_policy_failed = bool(candidate.get("pause_policy_failed", False))
        publishable_story_override = bool(
            candidate.get("publishable_story_override", False)
            or self._is_story_override_candidate(candidate, subtitle_info)
        )
        story_failure_flags = bool(
            candidate.get("rejected_for_missing_payoff", False)
            or candidate.get("rejected_for_topic_jump", False)
            or candidate.get("rejected_for_confusing_story", False)
        )
        dialogue_flow_score = max(
            float(signals.get("dialogue_flow_score", 0.0) or 0.0),
            float(
                candidate.get("score_breakdown", {}).get("dialogue_exchange_score", 0.0)
                or 0.0
            ),
        )
        dialogue_safe_accept = bool(
            dialogue_flow_score >= 0.42
            and subtitle_quality_score
            >= float(self.cfg.get("subtitle_quality_score_threshold", 0.66)) * 0.9
            and (dialogue_mode_windows > 0 or speaker_centered_rate >= 0.20)
        )
        source_face_presence = float(
            candidate.get("score_breakdown", {}).get("face_presence", 0.0) or 0.0
        )
        source_person_presence = float(
            candidate.get("score_breakdown", {}).get("person_presence", 0.0) or 0.0
        )
        source_subject_presence = float(
            candidate.get("score_breakdown", {}).get("subject_presence", 0.0) or 0.0
        )
        visual_premise_strength = float(
            candidate.get("score_breakdown", {}).get("visual_premise_strength", 0.0)
            or 0.0
        )
        sound_off_hook_score = float(
            candidate.get("score_breakdown", {}).get("sound_off_hook_score", 0.0) or 0.0
        )
        first_second_hook_score = float(
            candidate.get("score_breakdown", {}).get("first_second_hook_score", 0.0)
            or 0.0
        )
        premise_signal_score = float(
            candidate.get("score_breakdown", {}).get("premise_signal_score", 0.0) or 0.0
        )
        cold_open_dead_time_penalty = float(
            candidate.get("score_breakdown", {}).get("cold_open_dead_time_penalty", 0.0)
            or 0.0
        )
        dialogue_dependency_penalty = float(
            candidate.get("score_breakdown", {}).get("dialogue_dependency_penalty", 0.0)
            or 0.0
        )
        face_present_but_lock_failed = bool(
            max(source_face_presence, source_person_presence, source_subject_presence)
            >= 0.18
            and (
                bool(reframe_debug.get("center_safe_fallback_used", False))
                or str(reframe_debug.get("subject_acquisition_state", ""))
                == "no_visible_subject"
                or speaker_face_centered_windows <= 0
                or speaker_centered_rate <= 0.0
            )
        )
        strong_speaker_center = (
            speaker_face_centered_windows >= 3
            and speaker_centered_rate >= 0.32
            and face_edge_clip_rate <= 0.18
        )
        visual_subject_score = float(
            candidate.get("score_breakdown", {}).get("visual_subject_score", 0.0) or 0.0
        )
        reframe_feasibility_score = float(
            candidate.get("score_breakdown", {}).get("reframe_feasibility_score", 0.0)
            or 0.0
        )
        premise_strength = max(
            visual_premise_strength,
            sound_off_hook_score,
            first_second_hook_score,
            premise_signal_score,
        )
        fallback_visual_ok = bool(
            (
                bool(candidate.get("selection_visual_soft_gate", True))
                or publishable_story_override
            )
            and premise_strength >= 0.58
            and (
                story_interest >= 0.60
                or story_completeness >= 0.60
                or watchability_score >= 0.60
            )
        )
        fallback_reframe_used = bool(
            reframe_debug.get("hard_timeout_triggered", False)
            or reframe_debug.get("auto_reframe_retry_used", False)
            or reframe_debug.get("face_preserving_fallback_used", False)
            or reframe_debug.get("dialogue_center_used", False)
            or reframe_debug.get("listener_face_fallback_used", False)
            or reframe_debug.get("subject_person_fallback_used", False)
            or reframe_debug.get("center_safe_fallback_used", False)
        )
        source_visual_evidence = max(
            source_face_presence, source_person_presence, source_subject_presence
        )
        final_crop_visual_evidence = max(
            float(reframe_debug.get("final_crop_face_presence", 0.0) or 0.0),
            float(reframe_debug.get("final_crop_person_presence", 0.0) or 0.0),
            float(reframe_debug.get("final_crop_subject_presence", 0.0) or 0.0),
        )
        final_crop_visual_floor = max(
            float(reframe_debug.get("final_crop_face_presence_min", 0.0) or 0.0),
            float(reframe_debug.get("final_crop_person_presence_min", 0.0) or 0.0),
            float(reframe_debug.get("final_crop_subject_presence_min", 0.0) or 0.0),
        )
        final_crop_probe_used = bool(
            reframe_debug.get("final_crop_visual_probe_used", False)
        )
        final_crop_visual_ok = bool(final_crop_visual_evidence >= 0.18)
        face_signal_present = bool(
            evidence_visible_faces_peak > 0
            or evidence_visible_persons_peak > 0
            or source_face_presence >= 0.08
            or source_person_presence >= 0.08
            or source_subject_presence >= 0.08
        )
        # Core pipeline is accept-first; visual/story quality stays in debug metadata
        # and downstream packaging, but must not block output in the production path.
        return "accept"
        if face_present_but_lock_failed:
            if (
                final_crop_probe_used
                and fallback_reframe_used
                and final_crop_visual_floor < 0.08
            ):
                return "reject_visual"
            if (
                final_crop_visual_ok
                and source_visual_evidence >= 0.50
                and premise_strength >= 0.58
                and (
                    story_interest >= 0.50
                    or story_completeness >= 0.50
                    or watchability_score >= 0.58
                )
            ):
                return "accept"
            if fallback_reframe_used:
                if premise_strength >= 0.72 and source_face_presence < 0.28:
                    return "accept"
                if fallback_visual_ok:
                    return "accept"
                return "reject_visual"
            return "retry_reframe_subject_first"
        if speaker_to_listener_switches > 0 and not bool(
            candidate.get("publishable_story_override", False)
        ):
            if (
                subject_visibility_ratio
                >= float(self.cfg.get("subject_visibility_threshold", 0.46))
                and dialogue_mode_windows > 0
            ):
                return "retry_reframe_subject_first"
        if subject_person_fallback_used and not bool(
            candidate.get("publishable_story_override", False)
        ):
            if dialogue_mode_windows > 0 and subject_visibility_ratio >= float(
                self.cfg.get("subject_visibility_threshold", 0.46)
            ):
                return "retry_reframe_subject_first"
        if (
            speaker_center_offset_p95
            > float(self.cfg.get("speaker_center_max_offset", 0.18)) * 1.18
            and not strong_speaker_center
            or (
                speaker_center_offset_avg
                > float(self.cfg.get("speaker_center_max_offset", 0.18)) * 0.92
                and not strong_speaker_center
            )
            or (
                speaker_centered_rate > 0.0
                and speaker_centered_rate < 0.58
                and (
                    dialogue_center_windows > 0
                    or listener_fallback_windows > 0
                    or subject_person_fallback_windows > 0
                )
            )
        ) and not bool(candidate.get("publishable_story_override", False)):
            return "retry_reframe_subject_first"
        if (
            no_subject_windows > 0
            and subject_visibility_ratio
            < float(self.cfg.get("subject_visibility_threshold", 0.46))
            and not publishable_story_override
        ):
            return "reject_visual"
        if scene_interest_windows >= 3 or no_subject_windows >= 2:
            return "retry_reframe_subject_first"
        if face_edge_clip_rate > 0.30:
            return "retry_reframe_subject_first"
        if (
            visual_subject_score
            < float(self.cfg.get("final_visual_subject_hard_floor", 0.24))
            or reframe_feasibility_score
            < float(self.cfg.get("final_reframe_hard_floor", 0.18))
        ) and not publishable_story_override:
            return "reject_visual"
        if (
            final_crop_probe_used
            and fallback_reframe_used
            and final_crop_visual_floor < 0.08
            and not publishable_story_override
        ):
            return "reject_visual"
        if cold_open_dead_time_penalty > 0.0 and not bool(
            candidate.get("cold_open_recut_applied", False)
        ):
            return "reject_story"
        if (
            max(
                visual_premise_strength,
                sound_off_hook_score,
                first_second_hook_score,
                premise_signal_score,
            )
            < float(self.cfg.get("visual_premise_threshold", 0.48))
            and not publishable_story_override
        ):
            return "reject_story"
        if (
            dialogue_dependency_penalty > 0.42
            and max(
                visual_premise_strength, sound_off_hook_score, first_second_hook_score
            )
            < 0.66
            and not publishable_story_override
        ):
            return "reject_story"
        subtitle_needs_retry = bool(
            text_sanity < float(self.cfg.get("subtitle_text_sanity_threshold", 0.62))
            or subtitle_visual_drop_count > 0
            or subtitle_phrase_clear_count > 0
            or subtitle_hold_too_long
            or subtitle_blackout_count > 0
        )
        if subtitle_needs_retry and not dialogue_safe_accept:
            return "retry_subtitle_enhanced"
        if (
            subtitle_confidence
            < float(self.cfg.get("subtitle_confidence_threshold", 0.76))
            and not publishable_story_override
            and not dialogue_safe_accept
        ):
            return "retry_subtitle_enhanced"
        if (
            subtitle_quality_score
            < float(self.cfg.get("subtitle_quality_score_threshold", 0.66))
            and not publishable_story_override
            and not dialogue_safe_accept
        ):
            return "retry_subtitle_enhanced"
        if pause_policy_failed:
            return "reject_story"
        if (
            watchability_score < float(self.cfg.get("watchability_threshold", 0.54))
            and not dialogue_safe_accept
        ):
            return "reject_story"
        if (
            bool(self.cfg.get("recommendation_readiness_enabled", True))
            and recommendation_readiness_score
            < float(self.cfg.get("recommendation_readiness_threshold", 0.56))
            and not dialogue_safe_accept
        ):
            return "reject_story"
        if (
            packaging_quality_score
            < float(self.cfg.get("packaging_quality_threshold", 0.52))
            and not dialogue_safe_accept
        ):
            return "reject_story"
        if (
            story_interest
            < float(self.cfg.get("interestingness_threshold", 0.52)) * 0.88
            and not dialogue_safe_accept
        ):
            return "reject_story"
        if (
            story_completeness < float(self.cfg.get("min_story_payoff_score", 0.40))
            and boundary_conf
            < float(self.cfg.get("story_boundary_confidence_threshold", 0.58))
            and not dialogue_safe_accept
        ):
            return "reject_story"
        if (
            boundary_conf
            < float(self.cfg.get("story_boundary_confidence_threshold", 0.58))
            and not dialogue_safe_accept
        ):
            return "expand_story_boundary"
        if (
            float(
                candidate.get("score_breakdown", {}).get("dialogue_exchange_score", 0.0)
                or 0.0
            )
            >= 0.40
            and dialogue_mode_windows <= 0
            and subject_visibility_ratio < 0.64
        ):
            return "retry_reframe_subject_first"
        return "accept"

    def _retry_reframe_cfg(self):
        cfg = dict(self.cfg)
        cfg["reframe_priority"] = "stability_first"
        cfg["reframe_transition_mode"] = (
            "hard_switch"
            if bool(
                cfg.get(
                    "speaker_center_strict_mode",
                    self.cfg.get("speaker_center_strict_mode", True),
                )
            )
            else "smooth"
        )
        cfg["speaker_lock_mode"] = "state_machine"
        cfg["empty_frame_guard_enabled"] = True
        cfg["subject_detector_pass"] = str(
            cfg.get("active_speaker_refine_profile", "final_clip_strong")
            or "final_clip_strong"
        )
        cfg["face_detection_fps"] = max(5, int(cfg.get("face_detection_fps", 3)))
        cfg["crop_window_sec"] = min(float(cfg.get("crop_window_sec", 0.8)), 0.66)
        cfg["reframe_track_count_limit"] = max(
            4, int(cfg.get("reframe_track_count_limit", 3))
        )
        cfg["speaker_switch_hold_windows"] = (
            0
            if bool(
                cfg.get(
                    "speaker_center_strict_mode",
                    self.cfg.get("speaker_center_strict_mode", True),
                )
            )
            else 1
        )
        cfg["reframe_switch_confirm_windows"] = 1
        cfg["reframe_lost_face_hold_seconds"] = max(
            2.6, float(cfg.get("reframe_lost_face_hold_seconds", 2.2))
        )
        cfg["crop_window_sec"] = max(0.66, float(cfg.get("crop_window_sec", 0.8)))
        cfg["reframe_anchor_mode"] = (
            "stable_primary"
            if bool(
                cfg.get(
                    "speaker_center_strict_mode",
                    self.cfg.get("speaker_center_strict_mode", True),
                )
            )
            else (
                "dialogue_center"
                if bool(cfg.get("reframe_allow_wide_dialogue_center", True))
                else str(cfg.get("reframe_anchor_mode", "stable_primary"))
            )
        )
        cfg["shot_reacquire_boost_windows"] = max(
            3, int(cfg.get("shot_reacquire_boost_windows", 2))
        )
        cfg["new_face_fast_acquire_threshold"] = min(
            0.74, float(cfg.get("new_face_fast_acquire_threshold", 0.78))
        )
        return normalize_config(cfg)

    def _transcribe_full_episode(self, video_path: str) -> dict:
        """
        Extract and transcribe full episode audio.
        
        NEW (2026-06-14): Required for story-centric mode to build story chains
        before window selection.
        
        Returns:
            subtitle_info dict with 'segments' field containing full episode transcription.
            Returns empty dict if transcription fails.
        """
        from pathlib import Path
        import json
        from pipeline.audio_analysis import extract_audio_to_wav
        from pipeline.subtitle import transcribe_segment
        
        # Check cache first
        cache_path = Path(video_path).with_suffix('.subtitle_cache.json')
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                    if cached.get('segments'):
                        return cached
            except Exception:
                pass  # Cache read failed, proceed with transcription
        
        # Extract full episode audio to WAV
        temp_dir = Path(self.cfg.get('output_folder', 'output')) / '_temp_episode_audio'
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        episode_name = Path(video_path).stem
        wav_path = temp_dir / f"{episode_name}_full.wav"
        
        try:
            extract_audio_to_wav(video_path, str(wav_path))
        except Exception as e:
            print(f"[WARNING] Failed to extract episode audio: {e}")
            return {'segments': [], 'line_count': 0, 'confidence': 0.0}
        
        # Transcribe full episode
        try:
            subtitle_info = transcribe_segment(
                str(wav_path),
                out_dir=str(temp_dir),
                idx=0,  # Episode-level (not candidate-specific)
                cfg=self.cfg
            )
        except Exception as e:
            print(f"[WARNING] Failed to transcribe episode: {e}")
            return {'segments': [], 'line_count': 0, 'confidence': 0.0}
        
        # Cache result to disk for future runs
        if subtitle_info.get('segments'):
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(subtitle_info, f, ensure_ascii=False, indent=2)
            except Exception:
                pass  # Cache write failed, not critical
        
        # Clean up WAV file (keep cache)
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass
        
        return subtitle_info
    
    def _candidate_windows(self, video_path: str):
        """Generate candidate windows for story extraction.
        
        NEW (2026-06-14): Story-centric mode uses story_pipeline when enabled.
        Legacy temporal window mode kept as fallback.
        """
        # Feature flag for story-centric mode
        use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", False))
        
        if use_story_pipeline:
            return self._candidate_windows_story_centric(video_path)
        else:
            return self._candidate_windows_legacy(video_path)
    
    def _candidate_windows_story_centric(self, video_path: str):
        """NEW: Story-centric candidate window generation using story_pipeline.
        
        Returns windows that correspond to complete story chains rather than
        arbitrary temporal segments.
        """
        duration = probe_video(video_path)[1]
        
        # Get subtitle info for the video
        # This should come from transcription/subtitle analysis
        subtitle_info = getattr(self, 'subtitle_info', None)
        if not subtitle_info or not subtitle_info.get('segments'):
            # No subtitle data available - fallback to legacy
            return self._candidate_windows_legacy(video_path)
        
        # Build story chains from the entire episode
        story_chains = build_story_chains_for_episode(
            subtitle_info,
            cfg=self.cfg,
            source_id=video_path
        )
        
        if not story_chains:
            # Fallback to legacy if no story chains found
            return self._candidate_windows_legacy(video_path)
        
        # Convert story chains to candidate windows
        windows = []
        seen = set()
        
        for chain in story_chains:
            # Convert story chain to candidate dict
            candidate = story_chain_to_candidate(chain, source="story_pipeline")
            
            start = float(candidate.get("start", 0.0) or 0.0)
            end = float(candidate.get("end", duration) or duration)
            source = str(candidate.get("source", "story_pipeline") or "story_pipeline")
            
            # Validate window bounds
            if end <= start or start < 0:
                continue
            
            # Check minimum duration
            min_duration = max(35.0, float(self.cfg.get("min_candidate_seconds", 35)))
            if end - start < min_duration:
                continue
            
            # Deduplicate
            key = (round(start, 1), round(end, 1))
            if key in seen:
                continue
            seen.add(key)
            
            windows.append((round(start, 3), round(end, 3), source))
        
        # If no valid windows, fallback to legacy
        if not windows:
            return self._candidate_windows_legacy(video_path)
        
        return windows
    
    def _candidate_windows_legacy(self, video_path: str):
        """LEGACY: Temporal window generation based on scene detection.
        
        This is the original implementation. Will be deprecated after
        story-centric migration is complete.
        """
        duration = probe_video(video_path)[1]
        windows, seen = [], set()
        scenes = []
        for start, end in detect_scenes(video_path):
            end = duration if end is None else end
            if end > start:
                scenes.append((float(start), float(end)))
        max_window = max(
            float(self.cfg.get("candidate_window_seconds", 45)),
            min(float(self.cfg.get("allow_story_extension_seconds", 60)), 60.0),
        )
        min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
        step = float(self.cfg.get("candidate_step_seconds", 20))

        if scenes:
            index = 0
            while index < len(scenes):
                start = scenes[index][0]
                end = scenes[index][1]
                next_index = index
                while next_index + 1 < len(scenes):
                    proposed_end = scenes[next_index + 1][1]
                    if proposed_end - start > max_window:
                        break
                    end = proposed_end
                    next_index += 1
                    if end - start >= min_story:
                        break
                windows.append((start, end, "scene_cluster"))
                index = max(next_index + 1, index + 1)

        if duration > 0 and not windows:
            cursor = 0.0
            while cursor < duration:
                windows.append(
                    (cursor, min(duration, cursor + max_window), "global_scan")
                )
                cursor += max(step, max_window * 0.85)
        result = []
        for start, end, source in windows:
            if end - start < max(
                35.0, float(self.cfg.get("min_candidate_seconds", 35))
            ):
                continue
            key = (round(start, 1), round(end, 1))
            if key in seen:
                continue
            seen.add(key)
            result.append((round(start, 3), round(end, 3), source))
        if not result and duration >= min_story:
            result.append((0.0, round(duration, 3), "short_fallback"))
        return result

    def _extract_audio_summary(self, video_path: str, start: float, end: float):
        duration = max(0.1, end - start)
        summary_key = (str(video_path), round(float(start), 3), round(float(end), 3))
        cached_summary = self._audio_summary_cache.get(summary_key)
        if cached_summary is not None:
            self._audio_cache_stats["audio_summary_cache_hits"] = (
                self._audio_cache_stats.get("audio_summary_cache_hits", 0) + 1
            )
            return dict(cached_summary)
        episode_wav, segment_wav, summary_json = _audio_summary_cache_paths(
            video_path, start, end, self.cfg
        )
        cached = self._load_cached_audio_summary(summary_json)
        if cached is not None:
            self._audio_summary_cache[summary_key] = dict(cached)
            return cached
        wav_path = None
        tmp_dir = None
        try:
            if not episode_wav.exists() or episode_wav.stat().st_size <= 0:
                wav_path = self._ensure_episode_audio_wav(video_path)
            else:
                wav_path = str(episode_wav)
            if not wav_path or not os.path.exists(wav_path):
                tmp_dir = tempfile.mkdtemp(prefix="sf_audio_")
                fallback_wav = os.path.join(tmp_dir, "segment.wav")
                run_ffmpeg(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        video_path,
                        "-ss",
                        str(start),
                        "-to",
                        str(end),
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-vn",
                        fallback_wav,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                    ],
                    timeout=180,
                )
                wav_path = fallback_wav if os.path.exists(fallback_wav) else None
            else:
                if not segment_wav.exists() or segment_wav.stat().st_size <= 0:
                    if _write_wav_segment(wav_path, start, end, str(segment_wav)):
                        wav_path = str(segment_wav)
                    else:
                        tmp_dir = tempfile.mkdtemp(prefix="sf_audio_")
                        fallback_wav = os.path.join(tmp_dir, "segment.wav")
                        run_ffmpeg(
                            [
                                "ffmpeg",
                                "-y",
                                "-i",
                                video_path,
                                "-ss",
                                str(start),
                                "-to",
                                str(end),
                                "-ac",
                                "1",
                                "-ar",
                                "16000",
                                "-vn",
                                fallback_wav,
                                "-hide_banner",
                                "-loglevel",
                                "error",
                            ],
                            timeout=180,
                        )
                        wav_path = (
                            fallback_wav if os.path.exists(fallback_wav) else None
                        )
                else:
                    wav_path = str(segment_wav)

            if not wav_path or not os.path.exists(wav_path):
                return {
                    "speech_density": 0.0,
                    "silence_ratio": 1.0,
                    "audio_energy": 0.0,
                    "voiced_intervals": [],
                    "turns": [],
                    "pause_timeline": [],
                }

            speech = speech_density(wav_path)
            rms = compute_rms(wav_path)
            try:
                energy = min(1.0, (statistics.mean(rms) / 1500.0)) if rms else 0.0
            except Exception:
                energy = 0.0
            silences = detect_silence_ffmpeg(
                wav_path, silence_thresh_db=-40, min_silence_len=0.55
            )
            silence_ratio = min(
                1.0,
                sum(
                    max(0.0, item_end - item_start) for item_start, item_end in silences
                )
                / duration,
            )
            voiced = _safe_voiced_intervals(wav_path, self.cfg)
            turns = _merge_intervals(
                voiced, max_gap=float(self.cfg.get("story_merge_gap_seconds", 1.0))
            )
            pcm = np.array([], dtype=np.int16)
            sample_rate = 16000
            try:
                with wave.open(wav_path, "rb") as handle:
                    sample_rate = int(handle.getframerate() or 16000)
                    pcm = np.frombuffer(
                        handle.readframes(handle.getnframes()), dtype=np.int16
                    )
            except Exception:
                pcm = np.array([], dtype=np.int16)
            pause_timeline = _build_pause_timeline(
                voiced,
                pcm,
                sample_rate,
                self.cfg,
                detected_silences=silences,
                total_duration=duration,
            )
            summary = {
                "speech_density": round(speech, 4),
                "silence_ratio": round(silence_ratio, 4),
                "audio_energy": round(energy, 4),
                "voiced_intervals": voiced,
                "turns": turns,
                "pause_timeline": pause_timeline,
                **_pause_timeline_stats(pause_timeline),
            }
            self._audio_summary_cache[summary_key] = dict(summary)
            self._store_cached_audio_summary(summary_json, summary)
            return summary
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _build_story_candidates_from_window(
        self, window_start: float, window_end: float, source: str, summary: dict
    ):
        turns = summary.get("turns", [])
        if len(turns) < 1 and float(summary.get("speech_density", 0.0) or 0.0) < 0.18:
            return []
        min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
        target_story = max(
            45.0,
            float(
                self.cfg.get(
                    "story_soft_max_seconds", self.cfg.get("target_story_seconds", 45)
                )
            ),
        )
        max_story = min(
            60.0,
            float(
                self.cfg.get(
                    "story_hard_max_seconds",
                    self.cfg.get("allow_story_extension_seconds", 60),
                )
            ),
        )
        left_pad = float(self.cfg.get("context_left_pad_seconds", 2.0))
        right_pad = float(self.cfg.get("context_right_pad_seconds", 1.4))
        gap_limit = float(self.cfg.get("story_merge_gap_seconds", 1.0))
        keep_gap = float(self.cfg.get("keep_dialogue_gap_seconds", 1.0))
        extension_gap = min(
            float(
                self.cfg.get("story_extension_max_pause_seconds", max(keep_gap, 1.15))
            ),
            keep_gap + 0.15,
        )
        candidates = []
        seen = set()

        for index in range(len(turns)):
            first_turn = turns[index][0]
            last_turn = turns[index][1]
            end_index = index
            while end_index + 1 < len(turns):
                next_start, next_end = turns[end_index + 1]
                candidate_start = max(window_start, first_turn - left_pad)
                candidate_end = min(window_end, next_end + right_pad)
                if next_start - last_turn > gap_limit and not bool(
                    self.cfg.get("story_continue_after_silence", True)
                ):
                    break
                if next_start - last_turn > max(gap_limit, extension_gap):
                    break
                if candidate_end - candidate_start > max_story:
                    break
                end_index += 1
                last_turn = turns[end_index][1]

            for cluster_end in range(index, end_index + 1):
                candidate_start = max(window_start, turns[index][0] - left_pad)
                candidate_end = min(window_end, turns[cluster_end][1] + right_pad)
                duration = candidate_end - candidate_start
                if duration < max(12.0, min_story * 0.5):
                    continue
                if duration < min_story and cluster_end + 1 < len(turns):
                    extra_end = min(window_end, turns[cluster_end + 1][1] + right_pad)
                    if extra_end - candidate_start <= max_story and turns[
                        cluster_end + 1
                    ][0] - turns[cluster_end][1] <= max(gap_limit, extension_gap):
                        candidate_end = extra_end
                        duration = candidate_end - candidate_start
                        cluster_end += 1
                key = (round(candidate_start, 2), round(candidate_end, 2))
                if key in seen:
                    continue
                seen.add(key)
                turns_subset = turns[index : cluster_end + 1]
                if len(turns_subset) < 1:
                    continue
                speech_coverage = sum(
                    item_end - item_start for item_start, item_end in turns_subset
                ) / max(0.001, duration)
                hook_gap = max(0.0, turns_subset[0][0] - candidate_start)
                tail_gap = max(0.0, candidate_end - turns_subset[-1][1])
                duration_fit = max(
                    0.0, 1.0 - (abs(duration - target_story) / max(target_story, 1.0))
                )
                continuation_bonus = (
                    0.08
                    if bool(self.cfg.get("story_extension_bonus_enabled", False))
                    and len(turns_subset) >= 2
                    and tail_gap <= max(right_pad + 0.8, extension_gap)
                    else 0.0
                )
                story_clarity = (
                    min(1.0, speech_coverage / 0.55) * 0.34
                    + min(1.0, len(turns_subset) / 4.0) * 0.22
                    + max(
                        0.0,
                        1.0
                        - (
                            hook_gap
                            / max(
                                0.5, float(self.cfg.get("hook_max_lead_seconds", 4.5))
                            )
                        ),
                    )
                    * 0.24
                    + min(1.0, duration_fit + 0.15) * 0.20
                    + continuation_bonus
                )
                fast_score = (
                    summary["speech_density"] * 0.28
                    + (1.0 - summary["silence_ratio"]) * 0.16
                    + summary["audio_energy"] * 0.10
                    + min(1.0, speech_coverage / 0.55) * 0.18
                    + min(1.0, len(turns_subset) / 4.0) * 0.14
                    + max(0.0, 1.0 - hook_gap / 6.0) * 0.08
                    + duration_fit * 0.06
                )
                if summary["silence_ratio"] > 0.72:
                    fast_score -= 0.1
                candidates.append(
                    {
                        "start": round(candidate_start, 3),
                        "end": round(candidate_end, 3),
                        "window_start": window_start,
                        "window_end": window_end,
                        "source": source,
                        "estimated_turns": len(turns_subset),
                        "hook_gap": round(hook_gap, 3),
                        "tail_gap": round(tail_gap, 3),
                        "story_continued_after_pause": bool(continuation_bonus > 0.0),
                        "story_unit_type": "dialogue_cluster",
                        "speech_coverage": round(speech_coverage, 4),
                        "story_clarity_score": round(story_clarity, 4),
                        "score": round(fast_score, 4),
                        "score_breakdown": {
                            "speech_density": summary["speech_density"],
                            "silence_ratio": summary["silence_ratio"],
                            "audio_energy": summary["audio_energy"],
                            "speech_coverage": round(speech_coverage, 4),
                            "estimated_turns": len(turns_subset),
                            "hook_gap": round(hook_gap, 4),
                            "story_clarity_score": round(story_clarity, 4),
                            "story_context_score": round(
                                continuation_bonus
                                + min(1.0, len(turns_subset) / 5.0) * 0.18,
                                4,
                            ),
                            "duration": round(duration, 4),
                            "source": source,
                        },
                    }
                )
        return candidates

    def _build_story_candidates_from_turns_linear(
        self, window_start: float, window_end: float, source: str, summary: dict
    ):
        turns = summary.get("turns", [])
        if len(turns) < 1 and float(summary.get("speech_density", 0.0) or 0.0) < 0.18:
            return []
        min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
        target_story = max(
            45.0,
            float(
                self.cfg.get(
                    "story_soft_max_seconds", self.cfg.get("target_story_seconds", 45)
                )
            ),
        )
        max_story = min(
            60.0,
            float(
                self.cfg.get(
                    "story_hard_max_seconds",
                    self.cfg.get("allow_story_extension_seconds", 60),
                )
            ),
        )
        left_pad = float(self.cfg.get("context_left_pad_seconds", 2.0))
        right_pad = float(self.cfg.get("context_right_pad_seconds", 1.4))
        gap_limit = float(self.cfg.get("story_merge_gap_seconds", 1.0))
        keep_gap = float(self.cfg.get("keep_dialogue_gap_seconds", 1.0))
        extension_gap = min(
            float(
                self.cfg.get("story_extension_max_pause_seconds", max(keep_gap, 1.15))
            ),
            keep_gap + 0.15,
        )
        built = []
        seen = set()
        index = 0

        while index < len(turns):
            cluster_start = turns[index][0]
            end_index = index
            while end_index + 1 < len(turns):
                next_gap = turns[end_index + 1][0] - turns[end_index][1]
                if next_gap > gap_limit and not bool(
                    self.cfg.get("story_continue_after_silence", True)
                ):
                    break
                if next_gap > max(gap_limit, extension_gap):
                    break
                candidate_start = max(window_start, cluster_start - left_pad)
                candidate_end = min(window_end, turns[end_index + 1][1] + right_pad)
                if candidate_end - candidate_start > max_story:
                    break
                end_index += 1
                if candidate_end - candidate_start >= target_story:
                    break

            candidate_start = max(window_start, cluster_start - left_pad)
            candidate_end = min(window_end, turns[end_index][1] + right_pad)
            if candidate_end - candidate_start < min_story:
                probe = end_index
                while probe + 1 < len(turns):
                    next_gap = turns[probe + 1][0] - turns[probe][1]
                    if next_gap > gap_limit and not bool(
                        self.cfg.get("story_continue_after_silence", True)
                    ):
                        break
                    if next_gap > max(gap_limit, extension_gap):
                        break
                    proposed_end = min(window_end, turns[probe + 1][1] + right_pad)
                    if proposed_end - candidate_start > max_story:
                        break
                    probe += 1
                    candidate_end = proposed_end
                    end_index = probe
                    if candidate_end - candidate_start >= min_story:
                        break

            duration = candidate_end - candidate_start
            if duration >= max(35.0, float(self.cfg.get("min_candidate_seconds", 35))):
                key = (round(candidate_start, 2), round(candidate_end, 2))
                if key not in seen:
                    seen.add(key)
                    turns_subset = turns[index : end_index + 1]
                    if len(turns_subset) < 1:
                        index += 1
                        continue
                    speech_coverage = sum(
                        item_end - item_start for item_start, item_end in turns_subset
                    ) / max(0.001, duration)
                    hook_gap = max(0.0, turns_subset[0][0] - candidate_start)
                    duration_fit = max(
                        0.0,
                        1.0 - (abs(duration - target_story) / max(target_story, 1.0)),
                    )
                    continuation_bonus = (
                        0.08
                        if bool(self.cfg.get("story_extension_bonus_enabled", False))
                        and len(turns_subset) >= 2
                        else 0.0
                    )
                    story_clarity = (
                        min(1.0, speech_coverage / 0.55) * 0.38
                        + min(1.0, len(turns_subset) / 4.0) * 0.22
                        + max(
                            0.0,
                            1.0
                            - (
                                hook_gap
                                / max(
                                    0.5,
                                    float(self.cfg.get("hook_max_lead_seconds", 4.5)),
                                )
                            ),
                        )
                        * 0.20
                        + duration_fit * 0.20
                        + continuation_bonus
                    )
                    
                    # PHASE 3C: Extract candidate-local subtitle segments for turn-first speaker switching
                    candidate_subtitle_segments = []
                    if hasattr(self, 'subtitle_info') and self.subtitle_info:
                        full_segments = list(self.subtitle_info.get("segments", []) or [])
                        for seg in full_segments:
                            seg_start = float(seg.get("start", 0))
                            seg_end = float(seg.get("end", 0))
                            # Include segments that overlap with candidate time range
                            if seg_end > candidate_start and seg_start < candidate_end:
                                # Create candidate-relative copy
                                local_seg = dict(seg)
                                # Adjust timestamps to be relative to candidate start
                                local_seg["start"] = max(0.0, seg_start - candidate_start)
                                local_seg["end"] = max(0.0, seg_end - candidate_start)
                                candidate_subtitle_segments.append(local_seg)
                    
                    built.append(
                        {
                            "start": round(candidate_start, 3),
                            "end": round(candidate_end, 3),
                            "window_start": window_start,
                            "window_end": window_end,
                            "source": source,
                            "estimated_turns": len(turns_subset),
                            "hook_gap": round(hook_gap, 3),
                            "tail_gap": round(
                                max(0.0, candidate_end - turns_subset[-1][1]), 3
                            ),
                            "story_continued_after_pause": bool(
                                continuation_bonus > 0.0
                            ),
                            "story_unit_type": "dialogue_linear",
                            "speech_coverage": round(speech_coverage, 4),
                            "story_clarity_score": round(story_clarity, 4),
                            "score": round(
                                story_clarity * 0.74 + summary["speech_density"] * 0.26,
                                4,
                            ),
                            "score_breakdown": {
                                "speech_density": summary["speech_density"],
                                "silence_ratio": summary["silence_ratio"],
                                "audio_energy": summary["audio_energy"],
                                "speech_coverage": round(speech_coverage, 4),
                                "estimated_turns": len(turns_subset),
                                "hook_gap": round(hook_gap, 4),
                                "story_clarity_score": round(story_clarity, 4),
                                "story_context_score": round(
                                    continuation_bonus
                                    + min(1.0, len(turns_subset) / 5.0) * 0.18,
                                    4,
                                ),
                                "duration": round(duration, 4),
                                "source": source,
                            },
                            "subtitle_segments": candidate_subtitle_segments,  # PHASE 3C: Turn-first data
                        }
                    )
            advance = max(1, (end_index - index + 1) // 2)
            index += advance
        return built

    def _fallback_window_candidate(
        self, window_start: float, window_end: float, source: str, summary: dict
    ):
        duration = max(0.1, window_end - window_start)
        speech_density_value = float(summary.get("speech_density", 0.0))
        silence_ratio = float(summary.get("silence_ratio", 1.0))
        if speech_density_value < 0.18 or duration < max(
            35.0, float(self.cfg.get("min_candidate_seconds", 35))
        ):
            return None
        hook_gap = min(
            float(self.cfg.get("hook_max_lead_seconds", 4.5)), duration * 0.08
        )
        turns = list(summary.get("turns", []) or [])
        dialogue_like = (
            len(turns) >= 2
            or speech_density_value >= 0.26
            or float(summary.get("audio_energy", 0.0) or 0.0) >= 0.18
        )
        story_unit_type = "dialogue_cluster" if dialogue_like else "fallback_window"
        source_label = source if dialogue_like else f"{source}_fallback"
        story_clarity = (
            min(1.0, speech_density_value / 0.30) * 0.40
            + (1.0 - silence_ratio) * 0.25
            + min(
                1.0,
                duration / max(float(self.cfg.get("target_story_seconds", 45)), 1.0),
            )
            * 0.20
            + min(1.0, len(turns) / 3.0) * 0.15
        )
        
        # PHASE 3C: Extract candidate-local subtitle segments for turn-first speaker switching
        candidate_subtitle_segments = []
        if hasattr(self, 'subtitle_info') and self.subtitle_info:
            full_segments = list(self.subtitle_info.get("segments", []) or [])
            for seg in full_segments:
                seg_start = float(seg.get("start", 0))
                seg_end = float(seg.get("end", 0))
                # Include segments that overlap with candidate time range
                if seg_end > window_start and seg_start < window_end:
                    # Create candidate-relative copy
                    local_seg = dict(seg)
                    # Adjust timestamps to be relative to candidate start
                    local_seg["start"] = max(0.0, seg_start - window_start)
                    local_seg["end"] = max(0.0, seg_end - window_start)
                    candidate_subtitle_segments.append(local_seg)
        
        return {
            "start": round(window_start, 3),
            "end": round(window_end, 3),
            "window_start": window_start,
            "window_end": window_end,
            "source": source_label,
            "estimated_turns": max(1, len(turns)),
            "hook_gap": round(hook_gap, 3),
            "tail_gap": 0.5,
            "story_unit_type": story_unit_type,
            "speech_coverage": round(min(1.0, speech_density_value * 1.2), 4),
            "story_clarity_score": round(story_clarity, 4),
            "score": round(story_clarity * 0.75 + speech_density_value * 0.25, 4),
            "score_breakdown": {
                "speech_density": round(speech_density_value, 4),
                "silence_ratio": round(silence_ratio, 4),
                "audio_energy": round(float(summary.get("audio_energy", 0.0)), 4),
                "speech_coverage": round(min(1.0, speech_density_value * 1.2), 4),
                "estimated_turns": max(1, len(turns)),
                "hook_gap": round(hook_gap, 4),
                "story_clarity_score": round(story_clarity, 4),
                "story_context_score": round(min(1.0, len(turns) / 4.0) * 0.20, 4),
                "duration": round(duration, 4),
                "source": source_label,
                "story_unit_type": story_unit_type,
            },
            "subtitle_segments": candidate_subtitle_segments,  # PHASE 3C: Turn-first data
        }

    def _score_story_candidate(self, video_path: str, candidate: dict):
        import time
        _timings = {}
        _total_start = time.perf_counter()
        
        start, end = candidate["start"], candidate["end"]
        duration = max(0.1, end - start)
        summary = {
            "speech_density": float(
                candidate["score_breakdown"].get("speech_density", 0.0)
            ),
            "silence_ratio": float(
                candidate["score_breakdown"].get("silence_ratio", 1.0)
            ),
            "audio_energy": float(
                candidate["score_breakdown"].get("audio_energy", 0.0)
            ),
        }
        duration_policy = self._candidate_duration_policy(candidate)
        candidate["duration_policy"] = dict(duration_policy)
        min_publishable_seconds = float(
            duration_policy.get(
                "min_publishable_seconds", self.cfg.get("min_publishable_seconds", 35)
            )
            or self.cfg.get("min_publishable_seconds", 35)
        )
        
        # Face detection timing
        _t0 = time.perf_counter()
        faces = sample_face_focus_stats(
            video_path,
            start,
            end,
            sample_fps=int(self.cfg.get("face_detection_fps", 2)),
            detector_profile=str(self.cfg.get("active_speaker_scan_profile", "light")),
        )
        _timings["face_detection_sec"] = round(time.perf_counter() - _t0, 3)
        
        # Video metrics timing
        _t0 = time.perf_counter()
        video = _video_metrics(video_path, start, end)
        _timings["video_metrics_sec"] = round(time.perf_counter() - _t0, 3)
        dialogue_density = min(1.0, candidate.get("speech_coverage", 0.0) / 0.55)
        hook_score = max(
            0.0,
            1.0
            - (
                candidate.get("hook_gap", 0.0)
                / max(0.5, float(self.cfg.get("hook_max_lead_seconds", 4.5)))
            ),
        )
        duration_fit = max(
            0.0,
            1.0
            - abs(
                duration
                - float(
                    duration_policy.get(
                        "target_seconds", self.cfg.get("target_story_seconds", 45)
                    )
                )
            )
            / max(
                1.0,
                float(
                    duration_policy.get(
                        "target_seconds", self.cfg.get("target_story_seconds", 45)
                    )
                ),
            ),
        )
        development_score = (
            min(1.0, candidate.get("speech_coverage", 0.0) / 0.55) * 0.55
            + min(1.0, candidate.get("estimated_turns", 0) / 4.0) * 0.45
        )
        closure_score = min(1.0, max(0.0, 1.0 - (candidate.get("tail_gap", 0.0) / 2.1)))
        pause_cut_count = int(
            candidate["score_breakdown"].get("pause_cut_count", 0) or 0
        )
        pause_story_keep_count = int(
            candidate["score_breakdown"].get("pause_story_keep_count", 0) or 0
        )
        pause_soft_keep_count = int(
            candidate["score_breakdown"].get("pause_soft_keep_count", 0) or 0
        )
        pause_cut_seconds_total = float(
            candidate["score_breakdown"].get("pause_cut_seconds_total", 0.0) or 0.0
        )
        cold_open_window_seconds = float(
            self.cfg.get("cold_open_window_seconds", 3.0) or 3.0
        )
        cold_open_dead_time_threshold = float(
            self.cfg.get("cold_open_dead_time_threshold_seconds", 0.45) or 0.45
        )
        story_context_score = float(
            candidate["score_breakdown"].get("story_context_score", 0.0) or 0.0
        )
        subtitle_confidence = float(
            candidate["score_breakdown"].get("subtitle_confidence", 0.0) or 0.0
        )
        subtitle_text_sanity = float(
            candidate["score_breakdown"].get("subtitle_text_sanity_score", 0.0) or 0.0
        )
        subtitle_language_consistency = float(
            candidate["score_breakdown"].get("subtitle_language_consistency", 0.0)
            or 0.0
        )
        subtitle_quality_score = float(
            candidate["score_breakdown"].get(
                "subtitle_quality_score",
                min(
                    1.0,
                    subtitle_confidence * 0.34
                    + subtitle_text_sanity * 0.38
                    + subtitle_language_consistency * 0.18,
                ),
            )
            or 0.0
        )
        visual_subject_score = min(
            1.0,
            float(faces.get("face_presence", 0.0)) * 0.56
            + float(faces.get("person_presence", 0.0)) * 0.28
            + min(1.0, float(faces.get("avg_face_size", 0.0)) / 0.035) * 0.10
            + min(1.0, float(faces.get("avg_person_size", 0.0)) / 0.09) * 0.06,
        )
        reframe_feasibility_score = min(
            1.0,
            float(faces.get("subject_presence", 0.0)) * 0.52
            + visual_subject_score * 0.24
            + min(1.0, video["motion"] / 0.18) * 0.08
            + min(1.0, video["brightness"] / 0.18) * 0.06
            + min(1.0, summary["audio_energy"] / 0.40) * 0.10,
        )
        empty_frame_risk = max(
            0.0,
            1.0
            - (
                float(faces.get("subject_presence", 0.0)) * 0.9
                + visual_subject_score * 0.45
            ),
        )
        face_evidence_score = min(
            1.0,
            float(faces.get("face_presence", 0.0)) * 0.62
            + float(faces.get("person_presence", 0.0)) * 0.22
            + float(faces.get("subject_presence", 0.0)) * 0.16,
        )
        hook_strength = hook_score
        curiosity_gap_score = max(
            0.0,
            min(
                1.0,
                hook_score * 0.46
                + dialogue_density * 0.14
                + min(1.0, candidate.get("estimated_turns", 0) / 4.0) * 0.18
                + story_context_score * 0.10
                + max(0.0, 1.0 - summary["silence_ratio"]) * 0.12,
            ),
        )
        payoff_strength = min(
            1.0,
            closure_score * 0.72
            + min(1.0, candidate.get("speech_coverage", 0.0) / 0.58) * 0.16
            + (0.12 if candidate.get("story_continued_after_pause") else 0.0),
        )
        story_interest_score = (
            hook_score * 0.24
            + development_score * 0.16
            + min(1.0, summary["audio_energy"] / 0.45) * 0.10
            + min(1.0, video["motion"] / 0.20) * 0.08
            + min(1.0, candidate.get("estimated_turns", 0) / 4.0) * 0.16
            + story_context_score * 0.10
            + max(0.0, 1.0 - summary["silence_ratio"]) * 0.16
            + subtitle_quality_score * 0.06
        )
        story_completeness_score = (
            closure_score * 0.42
            + min(1.0, candidate.get("speech_coverage", 0.0) / 0.58) * 0.20
            + min(1.0, duration_fit + 0.10) * 0.14
            + (0.12 if candidate.get("story_continued_after_pause") else 0.0)
            + min(0.12, pause_story_keep_count * 0.04)
            + subtitle_quality_score * 0.04
        )
        cold_open_dead_time = min(
            cold_open_window_seconds,
            max(0.0, float(candidate.get("hook_gap", 0.0) or 0.0)),
        )
        cold_open_dead_time_penalty = max(
            0.0,
            min(
                1.0,
                (cold_open_dead_time / max(0.05, cold_open_dead_time_threshold)) - 1.0,
            ),
        )
        
        # Premise scoring timing
        _t0 = time.perf_counter()
        premise_scores = self._premise_signal_scores(
            faces=faces,
            video=video,
            summary=summary,
            hook_score=hook_score,
            story_context_score=story_context_score,
            curiosity_gap_score=curiosity_gap_score,
            payoff_strength=payoff_strength,
            cold_open_dead_time_penalty=cold_open_dead_time_penalty,
            subtitle_quality_score=subtitle_quality_score,
            visual_subject_score=visual_subject_score,
            reframe_feasibility_score=reframe_feasibility_score,
            empty_frame_risk=empty_frame_risk,
        )
        _timings["premise_scoring_sec"] = round(time.perf_counter() - _t0, 3)
        story_clarity = (
            dialogue_density * 0.22
            + hook_score * 0.18
            + premise_scores["visual_premise_strength"] * 0.12
            + premise_scores["first_second_hook_score"] * 0.10
            + min(1.0, candidate.get("estimated_turns", 0) / 4.0) * 0.14
            + min(
                1.0, float(faces.get("subject_presence", faces["face_presence"])) / 0.55
            )
            * 0.10
            + min(1.0, summary["audio_energy"] / 0.45) * 0.08
            + duration_fit * 0.10
            + max(0.0, 1.0 - cold_open_dead_time_penalty) * 0.04
        )
        dead_air_severity = max(
            0.0,
            min(
                1.0,
                pause_cut_seconds_total / max(1.0, duration * 0.16)
                + summary["silence_ratio"] * 0.42
                + cold_open_dead_time_penalty * 0.44,
            ),
        )
        watchability_score = max(
            0.0,
            min(
                1.0,
                hook_strength * 0.18
                + curiosity_gap_score * 0.10
                + payoff_strength * 0.15
                + story_clarity * 0.14
                + story_interest_score * 0.13
                + story_completeness_score * 0.12
                + reframe_feasibility_score * 0.08
                + visual_subject_score * 0.05
                + premise_scores["premise_signal_score"] * 0.10
                + premise_scores["sound_off_hook_score"] * 0.06
                + premise_scores["first_second_hook_score"] * 0.04
                + max(0.0, 1.0 - summary["silence_ratio"]) * 0.08
                + subtitle_quality_score * 0.08
                - dead_air_severity * 0.20,
            ),
        )
        packaging_quality_score = max(
            0.0,
            min(
                1.0,
                hook_strength * 0.26
                + payoff_strength * 0.18
                + story_clarity * 0.14
                + story_interest_score * 0.14
                + story_completeness_score * 0.08
                + premise_scores["visual_premise_strength"] * 0.08
                + premise_scores["first_second_hook_score"] * 0.06
                + max(0.0, 1.0 - cold_open_dead_time_penalty) * 0.06
                + subtitle_quality_score * 0.04,
            ),
        )
        recommendation_readiness_score = max(
            0.0,
            min(
                1.0,
                watchability_score * 0.28
                + hook_strength * 0.14
                + curiosity_gap_score * 0.10
                + payoff_strength * 0.12
                + packaging_quality_score * 0.08
                + premise_scores["premise_signal_score"] * 0.10
                + premise_scores["sound_off_hook_score"] * 0.08
                + visual_subject_score * 0.06
                + reframe_feasibility_score * 0.04
                + subtitle_quality_score * 0.04
                + max(0.0, 1.0 - dead_air_severity) * 0.04,
            ),
        )
        penalties = 0.0
        duration_floor_penalty = max(
            0.0,
            (min_publishable_seconds - duration) / max(1.0, min_publishable_seconds),
        )
        if summary["silence_ratio"] > 0.58:
            penalties += 0.10
        if pause_cut_count >= 3:
            penalties += min(0.10, pause_cut_count * 0.02)
        if cold_open_dead_time_penalty > 0:
            penalties += min(0.12, cold_open_dead_time_penalty * 0.12)
        if duration < min_publishable_seconds:
            penalties += 0.16
        if video["brightness"] < 0.10:
            penalties += 0.06
        if reframe_feasibility_score < float(
            self.cfg.get("reframe_feasibility_threshold", 0.34)
        ):
            penalties += 0.10
        if empty_frame_risk > float(
            self.cfg.get("empty_frame_risk_reject_threshold", 0.58)
        ):
            penalties += 0.08
        premise_floor = float(self.cfg.get("visual_premise_threshold", 0.48))
        sound_floor = float(self.cfg.get("sound_off_hook_threshold", 0.56))
        first_second_floor = float(self.cfg.get("first_second_hook_threshold", 0.54))
        if premise_scores["visual_premise_strength"] < premise_floor:
            penalties += min(
                0.08, (premise_floor - premise_scores["visual_premise_strength"]) * 0.18
            )
        if premise_scores["sound_off_hook_score"] < sound_floor:
            penalties += min(
                0.10, (sound_floor - premise_scores["sound_off_hook_score"]) * 0.20
            )
        if premise_scores["first_second_hook_score"] < first_second_floor:
            penalties += min(
                0.08,
                (first_second_floor - premise_scores["first_second_hook_score"]) * 0.16,
            )
        story_interest_weight = float(self.cfg.get("story_interest_weight", 0.40))
        story_completeness_weight = float(
            self.cfg.get("story_completeness_weight", 0.28)
        )
        story_context_weight = float(self.cfg.get("story_context_weight", 0.18))
        story_visual_weight = float(self.cfg.get("story_visual_weight", 0.08))
        story_subtitle_sanity_weight = float(
            self.cfg.get("story_subtitle_sanity_weight", 0.06)
        )
        face_visibility_multiplier = max(
            0.35, min(1.0, 0.35 + face_evidence_score * 0.65)
        )
        face_evidence_penalty = 0.0
        if face_evidence_score <= 0.06:
            face_evidence_penalty = 0.14
        elif face_evidence_score < 0.18:
            face_evidence_penalty = (0.18 - face_evidence_score) * 0.22
        score = max(
            0.0,
            (
                story_interest_score * story_interest_weight
                + story_completeness_score * story_completeness_weight
                + story_context_score * story_context_weight
                + reframe_feasibility_score * story_visual_weight
                + story_clarity * 0.08
                + recommendation_readiness_score * 0.14
                + watchability_score * 0.10
                + packaging_quality_score * 0.06
                + subtitle_quality_score * story_subtitle_sanity_weight
                + premise_scores["premise_signal_score"] * 0.10
                - penalties
                - face_evidence_penalty
            )
            * face_visibility_multiplier,
        )
        breakdown = dict(candidate["score_breakdown"])
        story_unit_type = self._classify_story_archetype(
            candidate,
            {
                **breakdown,
                "hook_score": hook_score,
                "closure_score": closure_score,
                "dialogue_exchange_score": story_context_score,
            },
        )
        story_profile = self._story_arc_profile(candidate)
        breakdown.update(
            {
                "speech_density": round(summary["speech_density"], 4),
                "silence_ratio": round(summary["silence_ratio"], 4),
                "audio_energy": round(summary["audio_energy"], 4),
                "face_presence": round(faces["face_presence"], 4),
                "person_presence": round(float(faces.get("person_presence", 0.0)), 4),
                "subject_presence": round(
                    float(faces.get("subject_presence", faces["face_presence"])), 4
                ),
                "motion": round(video["motion"], 4),
                "brightness": round(video["brightness"], 4),
                "story_clarity_score": round(story_clarity, 4),
                "clarity_score": round(
                    min(1.0, max(0.0, story_clarity - duration_floor_penalty * 0.08)), 4
                ),
                "duration_penalty": round(duration_floor_penalty, 4),
                "story_interest_score": round(story_interest_score, 4),
                "story_completeness_score": round(story_completeness_score, 4),
                "story_context_score": round(story_context_score, 4),
                "subtitle_confidence": round(subtitle_confidence, 4),
                "subtitle_text_sanity_score": round(subtitle_text_sanity, 4),
                "subtitle_language_consistency": round(
                    subtitle_language_consistency, 4
                ),
                "subtitle_quality_score": round(subtitle_quality_score, 4),
                "face_evidence_score": round(face_evidence_score, 4),
                "hook_score": round(hook_score, 4),
                "hook_strength": round(hook_strength, 4),
                "curiosity_gap_score": round(curiosity_gap_score, 4),
                "development_score": round(development_score, 4),
                "closure_score": round(closure_score, 4),
                "payoff_strength": round(payoff_strength, 4),
                "watchability_score": round(watchability_score, 4),
                "recommendation_readiness_score": round(
                    recommendation_readiness_score, 4
                ),
                "packaging_quality_score": round(packaging_quality_score, 4),
                "cold_open_dead_time_penalty": round(cold_open_dead_time_penalty, 4),
                "dead_air_severity": round(dead_air_severity, 4),
                "visual_subject_score": round(visual_subject_score, 4),
                "reframe_feasibility_score": round(reframe_feasibility_score, 4),
                "empty_frame_risk": round(empty_frame_risk, 4),
                "visual_premise_strength": round(
                    float(premise_scores["visual_premise_strength"]), 4
                ),
                "first_second_hook_score": round(
                    float(premise_scores["first_second_hook_score"]), 4
                ),
                "sound_off_hook_score": round(
                    float(premise_scores["sound_off_hook_score"]), 4
                ),
                "premise_signal_score": round(
                    float(premise_scores["premise_signal_score"]), 4
                ),
                "pause_soft_keep_count": pause_soft_keep_count,
                "pause_cut_seconds_total": round(pause_cut_seconds_total, 4),
                "story_unit_type": story_unit_type,
                "story_mode": str(
                    duration_policy.get("story_mode", self._story_mode())
                ),
                "tension_context_score": round(
                    float(duration_policy.get("tension_context_score", 0.0) or 0.0), 4
                ),
                "story_completion_score": round(
                    float(story_profile["story_completion_score"]), 4
                ),
                "context_completeness_score": round(
                    float(story_profile["context_completeness_score"]), 4
                ),
                "hook_type": story_profile["hook_type"],
                "payoff_type": story_profile["payoff_type"],
                "story_arc_shape": story_profile["story_arc_shape"],
                "conversation_id": story_profile["conversation_id"],
                "topic_shift_events": int(story_profile["topic_shift_events"]),
                "subject_detector_pass": str(
                    faces.get("subject_detector_pass", "light")
                ),
                "duration": round(duration, 4),
                "score": round(score, 4),
                "quality_penalty": round(penalties, 4),
            }
        )
        
        # Add timing information to breakdown
        if _timings:
            elapsed_total = time.perf_counter() - _total_start
            breakdown['debug_timings'] = {
                'total_sec': round(elapsed_total, 3),
                'face_detection_sec': _timings.get('face_detection_sec', 0),
                'video_metrics_sec': _timings.get('video_metrics_sec', 0),
                'premise_scoring_sec': _timings.get('premise_scoring_sec', 0),
            }
        
        return round(score, 4), breakdown

    def _semantic_preview_rerank(
        self, video_path: str, candidates: list[dict], progress_callback=None
    ):
        if not candidates:
            return candidates
        reranked = []
        soft_timeout_seconds = float(
            self.cfg.get(
                "semantic_preview_soft_timeout_seconds",
                self.cfg.get("semantic_preview_candidate_timeout_seconds", 120),
            )
        )
        hard_timeout_seconds = float(
            self.cfg.get(
                "semantic_preview_hard_timeout_seconds",
                max(soft_timeout_seconds + 10.0, 120.0),
            )
        )
        heartbeat_seconds = float(self.cfg.get("heartbeat_interval_seconds", 30))
        for candidate in candidates:
            candidate = dict(candidate)
            candidate["score_breakdown"] = dict(candidate.get("score_breakdown", {}))
            timed = _run_in_subprocess_with_timeout(
                "semantic_preview",
                {"cfg": self.cfg, "video_path": video_path, "candidate": candidate},
                soft_timeout_seconds=soft_timeout_seconds,
                hard_timeout_seconds=hard_timeout_seconds,
                default=candidate,
                heartbeat_seconds=heartbeat_seconds,
                on_heartbeat=self._heartbeat_callback(
                    progress_callback,
                    "ranking",
                    f"Semantic preview {candidate['start']:.2f}-{candidate['end']:.2f}",
                ),
                on_soft_timeout=lambda _elapsed: self._watchdog_stats.__setitem__(
                    "semantic_preview_timeouts",
                    self._watchdog_stats.get("semantic_preview_timeouts", 0) + 1,
                ),
                on_hard_timeout=lambda _elapsed: self._watchdog_stats.__setitem__(
                    "hard_timeouts", self._watchdog_stats.get("hard_timeouts", 0) + 1
                ),
            )
            candidate = timed["result"] if isinstance(timed, dict) else candidate
            if bool((timed or {}).get("hard_timeout")):
                self._watchdog_stats["semantic_preview_fallback_used"] = (
                    self._watchdog_stats.get("semantic_preview_fallback_used", 0) + 1
                )
            if (
                candidate.get("score_breakdown", {}).get(
                    "preview_interestingness_score"
                )
                is None
            ):
                self._watchdog_stats["semantic_preview_fallback_used"] = (
                    self._watchdog_stats.get("semantic_preview_fallback_used", 0) + 1
                )
            reranked.append(candidate)
        reranked.sort(
            key=lambda item: (
                item["score"],
                item["score_breakdown"].get("preview_interestingness_score", 0.0),
                item["score_breakdown"].get("story_clarity_score", 0.0),
            ),
            reverse=True,
        )
        return reranked

    def _candidate_continuity_score(
        self, left: dict, right: dict, *, max_gap: float | None = None
    ) -> float:
        gap = max(0.0, float(right["start"]) - float(left["end"]))
        max_gap = float(
            max_gap
            if max_gap is not None
            else self.cfg.get(
                "stitch_gap_max_seconds",
                self.cfg.get("story_extension_max_pause_seconds", 1.15),
            )
        )
        if gap > max_gap:
            return 0.0
        same_source = left.get("source") == right.get("source")
        same_window = abs(
            float(left.get("window_end", left["end"]))
            - float(right.get("window_start", right["start"]))
        ) <= max(4.0, max_gap * 2.0)
        left_breakdown = dict(left.get("score_breakdown", {}))
        right_breakdown = dict(right.get("score_breakdown", {}))
        closure_gain = max(
            0.0,
            float(right_breakdown.get("closure_score", 0.0))
            - float(left_breakdown.get("closure_score", 0.0)),
        )
        hook_quality = max(
            float(left_breakdown.get("hook_score", 0.0)),
            float(right_breakdown.get("hook_score", 0.0)),
        )
        continuation = (
            0.12
            if bool(
                left.get("story_continued_after_pause")
                or right.get("story_continued_after_pause")
            )
            else 0.0
        )
        score = (
            (0.24 if same_source else 0.0)
            + (0.14 if same_window else 0.0)
            + max(0.0, (max_gap - gap) / max(0.001, max_gap)) * 0.24
            + closure_gain * 0.22
            + hook_quality * 0.10
            + continuation
        )
        return round(min(1.0, score), 4)

    def _build_story_window_plan(
        self,
        candidate: dict,
        subtitle_info: dict | None = None,
        duration_policy: dict | None = None,
    ) -> dict:
        duration_policy = dict(
            duration_policy or candidate.get("duration_policy", {}) or {}
        )
        min_window = max(
            35.0,
            float(
                duration_policy.get(
                    "min_publishable_seconds",
                    self.cfg.get("story_window_min_seconds", 35),
                )
                or self.cfg.get("story_window_min_seconds", 35)
            ),
        )
        max_window = min(
            60.0,
            float(
                duration_policy.get(
                    "soft_max_seconds", self.cfg.get("story_window_max_seconds", 60)
                )
                or self.cfg.get("story_window_max_seconds", 60)
            ),
        )
        subtitle_segments = list((subtitle_info or {}).get("segments", []) or [])
        window_plan = _build_story_plan_montage(
            {
                "conversation_id": candidate.get("conversation_id"),
                "turns": subtitle_segments,
            },
            min_seconds=min_window,
            max_seconds=max_window,
        )
        window_plan["assembly_mode"] = str(
            candidate.get("story_window_mode", "assembled_story_window")
        )
        window_plan["story_window_min_seconds"] = round(min_window, 3)
        window_plan["story_window_max_seconds"] = round(max_window, 3)
        window_plan["duration"] = round(
            float(
                window_plan.get("story_window_plan", {}).get(
                    "duration", candidate.get("duration", 0.0)
                )
                or candidate.get("duration", 0.0)
            ),
            3,
        )
        window_plan["clarity_score"] = round(
            float(window_plan.get("clarity_score", 0.0) or 0.0), 4
        )
        window_plan["duration_penalty"] = round(
            float(window_plan.get("duration_penalty", 0.0) or 0.0), 4
        )
        window_plan["segments"] = list(
            window_plan.get("story_window_segments", []) or []
        )
        window_plan["subtitle_segment_count"] = int(len(subtitle_segments))
        window_plan["merge_reason"] = str(
            candidate.get(
                "merge_reason", candidate.get("stitch_reason", "story_window_assembly")
            )
        )
        window_plan["source_candidates"] = [
            list(map(float, pair))
            for pair in (candidate.get("stitched_from_candidates", []) or [])
        ]
        window_plan["window_expansion_meta"] = dict(
            candidate.get("duration_expansion_meta", {}) or {}
        )
        return window_plan

    def _candidate_review_defaults(
        self, direct_candidate_mode: bool
    ) -> tuple[bool, str]:
        if direct_candidate_mode:
            return False, "test_mode_visual_only"
        return False, "strong_publishable"

    def _story_thread_keywords(
        self,
        candidate: dict,
        subtitle_info: dict | None = None,
        *,
        max_keywords: int = 4,
    ) -> list[str]:
        summary = dict((subtitle_info or {}).get("summary", {}) or {})
        raw_keywords = [
            str(item).strip().lower()
            for item in (summary.get("keywords") or [])
            if str(item).strip()
        ]
        if raw_keywords:
            return raw_keywords[:max_keywords]
        text = str(summary.get("summary_text", "") or "")
        if not text:
            text = " ".join(
                str(item.get("text", "") or "")
                for item in list((subtitle_info or {}).get("segments", []) or [])[:6]
            )
        tokens = [
            token.lower() for token in _tokenize(_clean_text(text)) if len(token) >= 4
        ]
        if not tokens:
            breakdown = dict(candidate.get("score_breakdown", {}) or {})
            fallback_bits = [
                str(candidate.get("story_unit_type", "") or ""),
                str(candidate.get("merge_reason", "") or ""),
                str(candidate.get("stitch_reason", "") or ""),
                str(breakdown.get("story_unit_type", "") or ""),
            ]
            tokens = [
                token.lower()
                for token in _tokenize(" ".join(fallback_bits))
                if len(token) >= 4
            ]
        seen = set()
        keywords = []
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            keywords.append(token)
            if len(keywords) >= max_keywords:
                break
        return keywords

    def _story_thread_signature(
        self, candidate: dict, subtitle_info: dict | None = None
    ) -> dict:
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        start = float(candidate.get("start", 0.0) or 0.0)
        thread_window_seconds = max(
            12.0, float(self.cfg.get("story_thread_window_seconds", 24.0) or 24.0)
        )
        hook_score = max(
            float(breakdown.get("hook_score", 0.0) or 0.0),
            float(breakdown.get("first_second_hook_score", 0.0) or 0.0),
            float(breakdown.get("sound_off_hook_score", 0.0) or 0.0),
            float(breakdown.get("premise_signal_score", 0.0) or 0.0),
        )
        coherence_context = max(
            float(breakdown.get("story_context_score", 0.0) or 0.0),
            float(breakdown.get("dialogue_exchange_score", 0.0) or 0.0) * 0.35,
        )
        speaker_proxy = max(
            float(breakdown.get("face_presence", 0.0) or 0.0),
            float(breakdown.get("person_presence", 0.0) or 0.0),
            float(breakdown.get("subject_presence", 0.0) or 0.0),
            float(candidate.get("source_face_presence", 0.0) or 0.0),
            float(candidate.get("source_person_presence", 0.0) or 0.0),
            float(candidate.get("source_subject_presence", 0.0) or 0.0),
        )
        keywords = self._story_thread_keywords(candidate, subtitle_info)
        story_unit_type = str(
            breakdown.get(
                "story_unit_type", candidate.get("story_unit_type", "dialogue_cluster")
            )
            or "dialogue_cluster"
        ).lower()
        return {
            "source": str(candidate.get("source", "") or ""),
            "thread_bucket": int(start // thread_window_seconds),
            "story_unit_type": story_unit_type,
            "keywords": keywords,
            "hook_bin": int(min(4, max(0, round(hook_score * 4.0)))),
            "context_bin": int(min(4, max(0, round(coherence_context * 4.0)))),
            "speaker_bin": int(min(4, max(0, round(speaker_proxy * 4.0)))),
        }

    def _story_thread_id(
        self, candidate: dict, subtitle_info: dict | None = None
    ) -> str:
        signature = self._story_thread_signature(candidate, subtitle_info)
        payload = json.dumps(
            signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return "thread_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]

    def _story_arc_profile(
        self,
        candidate: dict,
        subtitle_info: dict | None = None,
        boundary_meta: dict | None = None,
    ) -> dict:
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        summary = dict((subtitle_info or {}).get("summary", {}) or {})
        signals = dict((subtitle_info or {}).get("signals", {}) or {})
        story_thread_id = str(
            candidate.get("story_thread_id")
            or self._story_thread_id(candidate, subtitle_info)
        )
        keywords = self._story_thread_keywords(candidate, subtitle_info)
        hook_score = max(
            float(breakdown.get("hook_score", 0.0) or 0.0),
            float(breakdown.get("first_second_hook_score", 0.0) or 0.0),
            float(breakdown.get("sound_off_hook_score", 0.0) or 0.0),
            float(breakdown.get("premise_signal_score", 0.0) or 0.0),
        )
        visible_stakes = max(
            float(breakdown.get("visible_stakes_score", 0.0) or 0.0),
            float(breakdown.get("visual_premise_strength", 0.0) or 0.0),
            float(summary.get("visible_stakes_score", 0.0) or 0.0),
        )
        first_frame_clarity = max(
            float(breakdown.get("first_frame_clarity_score", 0.0) or 0.0),
            float(summary.get("first_frame_clarity_score", 0.0) or 0.0),
        )
        sound_off_premise = max(
            float(
                breakdown.get(
                    "sound_off_premise_score",
                    breakdown.get("sound_off_hook_score", 0.0),
                )
                or 0.0
            ),
            float(summary.get("sound_off_premise_score", 0.0) or 0.0),
        )
        closure_score = max(
            float(breakdown.get("closure_score", 0.0) or 0.0),
            float(signals.get("closure_score", 0.0) or 0.0),
            1.0 if bool(signals.get("story_has_payoff", False)) else 0.0,
            1.0
            if bool(boundary_meta and boundary_meta.get("story_has_payoff"))
            else 0.0,
        )
        dialogue_exchange_score = max(
            float(breakdown.get("dialogue_exchange_score", 0.0) or 0.0),
            float(signals.get("dialogue_exchange_score", 0.0) or 0.0),
        )
        story_context_score = max(
            float(breakdown.get("story_context_score", 0.0) or 0.0),
            dialogue_exchange_score * 0.35,
            float(summary.get("story_context_score", 0.0) or 0.0),
        )
        story_completeness_score = max(
            float(breakdown.get("story_completeness_score", 0.0) or 0.0),
            closure_score,
            float(
                boundary_meta.get("story_boundary_confidence", 0.0)
                if boundary_meta
                else 0.0
            ),
        )
        context_completeness_score = max(
            0.0,
            min(
                1.0,
                story_context_score * 0.72
                + min(1.0, len(keywords) / 4.0) * 0.14
                + max(0.0, 1.0 - float(breakdown.get("silence_ratio", 1.0) or 1.0))
                * 0.14,
            ),
        )
        story_completion_score = max(
            0.0,
            min(
                1.0,
                closure_score * 0.46
                + story_completeness_score * 0.22
                + context_completeness_score * 0.18
                + hook_score * 0.14,
            ),
        )
        if (
            visible_stakes >= 0.76
            and first_frame_clarity >= 0.68
            and sound_off_premise >= 0.64
        ):
            hook_type = "stakes_first"
        elif first_frame_clarity >= sound_off_premise and first_frame_clarity >= 0.72:
            hook_type = "first_frame_clarity"
        elif sound_off_premise >= 0.68:
            hook_type = "sound_off_premise"
        elif hook_score >= 0.62 and dialogue_exchange_score >= 0.44:
            hook_type = "dialogue_conflict"
        elif hook_score >= 0.52:
            hook_type = "balanced_hook"
        else:
            hook_type = "weak_hook"
        if closure_score >= 0.90 and story_completion_score >= 0.76:
            payoff_type = "resolution"
        elif float(breakdown.get("payoff_strength", 0.0) or 0.0) >= 0.74:
            payoff_type = "punchline"
        elif float(breakdown.get("story_completeness_score", 0.0) or 0.0) >= 0.72:
            payoff_type = "scene"
        elif story_completion_score >= 0.58:
            payoff_type = "reaction"
        else:
            payoff_type = "unfinished"
        if story_completion_score >= 0.76 and context_completeness_score >= 0.56:
            story_arc_shape = "hook_setup_escalation_payoff"
        elif story_completion_score >= 0.62:
            story_arc_shape = "hook_setup_escalation"
        else:
            story_arc_shape = "hook_fragment"
        topic_shift_events = int(
            signals.get("topic_shift_events", signals.get("topic_shift_count", 0)) or 0
        )
        keywords_head = keywords[:3]
        if (
            len(keywords) >= 4
            and len(set(keywords[:2]) & set(keywords[-2:])) == 0
            and context_completeness_score < 0.56
        ):
            topic_shift_events = max(topic_shift_events, 1)
        if str(candidate.get("coherence_rejection_reason", "")) == "thread_boundary":
            topic_shift_events = max(topic_shift_events, 1)
        if story_context_score < 0.46 and story_completion_score < 0.58:
            topic_shift_events = max(topic_shift_events, 1)
        if not keywords_head:
            keywords_head = [
                str(
                    candidate.get("story_unit_type", "dialogue_cluster")
                    or "dialogue_cluster"
                )
            ]
        conversation_signature = {
            "thread": story_thread_id,
            "bucket": self._candidate_review_bucket(candidate),
            "keywords": keywords_head,
            "arc": story_arc_shape,
            "hook": hook_type,
            "payoff": payoff_type,
        }
        conversation_payload = json.dumps(
            conversation_signature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        conversation_id = (
            "conv_"
            + hashlib.sha1(conversation_payload.encode("utf-8")).hexdigest()[:10]
        )
        return {
            "story_thread_id": story_thread_id,
            "conversation_id": conversation_id,
            "story_arc_shape": story_arc_shape,
            "hook_type": hook_type,
            "payoff_type": payoff_type,
            "hook_score": round(hook_score, 4),
            "payoff_score": round(closure_score, 4),
            "context_completeness_score": round(context_completeness_score, 4),
            "story_completion_score": round(story_completion_score, 4),
            "topic_shift_events": int(topic_shift_events),
            "reordered_hook": bool(
                boundary_meta and boundary_meta.get("hook_shift_applied", False)
            ),
        }

    def _story_pair_coherence_score(
        self,
        left: dict,
        right: dict,
        *,
        subtitle_left: dict | None = None,
        subtitle_right: dict | None = None,
        max_gap: float | None = None,
    ) -> float:
        max_gap = float(
            max_gap
            if max_gap is not None
            else self.cfg.get(
                "story_thread_max_gap_seconds",
                self.cfg.get("story_extension_max_pause_seconds", 1.15),
            )
        )
        gap = max(
            0.0,
            float(right.get("start", 0.0) or 0.0) - float(left.get("end", 0.0) or 0.0),
        )
        left_breakdown = dict(left.get("score_breakdown", {}) or {})
        right_breakdown = dict(right.get("score_breakdown", {}) or {})
        left_keywords = set(self._story_thread_keywords(left, subtitle_left))
        right_keywords = set(self._story_thread_keywords(right, subtitle_right))
        keyword_overlap = len(left_keywords & right_keywords) / max(
            1, min(len(left_keywords), len(right_keywords), 4)
        )
        same_source = (
            1.0
            if str(left.get("source", "") or "") == str(right.get("source", "") or "")
            else 0.0
        )
        same_unit = (
            1.0
            if str(
                left_breakdown.get("story_unit_type", left.get("story_unit_type", ""))
                or ""
            ).lower()
            == str(
                right_breakdown.get("story_unit_type", right.get("story_unit_type", ""))
                or ""
            ).lower()
            else 0.0
        )
        same_bucket = (
            1.0
            if self._candidate_review_bucket(left)
            == self._candidate_review_bucket(right)
            else 0.0
        )
        temporal = max(0.0, 1.0 - (gap / max(0.001, max_gap)))
        hook_sim = max(
            0.0,
            1.0
            - abs(
                max(
                    float(left_breakdown.get("hook_score", 0.0) or 0.0),
                    float(left_breakdown.get("first_second_hook_score", 0.0) or 0.0),
                    float(left_breakdown.get("sound_off_hook_score", 0.0) or 0.0),
                )
                - max(
                    float(right_breakdown.get("hook_score", 0.0) or 0.0),
                    float(right_breakdown.get("first_second_hook_score", 0.0) or 0.0),
                    float(right_breakdown.get("sound_off_hook_score", 0.0) or 0.0),
                )
            ),
        )
        context_sim = max(
            0.0,
            1.0
            - abs(
                float(left_breakdown.get("story_context_score", 0.0) or 0.0)
                - float(right_breakdown.get("story_context_score", 0.0) or 0.0)
            ),
        )
        clarity_sim = max(
            0.0,
            1.0
            - abs(
                float(left_breakdown.get("story_clarity_score", 0.0) or 0.0)
                - float(right_breakdown.get("story_clarity_score", 0.0) or 0.0)
            ),
        )
        face_sim = max(
            0.0,
            1.0
            - abs(
                max(
                    float(left_breakdown.get("face_presence", 0.0) or 0.0),
                    float(left_breakdown.get("person_presence", 0.0) or 0.0),
                    float(left_breakdown.get("subject_presence", 0.0) or 0.0),
                )
                - max(
                    float(right_breakdown.get("face_presence", 0.0) or 0.0),
                    float(right_breakdown.get("person_presence", 0.0) or 0.0),
                    float(right_breakdown.get("subject_presence", 0.0) or 0.0),
                )
            ),
        )
        silence_sim = max(
            0.0,
            1.0
            - abs(
                float(left_breakdown.get("silence_ratio", 0.0) or 0.0)
                - float(right_breakdown.get("silence_ratio", 0.0) or 0.0)
            ),
        )
        score = (
            same_source * 0.24
            + same_unit * 0.16
            + same_bucket * 0.12
            + temporal * 0.16
            + keyword_overlap * 0.12
            + hook_sim * 0.08
            + context_sim * 0.08
            + clarity_sim * 0.06
            + face_sim * 0.04
            + silence_sim * 0.04
        )
        if same_source <= 0.0:
            score -= 0.14
        if same_unit <= 0.0 and keyword_overlap < 0.34:
            score -= 0.12
        if gap > max_gap * 2.0:
            score -= 0.10
        return round(max(0.0, min(1.0, score)), 4)

    def _candidate_story_coherence(
        self, candidate: dict, subtitle_info: dict | None = None
    ) -> float:
        if bool(candidate.get("stitched_story_unit", False)):
            stitched = list(candidate.get("stitched_from_candidates", []) or [])
            if len(stitched) >= 2:
                pairs = []
                for left_pair, right_pair in zip(stitched, stitched[1:]):
                    left = {
                        "start": float(left_pair[0]),
                        "end": float(left_pair[1]),
                        "source": candidate.get("source", ""),
                        "score_breakdown": dict(
                            candidate.get("score_breakdown", {}) or {}
                        ),
                    }
                    right = {
                        "start": float(right_pair[0]),
                        "end": float(right_pair[1]),
                        "source": candidate.get("source", ""),
                        "score_breakdown": dict(
                            candidate.get("score_breakdown", {}) or {}
                        ),
                    }
                    pairs.append(
                        self._story_pair_coherence_score(
                            left,
                            right,
                            subtitle_left=subtitle_info,
                            subtitle_right=subtitle_info,
                        )
                    )
                if pairs:
                    return round(min(1.0, max(0.0, sum(pairs) / len(pairs))), 4)
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        hook = max(
            float(breakdown.get("hook_score", 0.0) or 0.0),
            float(breakdown.get("first_second_hook_score", 0.0) or 0.0),
            float(breakdown.get("sound_off_hook_score", 0.0) or 0.0),
        )
        context = max(
            float(breakdown.get("story_context_score", 0.0) or 0.0),
            float(breakdown.get("dialogue_exchange_score", 0.0) or 0.0) * 0.35,
        )
        completeness = max(
            float(breakdown.get("story_completeness_score", 0.0) or 0.0),
            float(breakdown.get("closure_score", 0.0) or 0.0),
        )
        speech = max(
            0.0,
            min(
                1.0,
                float(
                    breakdown.get(
                        "speech_coverage", candidate.get("speech_coverage", 0.0)
                    )
                    or 0.0
                ),
            ),
        )
        silence = max(0.0, min(1.0, float(breakdown.get("silence_ratio", 1.0) or 1.0)))
        face = max(
            float(breakdown.get("face_presence", 0.0) or 0.0),
            float(breakdown.get("person_presence", 0.0) or 0.0),
            float(breakdown.get("subject_presence", 0.0) or 0.0),
        )
        score = (
            hook * 0.26
            + context * 0.22
            + completeness * 0.18
            + speech * 0.14
            + face * 0.12
            + max(0.0, 1.0 - silence) * 0.08
        )
        if bool(candidate.get("story_continued_after_pause", False)):
            score += 0.04
        if (
            str(
                candidate.get("story_unit_type", breakdown.get("story_unit_type", ""))
                or ""
            ).lower()
            == "fallback_window"
        ):
            score -= 0.08
        return round(max(0.0, min(1.0, score)), 4)

    def _assign_story_threads(
        self,
        candidates: list[dict],
        subtitle_info_by_candidate: dict[int, dict] | None = None,
        progress_callback=None,
    ) -> list[dict]:
        if not candidates:
            return candidates
        threshold = float(self.cfg.get("story_coherence_threshold", 0.62))
        ordered = list(enumerate(candidates))
        ordered.sort(
            key=lambda pair: (
                str(pair[1].get("source", "") or ""),
                float(pair[1].get("start", 0.0) or 0.0),
                float(pair[1].get("end", 0.0) or 0.0),
            )
        )
        current_thread_id = None
        current_anchor = None
        thread_counter = 0
        for original_index, candidate in ordered:
            subtitle_info = (
                None
                if subtitle_info_by_candidate is None
                else subtitle_info_by_candidate.get(original_index)
            )
            if current_anchor is None:
                current_thread_id = self._story_thread_id(candidate, subtitle_info)
                candidate["story_thread_id"] = current_thread_id
                candidate["story_coherence_score"] = self._candidate_story_coherence(
                    candidate, subtitle_info
                )
                candidate["coherence_merge_reason"] = "thread_seed"
                candidate["coherence_rejection_reason"] = ""
                arc_profile = self._story_arc_profile(candidate, subtitle_info)
                candidate["conversation_id"] = arc_profile["conversation_id"]
                current_anchor = candidate
                thread_counter += 1
                continue
            coherence = self._story_pair_coherence_score(
                current_anchor,
                candidate,
                subtitle_left=None,
                subtitle_right=subtitle_info,
            )
            if coherence >= threshold and str(candidate.get("source", "") or "") == str(
                current_anchor.get("source", "") or ""
            ):
                candidate["story_thread_id"] = current_thread_id
                candidate["story_coherence_score"] = coherence
                candidate["coherence_merge_reason"] = "thread_continuation"
                candidate["coherence_rejection_reason"] = ""
                candidate["conversation_id"] = self._story_arc_profile(
                    candidate, subtitle_info
                )["conversation_id"]
                current_anchor = candidate
                continue
            if progress_callback is not None:
                _emit(
                    progress_callback,
                    "story",
                    f"conversation_split topic_shift coherence={coherence:.2f}",
                )
            current_thread_id = self._story_thread_id(candidate, subtitle_info)
            candidate["story_thread_id"] = current_thread_id
            candidate["story_coherence_score"] = self._candidate_story_coherence(
                candidate, subtitle_info
            )
            candidate["coherence_merge_reason"] = "thread_reset"
            candidate["coherence_rejection_reason"] = "thread_boundary"
            candidate["conversation_id"] = self._story_arc_profile(
                candidate, subtitle_info
            )["conversation_id"]
            current_anchor = candidate
            thread_counter += 1
        return candidates

    def _candidate_face_evidence(self, candidate: dict) -> float:
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        return max(
            float(breakdown.get("face_evidence_score", 0.0) or 0.0),
            float(breakdown.get("face_presence", 0.0) or 0.0),
            float(breakdown.get("person_presence", 0.0) or 0.0),
            float(breakdown.get("subject_presence", 0.0) or 0.0),
            float(candidate.get("source_face_presence", 0.0) or 0.0),
            float(candidate.get("source_person_presence", 0.0) or 0.0),
            float(candidate.get("source_subject_presence", 0.0) or 0.0),
        )

    def _candidate_review_bucket(self, candidate: dict) -> tuple[int, str]:
        macro_window = max(
            180, int(self.cfg.get("review_pass_macro_window_seconds", 600) or 600)
        )
        start = max(0.0, float(candidate.get("start", 0.0) or 0.0))
        bucket = int(start // max(1, macro_window))
        source = str(candidate.get("source", "") or "")
        return bucket, source

    def _selection_starvation_bucket(
        self, reason: str, candidate: dict | None = None
    ) -> str:
        reason = str(reason or "").strip().lower()
        if reason in {
            "no_visual_subject",
            "low_visual_viability",
            "high_empty_frame_risk",
            "center_safe_subprocess_failed",
            "face_clipped",
            "no_subject_windows",
            "reject_visual",
        }:
            return "visual_starvation"
        if reason in {
            "low_subtitle_turns",
            "subtitle_timeout",
            "subtitle_confidence_low",
            "subtitle_quality_low",
            "subtitle_text_sanity_low",
            "subtitle_noise",
        }:
            return "subtitle_starvation"
        if reason in {
            "low_dialogue_flow",
            "low_speech_density",
            "too_much_silence",
            "audio_starvation",
            "dialogue_proxy_failed",
        }:
            return "vad_starvation"
        if reason in {
            "low_story_interest",
            "low_story_completeness",
            "low_story_clarity",
            "low_watchability",
            "low_recommendation_readiness",
            "weak_packaging_fit",
            "weak_premise_hook",
            "weak_hook",
            "no_payoff",
            "insufficient_context",
            "boundary_failed",
            "expand_story_boundary",
            "overlap",
            "trim_failed",
        }:
            return "boundary_starvation"
        if (
            candidate
            and float(
                candidate.get("score_breakdown", {}).get("dialogue_exchange_score", 0.0)
                or 0.0
            )
            >= 0.40
        ):
            return "subtitle_starvation"
        return "boundary_starvation"

    def _build_review_pass_candidates(
        self,
        ranked_candidates: list[dict],
        picked_candidates: list[dict],
        *,
        progress_callback=None,
    ) -> list[dict]:
        if not bool(self.cfg.get("review_pass_enabled", True)):
            return []
        target_floor = max(1, int(self.cfg.get("review_pass_min_outputs", 10) or 10))
        if len(picked_candidates) >= target_floor:
            return []

        face_floor = float(self.cfg.get("review_pass_face_floor", 0.10) or 0.10)
        min_speech_density = float(
            self.cfg.get("review_pass_min_speech_density", 0.14) or 0.14
        )
        chain_gap_seconds = float(
            self.cfg.get("review_pass_chain_gap_seconds", 72.0) or 72.0
        )
        segment_merge_gap_seconds = float(
            self.cfg.get(
                "segment_merge_gap_seconds",
                self.cfg.get("story_merge_gap_seconds", 1.0),
            )
            or self.cfg.get("story_merge_gap_seconds", 1.0)
        )
        segment_merge_semantic_threshold = float(
            self.cfg.get("segment_merge_semantic_threshold", 0.56) or 0.56
        )
        review_pass_semantic_threshold = max(
            0.40, segment_merge_semantic_threshold * 0.85
        )
        max_chain_windows = max(
            2, int(self.cfg.get("review_pass_max_chain_windows", 4) or 4)
        )
        max_stitched_seconds = float(
            self.cfg.get("review_pass_max_stitched_seconds", 60.0) or 60.0
        )
        allowed_reasons = {
            "low_story_interest",
            "low_story_completeness",
            "low_story_clarity",
            "low_watchability",
            "low_recommendation_readiness",
            "weak_packaging_fit",
            "weak_premise_hook",
            "weak_hook",
            "no_payoff",
            "insufficient_context",
            "low_dialogue_flow",
            "ranking_timeout",
            "ranking_failed",
            "overlap",
        }
        blocked_reasons = {
            "no_visual_subject",
            "low_visual_viability",
            "high_empty_frame_risk",
            "center_safe_subprocess_failed",
            "trim_failed",
            "subtitle_timeout",
        }
        strict_spans = [
            (float(item.get("start", 0.0) or 0.0), float(item.get("end", 0.0) or 0.0))
            for item in picked_candidates
            if float(item.get("end", 0.0) or 0.0) > float(item.get("start", 0.0) or 0.0)
        ]
        pool = []
        for candidate in ranked_candidates:
            if candidate in picked_candidates:
                continue
            reason = str(
                candidate.get(
                    "selection_rejection_reason", candidate.get("rejection_reason", "")
                )
                or ""
            )
            if reason in blocked_reasons:
                continue
            if (
                reason
                and reason not in allowed_reasons
                and bool(candidate.get("selection_visual_soft_gate", False)) is False
            ):
                continue
            face_evidence = self._candidate_face_evidence(candidate)
            if face_evidence < face_floor:
                continue
            breakdown = dict(candidate.get("score_breakdown", {}) or {})
            if (
                float(
                    breakdown.get(
                        "speech_density", candidate.get("speech_density", 0.0)
                    )
                    or 0.0
                )
                < min_speech_density
            ):
                continue
            if float(breakdown.get("empty_frame_risk", 0.0) or 0.0) > float(
                self.cfg.get("selection_empty_frame_soft_ceiling", 0.72)
            ):
                continue
            pool.append(dict(candidate))

        if len(pool) < 2:
            return []

        pool.sort(
            key=lambda item: (
                self._candidate_review_bucket(item)[0],
                self._candidate_review_bucket(item)[1],
                float(item.get("start", 0.0) or 0.0),
                -self._candidate_face_evidence(item),
                -float(item.get("score", 0.0) or 0.0),
            )
        )

        review_candidates = []
        chain = []
        chain_span_start = 0.0
        chain_span_end = 0.0
        chain_bucket = None
        chain_source = None

        def _flush_chain(items: list[dict]):
            if not items:
                return
            if len(items) == 1:
                single = dict(items[0])
                if self._candidate_face_evidence(single) < face_floor:
                    return
                if float(single.get("end", 0.0) or 0.0) - float(
                    single.get("start", 0.0) or 0.0
                ) < max(35.0, float(self.cfg.get("min_candidate_seconds", 35))):
                    return
                single["review_pass_rescued"] = True
                single["review_pass_window_count"] = 1
                single["review_pass_chain_span_seconds"] = round(
                    float(single.get("end", 0.0) or 0.0)
                    - float(single.get("start", 0.0) or 0.0),
                    3,
                )
                single["review_pass_reason"] = "face_positive_single_window"
                single["merge_reason"] = "single_segment_story_window"
                single["story_thread_id"] = single.get(
                    "story_thread_id"
                ) or self._story_thread_id(single)
                single["story_coherence_score"] = float(
                    single.get(
                        "story_coherence_score", self._candidate_story_coherence(single)
                    )
                    or self._candidate_story_coherence(single)
                )
                single["coherence_merge_reason"] = "single_segment_story_window"
                single["coherence_rejection_reason"] = ""
                score, breakdown = self._score_story_candidate_timeout_fallback(single)
                single["score"] = round(
                    max(float(single.get("score", 0.0) or 0.0), score), 4
                )
                single["score_breakdown"] = dict(breakdown or {})
                single["score_breakdown"]["review_pass_used"] = True
                single["score_breakdown"]["review_pass_window_count"] = 1
                single["score_breakdown"]["review_pass_chain_span_seconds"] = single[
                    "review_pass_chain_span_seconds"
                ]
                single["score_breakdown"]["story_unit_type"] = single.get(
                    "story_unit_type", "dialogue_cluster"
                )
                review_candidates.append(single)
                return

            merged = dict(items[0])
            merged["start"] = round(min(float(item["start"]) for item in items), 3)
            merged["end"] = round(max(float(item["end"]) for item in items), 3)
            merged["window_start"] = round(
                min(float(item.get("window_start", item["start"])) for item in items), 3
            )
            merged["window_end"] = round(
                max(float(item.get("window_end", item["end"])) for item in items), 3
            )
            merged["review_pass_rescued"] = True
            merged["review_pass_window_count"] = len(items)
            merged["review_pass_chain_span_seconds"] = round(
                float(merged["end"]) - float(merged["start"]), 3
            )
            merged["review_pass_reason"] = "stitched_face_positive_arc"
            merged["stitched_story_unit"] = True
            merged["story_unit_type"] = "stitched_context_story"
            merged["merge_reason"] = "segment_graph_merge"
            merged["story_thread_id"] = items[0].get(
                "story_thread_id"
            ) or self._story_thread_id(items[0])
            merged["story_coherence_score"] = round(
                min(
                    1.0,
                    max(
                        0.0,
                        sum(
                            self._story_pair_coherence_score(left, right)
                            for left, right in zip(items, items[1:])
                        )
                        / max(1, len(items) - 1),
                    ),
                ),
                4,
            )
            merged["coherence_merge_reason"] = "review_pass_coherence_chain"
            merged["coherence_rejection_reason"] = ""
            merged["stitched_from_candidates"] = [
                [round(float(item["start"]), 3), round(float(item["end"]), 3)]
                for item in items
            ]
            merged["stitch_reason"] = "review_pass_rescue"
            merged["story_continued_after_pause"] = True
            merged_breakdown = dict(items[0].get("score_breakdown", {}) or {})
            weights = [
                max(
                    0.1,
                    float(item.get("end", 0.0) or 0.0)
                    - float(item.get("start", 0.0) or 0.0),
                )
                for item in items
            ]
            total_weight = max(0.1, sum(weights))

            def _wavg(key: str) -> float:
                values = [
                    float(
                        dict(item.get("score_breakdown", {}) or {}).get(
                            key, item.get(key, 0.0)
                        )
                        or 0.0
                    )
                    for item in items
                ]
                return sum(v * w for v, w in zip(values, weights)) / total_weight

            merged_breakdown["speech_density"] = round(
                min(1.0, _wavg("speech_density")), 4
            )
            merged_breakdown["silence_ratio"] = round(
                min(1.0, _wavg("silence_ratio")), 4
            )
            merged_breakdown["audio_energy"] = round(
                min(
                    1.0,
                    max(
                        _wavg("audio_energy"),
                        max(
                            float(
                                dict(item.get("score_breakdown", {}) or {}).get(
                                    "audio_energy", 0.0
                                )
                                or 0.0
                            )
                            for item in items
                        ),
                    ),
                ),
                4,
            )
            merged_breakdown["speech_coverage"] = round(
                min(
                    1.0,
                    sum(
                        float(
                            item.get(
                                "speech_coverage",
                                dict(item.get("score_breakdown", {}) or {}).get(
                                    "speech_coverage", 0.0
                                ),
                            )
                            or 0.0
                        )
                        for item in items
                    )
                    / max(0.1, float(merged["end"]) - float(merged["start"])),
                ),
                4,
            )
            merged_breakdown["estimated_turns"] = int(
                sum(
                    int(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "estimated_turns", item.get("estimated_turns", 0)
                            )
                            or 0.0
                        )
                    )
                    for item in items
                )
            )
            merged_breakdown["hook_gap"] = round(
                min(
                    float(
                        item.get(
                            "hook_gap",
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "hook_gap", 0.0
                            ),
                        )
                        or 0.0
                    )
                    for item in items
                ),
                4,
            )
            merged_breakdown["tail_gap"] = round(
                max(
                    float(
                        item.get(
                            "tail_gap",
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "tail_gap", 0.0
                            ),
                        )
                        or 0.0
                    )
                    for item in items
                ),
                4,
            )
            merged_breakdown["story_context_score"] = round(
                min(
                    1.0,
                    max(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "story_context_score", 0.0
                            )
                            or 0.0
                        )
                        for item in items
                    )
                    + 0.10,
                ),
                4,
            )
            merged_breakdown["story_clarity_score"] = round(
                min(
                    1.0,
                    max(
                        float(
                            item.get(
                                "story_clarity_score",
                                dict(item.get("score_breakdown", {}) or {}).get(
                                    "story_clarity_score", 0.0
                                ),
                            )
                            or 0.0
                        )
                        for item in items
                    )
                    + 0.08,
                ),
                4,
            )
            merged_breakdown["face_presence"] = round(
                min(
                    1.0,
                    max(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "face_presence", 0.0
                            )
                            or 0.0
                        )
                        for item in items
                    ),
                ),
                4,
            )
            merged_breakdown["person_presence"] = round(
                min(
                    1.0,
                    max(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "person_presence", 0.0
                            )
                            or 0.0
                        )
                        for item in items
                    ),
                ),
                4,
            )
            merged_breakdown["subject_presence"] = round(
                min(
                    1.0,
                    max(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "subject_presence", 0.0
                            )
                            or 0.0
                        )
                        for item in items
                    ),
                ),
                4,
            )
            merged_breakdown["face_evidence_score"] = round(
                min(
                    1.0,
                    max(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "face_evidence_score", 0.0
                            )
                            or 0.0
                        )
                        for item in items
                    ),
                ),
                4,
            )
            merged_breakdown["visual_subject_score"] = round(
                min(
                    1.0,
                    max(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "visual_subject_score", 0.0
                            )
                            or 0.0
                        )
                        for item in items
                    ),
                ),
                4,
            )
            merged_breakdown["reframe_feasibility_score"] = round(
                min(
                    1.0,
                    max(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "reframe_feasibility_score", 0.0
                            )
                            or 0.0
                        )
                        for item in items
                    ),
                ),
                4,
            )
            merged_breakdown["empty_frame_risk"] = round(
                max(
                    0.0,
                    min(
                        float(
                            dict(item.get("score_breakdown", {}) or {}).get(
                                "empty_frame_risk", 1.0
                            )
                            or 1.0
                        )
                        for item in items
                    ),
                ),
                4,
            )
            merged_breakdown["review_pass_used"] = True
            merged_breakdown["review_pass_window_count"] = len(items)
            merged_breakdown["review_pass_chain_span_seconds"] = merged[
                "review_pass_chain_span_seconds"
            ]
            merged_breakdown["review_pass_source_bucket"] = (
                f"{chain_source}:{chain_bucket}"
            )
            merged["score_breakdown"] = merged_breakdown
            score, fallback_breakdown = self._score_story_candidate_timeout_fallback(
                merged
            )
            merged["score"] = round(
                max(float(merged.get("score", 0.0) or 0.0), score)
                + min(0.12, 0.02 * (len(items) - 1)),
                4,
            )
            merged["score_breakdown"].update(fallback_breakdown)
            merged["score_breakdown"]["review_pass_used"] = True
            merged["score_breakdown"]["review_pass_window_count"] = len(items)
            merged["score_breakdown"]["review_pass_chain_span_seconds"] = merged[
                "review_pass_chain_span_seconds"
            ]
            merged["score_breakdown"]["story_unit_type"] = merged.get(
                "story_unit_type", "stitched_context_story"
            )
            review_candidates.append(merged)

        for candidate in pool:
            start = float(candidate.get("start", 0.0) or 0.0)
            end = float(candidate.get("end", start) or start)
            bucket, source = self._candidate_review_bucket(candidate)
            overlap_with_strict = any(
                max(0.0, min(end, strict_end) - max(start, strict_start))
                / max(0.001, max(end, strict_end) - min(start, strict_start))
                > 0.58
                for strict_start, strict_end in strict_spans
            )
            if overlap_with_strict:
                continue
            if not chain:
                chain = [candidate]
                chain_span_start = start
                chain_span_end = end
                chain_bucket = bucket
                chain_source = source
                continue
            gap = max(0.0, start - chain_span_end)
            same_bucket = bucket == chain_bucket
            same_source = source == chain_source
            expanded_span = max(chain_span_end, end) - min(chain_span_start, start)
            continuity_score = self._candidate_continuity_score(
                chain[-1], candidate, max_gap=segment_merge_gap_seconds
            )
            same_thread = (
                str(candidate.get("story_thread_id", ""))
                == str(chain[-1].get("story_thread_id", ""))
                if candidate.get("story_thread_id") and chain[-1].get("story_thread_id")
                else True
            )
            if (
                same_bucket
                and same_source
                and same_thread
                and gap <= chain_gap_seconds
                and expanded_span <= max_stitched_seconds
                and len(chain) < max_chain_windows
                and continuity_score >= review_pass_semantic_threshold
            ):
                chain.append(candidate)
                chain_span_start = min(chain_span_start, start)
                chain_span_end = max(chain_span_end, end)
                continue
            _flush_chain(chain)
            chain = [candidate]
            chain_span_start = start
            chain_span_end = end
            chain_bucket = bucket
            chain_source = source
        _flush_chain(chain)

        review_candidates.sort(
            key=lambda item: (
                float(
                    item.get("score_breakdown", {}).get("face_evidence_score", 0.0)
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get("story_clarity_score", 0.0)
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get("story_interest_score", 0.0)
                    or 0.0
                ),
                float(item.get("score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        return review_candidates

    def _merge_story_candidates(
        self, left: dict, right: dict, continuity_score: float
    ) -> dict:
        merged = dict(left)
        merged["start"] = round(min(float(left["start"]), float(right["start"])), 3)
        merged["end"] = round(max(float(left["end"]), float(right["end"])), 3)
        merged["window_start"] = round(
            min(
                float(left.get("window_start", left["start"])),
                float(right.get("window_start", right["start"])),
            ),
            3,
        )
        merged["window_end"] = round(
            max(
                float(left.get("window_end", left["end"])),
                float(right.get("window_end", right["end"])),
            ),
            3,
        )
        merged["stitched_story_unit"] = True
        merged["story_unit_type"] = "stitched_context_story"
        merged["story_thread_id"] = (
            left.get("story_thread_id")
            or right.get("story_thread_id")
            or self._story_thread_id(left)
        )
        merged["story_coherence_score"] = round(
            max(
                float(left.get("story_coherence_score", 0.0) or 0.0),
                float(right.get("story_coherence_score", 0.0) or 0.0),
                continuity_score,
            ),
            4,
        )
        merged["coherence_merge_reason"] = "adjacent_thread_continuation"
        merged["coherence_rejection_reason"] = ""
        merged["stitched_from_candidates"] = [
            [round(float(left["start"]), 3), round(float(left["end"]), 3)],
            [round(float(right["start"]), 3), round(float(right["end"]), 3)],
        ]
        merged["stitch_reason"] = "adjacent_context_continuation"
        merged["story_continued_after_pause"] = True
        merged_breakdown = dict(left.get("score_breakdown", {}))
        right_breakdown = dict(right.get("score_breakdown", {}))
        merged_breakdown["hook_score"] = round(
            max(
                float(merged_breakdown.get("hook_score", 0.0)),
                float(right_breakdown.get("hook_score", 0.0)),
            ),
            4,
        )
        merged_breakdown["development_score"] = round(
            max(
                float(merged_breakdown.get("development_score", 0.0)),
                float(right_breakdown.get("development_score", 0.0)),
            ),
            4,
        )
        merged_breakdown["closure_score"] = round(
            max(
                float(merged_breakdown.get("closure_score", 0.0)),
                float(right_breakdown.get("closure_score", 0.0)),
            ),
            4,
        )
        merged_breakdown["story_clarity_score"] = round(
            max(
                float(merged_breakdown.get("story_clarity_score", 0.0)),
                float(right_breakdown.get("story_clarity_score", 0.0)),
                continuity_score,
            ),
            4,
        )
        merged_breakdown["continuity_score"] = continuity_score
        merged_breakdown["story_context_score"] = round(
            max(
                float(merged_breakdown.get("story_context_score", 0.0)),
                float(right_breakdown.get("story_context_score", 0.0)),
                continuity_score,
            ),
            4,
        )
        merged["score_breakdown"] = merged_breakdown
        merged["score"] = round(
            max(float(left.get("score", 0.0)), float(right.get("score", 0.0)))
            + 0.08
            + continuity_score * 0.12,
            4,
        )
        return merged

    def _apply_story_stitching(self, candidates: list[dict]):
        if (
            not bool(self.cfg.get("story_stitching_enabled", True))
            or len(candidates) < 2
        ):
            return candidates
        max_eval = max(2, int(self.cfg.get("max_stitch_pairs_to_evaluate", 6)))
        max_stitched_seconds = float(self.cfg.get("max_stitched_story_seconds", 60))
        require_payoff_gain = bool(self.cfg.get("stitch_requires_payoff_gain", True))
        coherence_threshold = float(self.cfg.get("story_coherence_threshold", 0.62))
        sorted_candidates = sorted(candidates, key=lambda item: float(item["start"]))
        merged_results = []
        used = set()
        evaluations = 0
        for index in range(len(sorted_candidates) - 1):
            if index in used or index + 1 in used or evaluations >= max_eval:
                continue
            left = sorted_candidates[index]
            right = sorted_candidates[index + 1]
            total_seconds = float(right["end"]) - float(left["start"])
            if total_seconds > max_stitched_seconds:
                continue
            continuity_score = self._candidate_continuity_score(left, right)
            coherence_score = self._story_pair_coherence_score(left, right)
            if coherence_score < coherence_threshold:
                continue
            left_duration = float(left["end"]) - float(left["start"])
            right_duration = float(right["end"]) - float(right["start"])
            left_policy = left.get(
                "duration_policy"
            ) or self._candidate_duration_policy(left)
            right_policy = right.get(
                "duration_policy"
            ) or self._candidate_duration_policy(right)
            left["duration_policy"] = dict(left_policy)
            right["duration_policy"] = dict(right_policy)
            left_min_publishable_seconds = float(
                left_policy.get(
                    "min_publishable_seconds",
                    self.cfg.get("min_publishable_seconds", 35),
                )
                or self.cfg.get("min_publishable_seconds", 35)
            )
            right_min_publishable_seconds = float(
                right_policy.get(
                    "min_publishable_seconds",
                    self.cfg.get("min_publishable_seconds", 35),
                )
                or self.cfg.get("min_publishable_seconds", 35)
            )
            pair_min_publishable_seconds = min(
                left_min_publishable_seconds, right_min_publishable_seconds
            )
            short_story_pair = (
                total_seconds < pair_min_publishable_seconds
                or left_duration < left_min_publishable_seconds
                or right_duration < right_min_publishable_seconds
            )
            min_continuity = 0.52 if not short_story_pair else 0.40
            if continuity_score < min_continuity:
                continue
            left_closure = float(
                left.get("score_breakdown", {}).get("closure_score", 0.0)
            )
            right_closure = float(
                right.get("score_breakdown", {}).get("closure_score", 0.0)
            )
            if require_payoff_gain and right_closure <= left_closure + 0.04:
                if (
                    not short_story_pair
                    and max(
                        float(
                            left.get("score_breakdown", {}).get(
                                "story_interest_score", 0.0
                            )
                            or 0.0
                        ),
                        float(
                            right.get("score_breakdown", {}).get(
                                "story_interest_score", 0.0
                            )
                            or 0.0
                        ),
                    )
                    < 0.66
                ):
                    continue
                if (
                    short_story_pair
                    and max(
                        float(
                            left.get("score_breakdown", {}).get(
                                "story_interest_score", 0.0
                            )
                            or 0.0
                        ),
                        float(
                            right.get("score_breakdown", {}).get(
                                "story_interest_score", 0.0
                            )
                            or 0.0
                        ),
                    )
                    < 0.58
                ):
                    continue
            if (
                short_story_pair
                and max(
                    float(
                        left.get("score_breakdown", {}).get("visible_stakes_score", 0.0)
                        or 0.0
                    ),
                    float(
                        right.get("score_breakdown", {}).get(
                            "visible_stakes_score", 0.0
                        )
                        or 0.0
                    ),
                )
                < 0.50
                and max(
                    float(
                        left.get("score_breakdown", {}).get("sound_off_hook_score", 0.0)
                        or 0.0
                    ),
                    float(
                        right.get("score_breakdown", {}).get(
                            "sound_off_hook_score", 0.0
                        )
                        or 0.0
                    ),
                )
                < 0.58
            ):
                continue
            merged_candidate = self._merge_story_candidates(
                left, right, continuity_score
            )
            merged_candidate["story_coherence_score"] = round(
                max(
                    float(merged_candidate.get("story_coherence_score", 0.0) or 0.0),
                    coherence_score,
                ),
                4,
            )
            merged_candidate["coherence_merge_reason"] = "story_thread_continuation"
            merged_candidate["coherence_rejection_reason"] = ""
            merged_results.append(merged_candidate)
            used.add(index)
            used.add(index + 1)
            evaluations += 1
        for index, candidate in enumerate(sorted_candidates):
            if index not in used:
                merged_results.append(candidate)
        merged_results.sort(
            key=lambda item: (
                float(item.get("score", 0.0)),
                float(
                    item.get("score_breakdown", {}).get(
                        "preview_interestingness_score", 0.0
                    )
                ),
                float(item.get("score_breakdown", {}).get("story_clarity_score", 0.0)),
            ),
            reverse=True,
        )
        return merged_results

    def _selection_admission_score(self, candidate: dict) -> float:
        breakdown = dict(candidate.get("score_breakdown", {}) or {})
        story_clarity = float(
            breakdown.get(
                "story_clarity_score", candidate.get("story_clarity_score", 0.0)
            )
            or 0.0
        )
        story_completion = max(
            float(
                breakdown.get(
                    "story_completion_score",
                    candidate.get("story_completion_score", 0.0),
                )
                or 0.0
            ),
            float(
                breakdown.get(
                    "story_completeness_score",
                    candidate.get("story_completeness_score", 0.0),
                )
                or 0.0
            ),
            float(
                breakdown.get("closure_score", candidate.get("closure_score", 0.0))
                or 0.0
            ),
        )
        speech_coverage = float(
            breakdown.get("speech_coverage", candidate.get("speech_coverage", 0.0))
            or 0.0
        )
        estimated_turns = float(
            breakdown.get("estimated_turns", candidate.get("estimated_turns", 0.0))
            or 0.0
        )
        story_context = float(
            breakdown.get(
                "story_context_score", candidate.get("story_context_score", 0.0)
            )
            or 0.0
        )
        context_completeness = max(
            float(
                breakdown.get(
                    "context_completeness_score",
                    candidate.get("context_completeness_score", 0.0),
                )
                or 0.0
            ),
            story_context,
            float(
                breakdown.get(
                    "dialogue_exchange_score",
                    candidate.get("dialogue_exchange_score", 0.0),
                )
                or 0.0
            )
            * 0.35,
        )
        hook_gap = float(
            breakdown.get("hook_gap", candidate.get("hook_gap", 0.0)) or 0.0
        )
        hook_score = max(
            float(breakdown.get("hook_score", candidate.get("hook_score", 0.0)) or 0.0),
            float(
                breakdown.get(
                    "first_second_hook_score",
                    candidate.get("first_second_hook_score", 0.0),
                )
                or 0.0
            ),
            float(
                breakdown.get(
                    "sound_off_hook_score", candidate.get("sound_off_hook_score", 0.0)
                )
                or 0.0
            ),
            float(
                breakdown.get(
                    "premise_signal_score", candidate.get("premise_signal_score", 0.0)
                )
                or 0.0
            ),
        )
        silence_ratio = float(
            breakdown.get("silence_ratio", candidate.get("silence_ratio", 1.0)) or 1.0
        )
        duration = float(
            breakdown.get("duration", candidate.get("duration", 0.0)) or 0.0
        )
        score = float(candidate.get("score", 0.0) or 0.0)
        story_unit_type = str(
            breakdown.get(
                "story_unit_type", candidate.get("story_unit_type", "dialogue_cluster")
            )
            or "dialogue_cluster"
        ).lower()
        stitched_bonus = (
            0.08
            if bool(candidate.get("stitched_story_unit", False))
            or story_unit_type == "stitched_context_story"
            else 0.0
        )
        fallback_penalty = (
            0.04
            if story_unit_type == "fallback_window"
            and not bool(candidate.get("stitched_story_unit", False))
            else 0.0
        )
        hook_score = max(
            0.0,
            1.0
            - (hook_gap / max(0.5, float(self.cfg.get("hook_max_lead_seconds", 4.5)))),
        )
        duration_policy = candidate.get(
            "duration_policy"
        ) or self._candidate_duration_policy(candidate)
        candidate["duration_policy"] = dict(duration_policy)
        admission_target_seconds = float(
            duration_policy.get(
                "target_seconds", self.cfg.get("target_story_seconds", 45)
            )
            or self.cfg.get("target_story_seconds", 45)
        )
        duration_floor = max(
            35.0,
            float(
                duration_policy.get(
                    "min_publishable_seconds",
                    self.cfg.get("min_publishable_seconds", 35),
                )
                or self.cfg.get("min_publishable_seconds", 35)
            ),
        )
        duration_fit = min(1.0, duration / max(1.0, admission_target_seconds))
        duration_floor_penalty = max(
            0.0, (duration_floor - duration) / max(1.0, duration_floor)
        )
        tension_bonus = 0.0
        tension_context_score = self._candidate_tension_context_score(candidate)
        if self._story_mode() == "tension":
            tension_bonus = tension_context_score * 0.08
        elif self._story_mode() == "auto" and tension_context_score >= 0.62:
            tension_bonus = (tension_context_score - 0.62) * 0.08
        return round(
            max(
                0.0,
                min(
                    1.0,
                    story_clarity * 0.23
                    + story_completion * 0.19
                    + context_completeness * 0.15
                    + hook_score * 0.15
                    + speech_coverage * 0.11
                    + min(1.0, estimated_turns / 5.0) * 0.08
                    + max(0.0, 1.0 - silence_ratio) * 0.05
                    + duration_fit * 0.03
                    + max(0.0, score) * 0.02
                    + tension_bonus
                    + stitched_bonus
                    - duration_floor_penalty * 0.22
                    - fallback_penalty,
                ),
            ),
            4,
        )

    def pick_candidates(self, video_path, progress_callback=None):
        self._reset_watchdog_stats()
        self._audio_cache_stats = {
            "episode_audio_cache_hits": 0,
            "episode_audio_cache_misses": 0,
            "audio_summary_cache_hits": 0,
            "audio_summary_cache_misses": 0,
        }
        _emit(progress_callback, "discovering", "Detecting source scenes")
        
        # NEW (2026-06-14): Transcribe full episode if story-centric mode is enabled
        use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", False))
        if use_story_pipeline:
            _emit(progress_callback, "transcribing", "Transcribing full episode for story analysis")
            self.subtitle_info = self._transcribe_full_episode(video_path)
            if self.subtitle_info and self.subtitle_info.get('segments'):
                _emit(
                    progress_callback, 
                    "transcribing", 
                    f"Episode transcription complete: {len(self.subtitle_info.get('segments', []))} segments"
                )
            else:
                _emit(
                    progress_callback,
                    "warning",
                    "Episode transcription returned no segments; falling back to legacy mode"
                )
                # Disable story pipeline for this run if transcription failed
                self.cfg["use_story_centric_pipeline"] = False
        
        story_candidates = []
        rejected = []
        gate_reasons = Counter()
        gate_admissions = Counter()
        windows = self._candidate_windows(video_path)
        for window_start, window_end, source in windows:
            _emit(
                progress_callback,
                "building_context",
                f"Building story candidates {window_start:.2f}-{window_end:.2f}",
            )
            summary = self._extract_audio_summary(video_path, window_start, window_end)
            admission = self._dialogue_flow_admission(summary)
            gate_reasons[admission.get("reason", "low_dialogue_flow")] += 1
            if not bool(admission.get("admit", False)):
                rejected.append(
                    {
                        "candidate": {
                            "start": window_start,
                            "end": window_end,
                            "source": source,
                        },
                        "reason": str(admission.get("reason", "low_dialogue_flow")),
                    }
                )
                continue
            gate_admissions[str(admission.get("reason", "low_dialogue_flow"))] += 1
            built = self._build_story_candidates_from_turns_linear(
                window_start, window_end, source, summary
            )
            if not built:
                built = self._build_story_candidates_from_window(
                    window_start, window_end, source, summary
                )
            if not built:
                fallback = self._fallback_window_candidate(
                    window_start, window_end, source, summary
                )
                # PHASE 3: Remove artificial candidate injection
                # If story_pipeline returns no candidates, respect that decision
                if fallback is not None:
                    built = [fallback]
            story_candidates.extend(built)

        if not story_candidates:
            selection_starvation_reasons = Counter()
            for item in rejected:
                reason = item.get("reason", "unknown")
                selection_starvation_reasons[
                    self._selection_starvation_bucket(reason, item.get("candidate"))
                ] += 1
            self._last_selection_stats = {
                "total_windows": len(windows),
                "total_story_candidates": 0,
                "publishable_candidates": 0,
                "rejection_reasons": {},
                "selection_starvation_reasons": dict(selection_starvation_reasons),
                "selection_starvation_visual": int(
                    selection_starvation_reasons.get("visual_starvation", 0)
                ),
                "selection_starvation_subtitle": int(
                    selection_starvation_reasons.get("subtitle_starvation", 0)
                ),
                "selection_starvation_boundary": int(
                    selection_starvation_reasons.get("boundary_starvation", 0)
                ),
                "selection_starvation_vad": int(
                    selection_starvation_reasons.get("vad_starvation", 0)
                ),
                "main_rejection_reason": "no_story_candidates",
                "main_rejection_bucket": max(
                    selection_starvation_reasons, key=selection_starvation_reasons.get
                )
                if selection_starvation_reasons
                else "vad_starvation",
                "review_pass_used": False,
                "review_pass_considered": False,
                "review_pass_candidates": 0,
                "review_pass_stitched_candidates": 0,
                "review_pass_rescued_outputs": 0,
                "selection_starvation_reasons": dict(selection_starvation_reasons),
                "selection_starvation_visual": int(
                    selection_starvation_reasons.get("visual_starvation", 0)
                ),
                "selection_starvation_subtitle": int(
                    selection_starvation_reasons.get("subtitle_starvation", 0)
                ),
                "selection_starvation_boundary": int(
                    selection_starvation_reasons.get("boundary_starvation", 0)
                ),
                "selection_starvation_vad": int(
                    selection_starvation_reasons.get("vad_starvation", 0)
                ),
                "audio_gate_reasons": dict(gate_reasons),
                "audio_gate_admissions": dict(gate_admissions),
                "audio_summary_cache_hits": int(
                    self._audio_cache_stats.get("audio_summary_cache_hits", 0) or 0
                ),
                "audio_summary_cache_misses": int(
                    self._audio_cache_stats.get(
                        "audio_summary_cache_misses", len(windows)
                    )
                    or len(windows)
                ),
                "episode_audio_cache_hits": int(
                    self._audio_cache_stats.get("episode_audio_cache_hits", 0) or 0
                ),
                "episode_audio_cache_misses": int(
                    self._audio_cache_stats.get(
                        "episode_audio_cache_misses", len(windows)
                    )
                    or len(windows)
                ),
                "dialogue_audio_mismatch_candidates": 0,
            }
            return [], rejected
        review_fast_mode_enabled = bool(self.cfg.get("review_fast_mode_enabled", False))
        review_fast_story_candidate_cap = max(
            8, int(self.cfg.get("review_fast_story_candidate_cap", 24) or 24)
        )
        if (
            review_fast_mode_enabled
            and len(story_candidates) > review_fast_story_candidate_cap
        ):
            story_candidates = story_candidates[:review_fast_story_candidate_cap]

        story_candidates.sort(
            key=lambda item: (
                self._selection_admission_score(item),
                float(
                    item.get("score_breakdown", {}).get(
                        "story_completion_score",
                        item.get("story_completion_score", 0.0),
                    )
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get(
                        "story_clarity_score", item.get("story_clarity_score", 0.0)
                    )
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get(
                        "story_context_score", item.get("story_context_score", 0.0)
                    )
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get(
                        "hook_score", item.get("hook_score", 0.0)
                    )
                    or 0.0
                ),
                item["score"],
            ),
            reverse=True,
        )
        quantity_first = (
            str(self.cfg.get("selection_policy", "quality_first")).lower()
            == "quantity_first"
        )
        quality_first = self._quality_profile() == "quality_first"
        requested_shorts = int(self.cfg.get("max_shorts", 3))
        episode_policy = self._episode_story_policy(story_candidates)
        story_mode = str(
            episode_policy.get("story_mode", self._story_mode()) or self._story_mode()
        )
        admission_fraction = float(
            episode_policy.get(
                "selection_admission_fraction",
                self.cfg.get("selection_admission_fraction", 0.20),
            )
        )
        admission_target = int(
            episode_policy.get(
                "selection_admission_target",
                ceil(len(story_candidates) * admission_fraction),
            )
        )
        admission_cap = int(
            episode_policy.get(
                "selection_admission_cap",
                min(
                    len(story_candidates),
                    max(
                        int(self.cfg.get("selection_admission_min_pool", 6)),
                        admission_target,
                    ),
                ),
            )
        )
        admission_sorted_candidates = sorted(
            story_candidates,
            key=lambda item: (
                self._selection_admission_score(item),
                float(
                    item.get("score_breakdown", {}).get(
                        "story_completion_score",
                        item.get("story_completion_score", 0.0),
                    )
                    or 0.0
                ),
                float(item.get("story_clarity_score", 0.0) or 0.0),
                float(
                    item.get("score_breakdown", {}).get(
                        "story_context_score", item.get("story_context_score", 0.0)
                    )
                    or 0.0
                ),
                float(
                    item.get("score_breakdown", {}).get(
                        "hook_score", item.get("hook_score", 0.0)
                    )
                    or 0.0
                ),
                float(item.get("score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        admission_pool = admission_sorted_candidates[:admission_cap]
        if bool(self.cfg.get("ranking_visual_precheck_enabled", True)):
            prechecked = []
            for item in admission_pool:
                prechecked.append(self._ranking_visual_precheck(video_path, item))
            admission_pool = sorted(
                prechecked,
                key=lambda item: (
                    float(
                        item.get("score_breakdown", {}).get("face_evidence_score", 0.0)
                        or 0.0
                    ),
                    self._selection_admission_score(item),
                    float(
                        item.get("score_breakdown", {}).get(
                            "story_completion_score",
                            item.get("story_completion_score", 0.0),
                        )
                        or 0.0
                    ),
                    float(item.get("story_clarity_score", 0.0) or 0.0),
                    float(
                        item.get("score_breakdown", {}).get(
                            "story_context_score", item.get("story_context_score", 0.0)
                        )
                        or 0.0
                    ),
                    float(item.get("score", 0.0) or 0.0),
                ),
                reverse=True,
            )
        rerank_short_goal_cap = max(
            4, int(self.cfg.get("ranking_rerank_short_goal_cap", 8))
        )
        rerank_limit = max(
            int(self.cfg.get("max_candidates_for_rerank", 8)),
            int(self.cfg.get("max_candidates_for_semantic_preview", 12)),
            min(requested_shorts, rerank_short_goal_cap)
            * (3 if quantity_first else (2 if not quality_first else 1)),
        )
        rerank_pool = list(admission_pool)
        ranked = []
        soft_timeout_seconds = float(
            self.cfg.get(
                "ranking_soft_timeout_seconds",
                self.cfg.get("ranking_candidate_timeout_seconds", 90),
            )
        )
        hard_timeout_seconds = float(
            self.cfg.get(
                "ranking_hard_timeout_seconds", max(soft_timeout_seconds + 15.0, 90.0)
            )
        )
        fallback_timeout_seconds = float(
            self.cfg.get("ranking_fallback_timeout_seconds", 18)
        )
        heartbeat_seconds = float(self.cfg.get("heartbeat_interval_seconds", 30))
        timeout_fallback_enabled = bool(self.cfg.get("timeout_fallback_enabled", True))
        fast_fallback_first = (
            str(self.cfg.get("ranking_mode", "") or "").lower() == "fast_fallback_first"
        )
        watchdog_skip_policy = str(
            self.cfg.get("watchdog_skip_policy", "skip_or_defer") or "skip_or_defer"
        ).lower()
        deferred_candidates = []
        large_pool_timeout_threshold = max(
            16, int(self.cfg.get("ranking_large_pool_timeout_threshold", 24))
        )
        if len(rerank_pool) > large_pool_timeout_threshold:
            soft_timeout_seconds = min(
                soft_timeout_seconds,
                float(self.cfg.get("ranking_large_pool_soft_timeout_seconds", 20)),
            )
            hard_timeout_seconds = min(
                hard_timeout_seconds,
                float(self.cfg.get("ranking_large_pool_hard_timeout_seconds", 30)),
            )
        for candidate in rerank_pool:
            if fast_fallback_first:
                face_evidence = float(
                    candidate.get("score_breakdown", {}).get("face_evidence_score", 0.0)
                    or 0.0
                )
                if face_evidence >= 0.08:
                    score, breakdown = self._score_story_candidate_timeout_fallback(
                        candidate
                    )
                    candidate = dict(candidate)
                    candidate["score"] = score
                    breakdown = dict(breakdown or {})
                    breakdown["ranking_mode_used"] = "fast_visual_fallback"
                    breakdown["timeout_fallback_used"] = False
                    breakdown["ranking_visual_precheck_used"] = bool(
                        candidate.get("score_breakdown", {}).get(
                            "ranking_visual_precheck_used", False
                        )
                    )
                    candidate["score_breakdown"] = breakdown
                    ranked.append(candidate)
                    self._watchdog_stats["ranking_fast_fallback_used"] = (
                        self._watchdog_stats.get("ranking_fast_fallback_used", 0) + 1
                    )
                    continue
            _emit(
                progress_callback,
                "ranking",
                f"Scoring story {candidate['start']:.2f}-{candidate['end']:.2f}",
            )
            timed = _run_in_subprocess_with_timeout(
                "score_story",
                {"cfg": self.cfg, "video_path": video_path, "candidate": candidate},
                soft_timeout_seconds=soft_timeout_seconds,
                hard_timeout_seconds=hard_timeout_seconds,
                default=None,
                heartbeat_seconds=heartbeat_seconds,
                on_heartbeat=self._heartbeat_callback(
                    progress_callback,
                    "ranking",
                    f"Still scoring story {candidate['start']:.2f}-{candidate['end']:.2f}",
                ),
                on_soft_timeout=lambda _elapsed: self._watchdog_stats.__setitem__(
                    "ranking_timeouts",
                    self._watchdog_stats.get("ranking_timeouts", 0) + 1,
                ),
                on_hard_timeout=lambda _elapsed: self._watchdog_stats.__setitem__(
                    "hard_timeouts", self._watchdog_stats.get("hard_timeouts", 0) + 1
                ),
            )
            score_result = timed["result"] if isinstance(timed, dict) else None
            if bool((timed or {}).get("hard_timeout")):
                if timeout_fallback_enabled:
                    _emit(
                        progress_callback,
                        "warning",
                        f"Ranking timeout for story {candidate['start']:.2f}-{candidate['end']:.2f}; using safe fallback scoring",
                    )
                    fallback_timed = _run_in_subprocess_with_timeout(
                        "score_story_fallback",
                        {"cfg": self.cfg, "candidate": candidate},
                        soft_timeout_seconds=min(
                            fallback_timeout_seconds,
                            max(5.0, fallback_timeout_seconds * 0.6),
                        ),
                        hard_timeout_seconds=max(fallback_timeout_seconds, 8.0),
                        default=None,
                    )
                    score_result = (
                        fallback_timed["result"]
                        if isinstance(fallback_timed, dict)
                        else None
                    )
                    if score_result is not None:
                        self._watchdog_stats["ranking_fallback_used"] = (
                            self._watchdog_stats.get("ranking_fallback_used", 0) + 1
                        )
                        self._watchdog_stats["watchdog_fallback_used"] = (
                            self._watchdog_stats.get("watchdog_fallback_used", 0) + 1
                        )
                if score_result is None:
                    if (
                        watchdog_skip_policy == "skip_or_defer"
                        and float(candidate.get("story_clarity_score", 0.0) or 0.0)
                        >= 0.62
                    ):
                        deferred_item = dict(candidate)
                        deferred_item["_deferred_watchdog"] = True
                        deferred_candidates.append(deferred_item)
                        self._watchdog_stats["deferred_candidates"] = (
                            self._watchdog_stats.get("deferred_candidates", 0) + 1
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Deferred story after hard timeout {candidate['start']:.2f}-{candidate['end']:.2f}",
                        )
                    else:
                        self._watchdog_stats["skipped_due_to_timeout"] = (
                            self._watchdog_stats.get("skipped_due_to_timeout", 0) + 1
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Fallback scoring failed for story {candidate['start']:.2f}-{candidate['end']:.2f}; skipping",
                        )
                        rejected.append(
                            {"candidate": dict(candidate), "reason": "ranking_timeout"}
                        )
                    continue
            if score_result is None:
                self._watchdog_stats["ranking_failed"] = (
                    self._watchdog_stats.get("ranking_failed", 0) + 1
                )
                rejected.append(
                    {"candidate": dict(candidate), "reason": "ranking_failed"}
                )
                continue
            score, breakdown = score_result
            candidate = dict(candidate)
            candidate["score"] = score
            breakdown = dict(breakdown or {})
            breakdown.setdefault(
                "ranking_mode_used",
                "timeout_fallback"
                if bool(breakdown.get("timeout_fallback_used", False))
                else "deep_rank",
            )
            breakdown.setdefault(
                "timeout_fallback_used",
                bool(breakdown.get("timeout_fallback_used", False)),
            )
            candidate["score_breakdown"] = breakdown
            ranked.append(candidate)

        if deferred_candidates and bool(self.cfg.get("deferred_retry_tail_pass", True)):
            for candidate in sorted(
                deferred_candidates,
                key=lambda item: (
                    float(item.get("score", 0.0) or 0.0),
                    float(item.get("story_clarity_score", 0.0) or 0.0),
                ),
                reverse=True,
            )[: max(2, int(self.cfg.get("max_candidates_for_rerank", 6)) // 2)]:
                fallback_timed = _run_in_subprocess_with_timeout(
                    "score_story_fallback",
                    {"cfg": self.cfg, "candidate": candidate},
                    soft_timeout_seconds=min(
                        fallback_timeout_seconds,
                        max(5.0, fallback_timeout_seconds * 0.6),
                    ),
                    hard_timeout_seconds=max(fallback_timeout_seconds, 8.0),
                    default=None,
                )
                score_result = (
                    fallback_timed["result"]
                    if isinstance(fallback_timed, dict)
                    else None
                )
                if score_result is None:
                    self._watchdog_stats["skipped_due_to_timeout"] = (
                        self._watchdog_stats.get("skipped_due_to_timeout", 0) + 1
                    )
                    rejected.append(
                        {"candidate": dict(candidate), "reason": "ranking_timeout"}
                    )
                    continue
                self._watchdog_stats["watchdog_fallback_used"] = (
                    self._watchdog_stats.get("watchdog_fallback_used", 0) + 1
                )
                score, breakdown = score_result
                candidate = dict(candidate)
                candidate["score"] = score
                breakdown = dict(breakdown or {})
                breakdown.setdefault("ranking_mode_used", "timeout_fallback")
                breakdown.setdefault("timeout_fallback_used", True)
                candidate["score_breakdown"] = breakdown
                ranked.append(candidate)

        ranked = self._semantic_preview_rerank(
            video_path, ranked[:rerank_limit], progress_callback=progress_callback
        )
        ranked = self._assign_story_threads(ranked, progress_callback=progress_callback)
        ranked = self._apply_story_stitching(ranked)
        episode_policy = self._episode_story_policy(story_candidates, ranked)
        story_mode = str(episode_policy.get("story_mode", story_mode) or story_mode)
        story_type_counts = Counter(
            str(
                item.get("score_breakdown", {}).get(
                    "story_unit_type", item.get("story_unit_type", "dialogue_cluster")
                )
                or "dialogue_cluster"
            ).lower()
            for item in ranked
        )
        repeat_limit = max(1, int(self.cfg.get("story_type_repeat_limit", 2)))
        repeat_penalty = max(
            0.0, float(self.cfg.get("story_type_repeat_penalty", 0.018))
        )
        dominant_story_types = {
            "emotional_confession",
            "dialogue_cluster",
            "fallback_window",
        }
        story_type_priority_bonus = {
            "confrontation": 0.06,
            "accusation_denial": 0.05,
            "reveal_discovery": 0.05,
            "investigation_clue": 0.04,
            "threat_tension": 0.04,
            "rescue_urgency": 0.07,
            "danger_escape": 0.06,
            "impossible_choice": 0.06,
            "stitched_context_story": 0.08,
            "emotional_confession": 0.01,
            "dialogue_cluster": 0.0,
            "fallback_window": -0.03,
        }
        for item in ranked:
            story_unit_type = str(
                item.get("score_breakdown", {}).get(
                    "story_unit_type", item.get("story_unit_type", "dialogue_cluster")
                )
                or "dialogue_cluster"
            ).lower()
            excess = max(
                0, int(story_type_counts.get(story_unit_type, 0)) - repeat_limit
            )
            penalty = 0.0
            if excess > 0:
                penalty += min(0.12, excess * repeat_penalty)
            if story_unit_type in dominant_story_types:
                penalty += min(
                    0.06,
                    max(0, int(story_type_counts.get(story_unit_type, 0)) - 1)
                    * repeat_penalty
                    * 0.8,
                )
            item["score_breakdown"]["story_diversity_penalty"] = round(penalty, 4)
            item["score_breakdown"]["story_type_priority_bonus"] = round(
                story_type_priority_bonus.get(story_unit_type, 0.0), 4
            )

        ranked.sort(
            key=lambda item: (
                item["score_breakdown"].get("visible_stakes_score", 0.0),
                item["score_breakdown"].get("first_frame_clarity_score", 0.0),
                item["score_breakdown"].get(
                    "face_evidence_score",
                    max(
                        item["score_breakdown"].get("face_presence", 0.0),
                        item["score_breakdown"].get("person_presence", 0.0),
                        item["score_breakdown"].get("subject_presence", 0.0),
                    ),
                ),
                item["score_breakdown"].get(
                    "sound_off_premise_score",
                    item["score_breakdown"].get("sound_off_hook_score", 0.0),
                ),
                item["score_breakdown"].get("premise_signal_score", 0.0),
                item["score_breakdown"].get("visual_premise_strength", 0.0),
                item["score_breakdown"].get("first_second_hook_score", 0.0),
                item["score_breakdown"].get("sound_off_hook_score", 0.0),
                item["score_breakdown"].get("story_interest_score", 0.0),
                item["score_breakdown"].get("story_completeness_score", 0.0),
                item["score_breakdown"].get("watchability_score", 0.0),
                item["score_breakdown"].get("recommendation_readiness_score", 0.0),
                item["score_breakdown"].get("packaging_quality_score", 0.0),
                item["score_breakdown"].get("story_clarity_score", 0.0),
                item["score_breakdown"].get("story_type_priority_bonus", 0.0),
                -item["score_breakdown"].get("story_diversity_penalty", 0.0),
                -item["score_breakdown"].get("dialogue_dependency_penalty", 0.0),
                item["score_breakdown"]["speech_density"],
                item["score"],
            ),
            reverse=True,
        )

        picked = []
        publishable_story_override_candidates = 0
        review_pass_used = False
        review_pass_considered = False
        review_pass_candidates_count = 0
        review_pass_stitched_count = 0
        review_pass_rescued_outputs = 0
        quality_floor = float(episode_policy.get("quality_floor", 0.56))
        effective_max = min(
            int(episode_policy.get("output_budget", requested_shorts)),
            int(self.cfg.get("max_shorts", requested_shorts)),
        )
        review_fast_mode_enabled = bool(self.cfg.get("review_fast_mode_enabled", False))
        if review_fast_mode_enabled:
            effective_max = min(
                effective_max, int(self.cfg.get("review_fast_output_cap", 3) or 3)
            )
        effective_max = max(1, effective_max)
        for candidate in ranked:
            breakdown = candidate["score_breakdown"]
            clarity_threshold = float(self.cfg.get("story_clarity_threshold", 0.56)) - (
                0.06 if quantity_first else 0.0
            )
            if quality_first:
                clarity_threshold += 0.04
            reframe_threshold = float(
                self.cfg.get("reframe_feasibility_threshold", 0.34)
            ) + (0.08 if quality_first else 0.0)
            empty_risk_threshold = float(
                self.cfg.get("empty_frame_risk_reject_threshold", 0.58)
            ) - (0.10 if quality_first else 0.0)
            visual_premise_threshold = float(
                self.cfg.get("visual_premise_threshold", 0.48)
            )
            sound_off_hook_threshold = float(
                self.cfg.get("sound_off_hook_threshold", 0.56)
            )
            first_second_hook_threshold = float(
                self.cfg.get("first_second_hook_threshold", 0.54)
            )
            story_override = self._is_story_override_candidate(candidate)
            strong_story_gate = bool(
                breakdown.get("story_interest_score", 0.0)
                >= float(self.cfg.get("interestingness_threshold", 0.52))
                and breakdown.get("story_completeness_score", 0.0)
                >= float(self.cfg.get("min_story_payoff_score", 0.40))
                and breakdown.get("watchability_score", 0.0)
                >= float(self.cfg.get("watchability_threshold", 0.54))
                and breakdown.get("recommendation_readiness_score", 0.0)
                >= float(self.cfg.get("recommendation_readiness_threshold", 0.56))
            )
            premise_gate = bool(
                breakdown.get("visual_premise_strength", 0.0)
                >= visual_premise_threshold
                or breakdown.get("sound_off_hook_score", 0.0)
                >= sound_off_hook_threshold
                or breakdown.get("first_second_hook_score", 0.0)
                >= first_second_hook_threshold
                or breakdown.get("premise_signal_score", 0.0)
                >= visual_premise_threshold
            )
            face_evidence_score = max(
                float(breakdown.get("face_evidence_score", 0.0) or 0.0),
                float(breakdown.get("face_presence", 0.0) or 0.0),
                float(breakdown.get("person_presence", 0.0) or 0.0),
                float(breakdown.get("subject_presence", 0.0) or 0.0),
            )
            face_evidence_gate = face_evidence_score >= 0.08
            selection_visual_soft_gate = bool(
                face_evidence_gate
                and (
                    story_override
                    or strong_story_gate
                    or premise_gate
                    or not (
                        breakdown.get("visual_subject_score", 1.0)
                        < float(
                            self.cfg.get("selection_visual_subject_soft_floor", 0.32)
                        )
                        or breakdown.get("reframe_feasibility_score", 1.0)
                        < float(self.cfg.get("selection_reframe_soft_floor", 0.26))
                        or breakdown.get("empty_frame_risk", 0.0)
                        > float(
                            self.cfg.get("selection_empty_frame_soft_ceiling", 0.72)
                        )
                    )
                )
            )
            candidate["publishable_story_override"] = bool(story_override)
            candidate["selection_visual_soft_gate"] = bool(selection_visual_soft_gate)
            candidate["final_visual_hard_gate"] = True
            if story_override:
                publishable_story_override_candidates += 1
            reason = None
            # PHASE 1 FIX: Convert speech_density and silence_ratio to soft penalties
            # These should influence score, not hard-block candidates
            speech_penalty = 0.0
            if breakdown["speech_density"] < 0.18:
                speech_penalty = max(0.0, (0.18 - breakdown["speech_density"]) * 0.5)
            
            silence_penalty = 0.0
            if breakdown["silence_ratio"] > 0.58:
                silence_penalty = max(0.0, (breakdown["silence_ratio"] - 0.58) * 0.3)
            
            # Apply soft penalties to score instead of hard rejecting
            if speech_penalty > 0.0 or silence_penalty > 0.0:
                candidate["score"] = max(0.0, candidate.get("score", 0.0) - speech_penalty - silence_penalty)
                breakdown["speech_penalty_applied"] = round(speech_penalty, 4)
                breakdown["silence_penalty_applied"] = round(silence_penalty, 4)
            
            if not premise_gate and not story_override and not strong_story_gate:
                reason = "weak_premise_hook"
            elif breakdown.get("story_interest_score", 0.0) < float(
                self.cfg.get("interestingness_threshold", 0.52)
            ):
                reason = "low_story_interest"
            elif breakdown.get("story_completeness_score", 0.0) < float(
                self.cfg.get("min_story_payoff_score", 0.40)
            ):
                reason = "low_story_completeness"
            elif breakdown["story_clarity_score"] < clarity_threshold:
                reason = "low_story_clarity"
            elif breakdown.get("watchability_score", 1.0) < float(
                self.cfg.get("watchability_threshold", 0.54)
            ):
                reason = "low_watchability"
            elif bool(
                self.cfg.get("recommendation_readiness_enabled", True)
            ) and breakdown.get("recommendation_readiness_score", 1.0) < float(
                self.cfg.get("recommendation_readiness_threshold", 0.56)
            ):
                reason = "low_recommendation_readiness"
            elif breakdown.get("packaging_quality_score", 1.0) < float(
                self.cfg.get("packaging_quality_threshold", 0.52)
            ):
                reason = "weak_packaging_fit"
            elif not face_evidence_gate and not story_override:
                reason = "no_visual_subject"
            elif (
                breakdown.get("visual_subject_score", 1.0)
                < (0.46 if quality_first else 0.36)
                and not story_override
                and not selection_visual_soft_gate
            ):
                reason = "low_visual_viability"
            elif (
                breakdown.get("reframe_feasibility_score", 1.0) < reframe_threshold
                and not story_override
                and not selection_visual_soft_gate
            ):
                reason = "low_visual_viability"
            elif (
                breakdown.get("empty_frame_risk", 0.0) > empty_risk_threshold
                and not story_override
                and not selection_visual_soft_gate
            ):
                reason = "high_empty_frame_risk"
            elif breakdown.get("hook_score", 0.0) < float(
                self.cfg.get("hook_score_threshold", 0.34)
            ):
                reason = "weak_hook"
            elif breakdown.get("closure_score", 0.0) < float(
                self.cfg.get("closure_score_threshold", 0.32)
            ):
                reason = "no_payoff"
            if reason:
                rejected.append({"candidate": candidate, "reason": reason})
                continue
            candidate_score = float(candidate.get("score", 0.0) or 0.0)
            if (
                picked
                and not story_override
                and not strong_story_gate
                and not premise_gate
                and candidate_score < quality_floor
            ):
                candidate["selection_quality_floor_note"] = (
                    "below_quality_floor_soft_kept"
                )
            # PHASE 1 FIX: Change overlap from hard reject to dedupe only
            # Only reject if near-identical duplicate (>95% overlap)
            overlap = any(
                (
                    max(
                        0.0,
                        min(candidate["end"], other["end"])
                        - max(candidate["start"], other["start"]),
                    )
                    / max(
                        0.001,
                        max(candidate["end"], other["end"])
                        - min(candidate["start"], other["start"]),
                    )
                )
                > 0.95  # Only reject near-identical duplicates
                for other in picked
            )
            if overlap:
                # This is a duplicate, not just overlapping content
                rejected.append({"candidate": candidate, "reason": "duplicate"})
                continue
            picked.append(candidate)
            if len(picked) >= effective_max:
                break

        review_pass_min_outputs = min(
            effective_max,
            max(1, int(self.cfg.get("review_pass_min_outputs", 10) or 10)),
        )
        review_pass_output_cap = min(
            effective_max,
            max(len(picked), int(self.cfg.get("review_pass_output_cap", 20) or 20)),
        )
        if (
            bool(self.cfg.get("review_pass_enabled", True))
            and len(picked) < review_pass_min_outputs
        ):
            review_pass_considered = True
            review_candidates = self._build_review_pass_candidates(
                ranked, picked, progress_callback=progress_callback
            )
            review_pass_candidates_count = len(review_candidates)
            if review_candidates:
                review_pass_used = True
                for candidate in review_candidates:
                    if len(picked) >= review_pass_output_cap:
                        break
                    overlap = any(
                        (
                            max(
                                0.0,
                                min(float(candidate["end"]), float(other["end"]))
                                - max(float(candidate["start"]), float(other["start"])),
                            )
                            / max(
                                0.001,
                                max(float(candidate["end"]), float(other["end"]))
                                - min(float(candidate["start"]), float(other["start"])),
                            )
                        )
                        > 0.42
                        for other in picked
                    )
                    if overlap:
                        continue
                    if self._candidate_face_evidence(candidate) < float(
                        self.cfg.get("review_pass_face_floor", 0.10) or 0.10
                    ):
                        continue
                    picked.append(candidate)
                    review_pass_rescued_outputs += 1
                    if bool(candidate.get("stitched_story_unit", False)):
                        review_pass_stitched_count += 1

        if (
            not picked
            and ranked
            and bool(self.cfg.get("fallback_story_window_enabled", True))
        ):
            fallback_candidates = sorted(
                ranked,
                key=lambda item: (
                    float(
                        item.get("score_breakdown", {}).get(
                            "clarity_score",
                            item.get("score_breakdown", {}).get(
                                "story_clarity_score", 0.0
                            ),
                        )
                        or 0.0
                    ),
                    float(
                        item.get("score_breakdown", {}).get("story_clarity_score", 0.0)
                        or 0.0
                    ),
                    float(
                        item.get("score_breakdown", {}).get("watchability_score", 0.0)
                        or 0.0
                    ),
                    float(item.get("score", 0.0) or 0.0),
                ),
                reverse=True,
            )[: min(3, effective_max)]
            for candidate in fallback_candidates:
                if (
                    self._candidate_face_evidence(candidate)
                    < float(self.cfg.get("review_pass_face_floor", 0.10) or 0.10) * 0.5
                ):
                    continue
                picked.append(candidate)
                review_pass_rescued_outputs += 1
                if bool(candidate.get("stitched_story_unit", False)):
                    review_pass_stitched_count += 1
                if len(picked) >= effective_max:
                    break

        rejection_reasons = {}
        selection_starvation_reasons = Counter()
        for item in rejected:
            reason = item.get("reason", "unknown")
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            selection_starvation_reasons[
                self._selection_starvation_bucket(reason, item.get("candidate"))
            ] += 1
        main_rejection_reason = (
            max(rejection_reasons, key=rejection_reasons.get)
            if rejection_reasons
            else None
        )
        main_rejection_bucket = (
            max(selection_starvation_reasons, key=selection_starvation_reasons.get)
            if selection_starvation_reasons
            else None
        )
        self._last_selection_stats = {
            "total_windows": len(windows),
            "total_story_candidates": len(story_candidates),
            "publishable_candidates": len(picked),
            "episode_story_mode": story_mode,
            "episode_output_budget": int(effective_max),
            "episode_quality_floor": round(quality_floor, 4),
            "episode_tension_density": float(
                episode_policy.get("tension_density", 0.0) or 0.0
            ),
            "episode_arc_count": int(episode_policy.get("arc_count", 0) or 0),
            "selection_admission_fraction": round(admission_fraction, 4),
            "selection_admission_target": int(admission_target),
            "selection_admission_cap": int(admission_cap),
            "selection_admission_pool": len(admission_pool),
            "publishable_pool_before_final_visual_gate": len(ranked),
            "story_override_candidates": publishable_story_override_candidates,
            "review_pass_used": bool(review_pass_used),
            "review_pass_considered": bool(review_pass_considered),
            "review_pass_candidates": int(review_pass_candidates_count),
            "review_pass_stitched_candidates": int(review_pass_stitched_count),
            "review_pass_rescued_outputs": int(review_pass_rescued_outputs),
            "rejection_reasons": rejection_reasons,
            "selection_starvation_reasons": dict(selection_starvation_reasons),
            "selection_starvation_visual": int(
                selection_starvation_reasons.get("visual_starvation", 0)
            ),
            "selection_starvation_subtitle": int(
                selection_starvation_reasons.get("subtitle_starvation", 0)
            ),
            "selection_starvation_boundary": int(
                selection_starvation_reasons.get("boundary_starvation", 0)
            ),
            "selection_starvation_vad": int(
                selection_starvation_reasons.get("vad_starvation", 0)
            ),
            "main_rejection_reason": main_rejection_reason,
            "main_rejection_bucket": main_rejection_bucket,
            "ranking_timeouts": int(self._watchdog_stats.get("ranking_timeouts", 0)),
            "ranking_fallback_used": int(
                self._watchdog_stats.get("ranking_fallback_used", 0)
            ),
            "ranking_fast_fallback_used": int(
                self._watchdog_stats.get("ranking_fast_fallback_used", 0)
            ),
            "ranking_failed": int(self._watchdog_stats.get("ranking_failed", 0)),
            "semantic_preview_timeouts": int(
                self._watchdog_stats.get("semantic_preview_timeouts", 0)
            ),
            "semantic_preview_fallback_used": int(
                self._watchdog_stats.get("semantic_preview_fallback_used", 0)
            ),
            "slow_stage_events": int(self._watchdog_stats.get("slow_stage_events", 0)),
            "hard_timeouts": int(self._watchdog_stats.get("hard_timeouts", 0)),
            "deferred_candidates": int(
                self._watchdog_stats.get("deferred_candidates", 0)
            ),
            "skipped_due_to_timeout": int(
                self._watchdog_stats.get("skipped_due_to_timeout", 0)
            ),
            "watchdog_fallback_used": int(
                self._watchdog_stats.get("watchdog_fallback_used", 0)
            ),
            "final_visual_rejects": 0,
            "silent_parts_removed_total": 0,
            "pause_policy_failed_outputs": 0,
            "audio_gate_reasons": dict(gate_reasons),
            "audio_gate_admissions": dict(gate_admissions),
            "audio_summary_cache_hits": int(
                self._audio_cache_stats.get("audio_summary_cache_hits", 0) or 0
            ),
            "audio_summary_cache_misses": int(
                self._audio_cache_stats.get("audio_summary_cache_misses", 0) or 0
            ),
            "episode_audio_cache_hits": int(
                self._audio_cache_stats.get("episode_audio_cache_hits", 0) or 0
            ),
            "episode_audio_cache_misses": int(
                self._audio_cache_stats.get("episode_audio_cache_misses", 0) or 0
            ),
            "dialogue_audio_mismatch_candidates": int(
                sum(
                    1
                    for item in ranked
                    if float(
                        item.get("score_breakdown", {}).get(
                            "dialogue_audio_mismatch", 0.0
                        )
                        or 0.0
                    )
                    > 0.5
                )
            ),
        }
        
        # PHASE 3: Remove minimum candidate top-up
        # Respect quality gates; do not force quantity
        return picked, rejected

    def trim_silence_and_limit(
        self, video_path, start, end, out_dir, idx, progress_callback=None
    ):
        trimmed = os.path.join(out_dir, f"cand_{idx}_trimmed.mp4")
        source_window_seconds = max(0.0, float(end) - float(start))
        min_publishable_seconds = max(
            35.0, float(self.cfg.get("min_publishable_seconds", 35))
        )
        trim_silence_in_candidate_ms.last_stats = {
            "pause_removed_segments": [],
            "pause_kept_for_context": [],
            "pause_timeline": [],
            "pause_cut_count": 0,
            "pause_soft_keep_count": 0,
            "pause_story_keep_count": 0,
            "long_pause_cut_seconds_total": 0.0,
            "story_sensitive_pause_kept_seconds_total": 0.0,
            "pause_policy_applied": False,
            "pause_policy_failed": False,
            "pause_cut_segments_count": 0,
            "pause_cut_seconds_total": 0.0,
            "pause_output_trim_delta_seconds": 0.0,
            "pause_story_keep_reasons": [],
        }
        can_trim_silence = source_window_seconds >= max(20.0, min_publishable_seconds)
        if (
            self.cfg.get("drop_silent", True)
            and can_trim_silence
            and trim_silence_in_candidate_ms(
                video_path, start, end, trimmed, self.cfg, progress_callback
            )
        ):
            has_video, trimmed_duration = probe_video(trimmed)
            if has_video and trimmed_duration >= min_publishable_seconds:
                return trimmed
            _emit(
                progress_callback,
                "warning",
                f"Silence trim would shorten candidate below {min_publishable_seconds:.0f}s; keeping raw cut",
            )
            with contextlib.suppress(Exception):
                if os.path.exists(trimmed):
                    os.remove(trimmed)
        rc, _, _ = run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-ss",
                str(start),
                "-to",
                str(end),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                trimmed,
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            timeout=300,
        )
        if rc != 0 or not os.path.exists(trimmed):
            return None
        has_video, duration = probe_video(trimmed)
        if has_video and duration > float(self.cfg.get("max_short_seconds", 60)):
            cut = trimmed + ".cut.mp4"
            run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    trimmed,
                    "-ss",
                    "0",
                    "-t",
                    str(self.cfg.get("max_short_seconds", 60)),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    "-b:a",
                    "160k",
                    "-movflags",
                    "+faststart",
                    cut,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                ],
                timeout=180,
            )
            if os.path.exists(cut):
                os.replace(cut, trimmed)
        return trimmed

    def _maybe_compact_dialogue_after_subtitles(
        self,
        trimmed: str,
        subtitle_info: dict,
        out_dir: str,
        idx: int,
        candidate: dict | None = None,
        progress_callback=None,
    ):
        if not bool(self.cfg.get("remove_silent", True)):
            return trimmed, False
        if not subtitle_info or not subtitle_info.get("segments"):
            return trimmed, False
        compaction_cfg = (
            self._candidate_cfg(candidate, stage="default") if candidate else self.cfg
        )
        has_video, duration = probe_video(trimmed)
        if not has_video or duration <= 0.0:
            return trimmed, False
        min_publishable_seconds = max(
            35.0,
            float(
                compaction_cfg.get(
                    "min_publishable_seconds",
                    self.cfg.get("min_publishable_seconds", 35),
                )
                or self.cfg.get("min_publishable_seconds", 35)
            ),
        )
        if duration <= min_publishable_seconds + 8.0:
            return trimmed, False
        keep_segments, removed_segments = _subtitle_dialogue_keep_segments(
            subtitle_info, duration, compaction_cfg
        )
        if not removed_segments:
            return trimmed, False
        compacted = os.path.join(out_dir, f"cand_{idx}_dialogue_compact.mp4")
        _emit(
            progress_callback,
            "trimming",
            f"Compacting dialogue gaps for candidate {idx}",
        )
        ok, output_duration = _concat_video_segments(trimmed, keep_segments, compacted)
        if not ok:
            return trimmed, False
        if bool(compaction_cfg.get("compaction_integrity_check", True)):
            sanitized_ok, sanitized_duration = _sanitize_compacted_video(
                compacted, cfg=compaction_cfg
            )
            if sanitized_ok:
                output_duration = sanitized_duration or output_duration
            valid_ok, valid_duration = _validate_compacted_video_integrity(compacted)
            if valid_ok:
                output_duration = valid_duration or output_duration
            else:
                trim_silence_in_candidate_ms.last_stats["pause_policy_failed"] = True
                trim_silence_in_candidate_ms.last_stats["pause_policy_applied"] = False
                with contextlib.suppress(Exception):
                    os.remove(compacted)
                _emit(
                    progress_callback,
                    "warning",
                    f"Candidate {idx} dialogue compaction integrity failed; keeping original trim",
                )
                return trimmed, False
        trimmed_delta = max(0.0, float(duration) - float(output_duration or 0.0))
        if trimmed_delta < 0.35:
            with contextlib.suppress(Exception):
                os.remove(compacted)
            return trimmed, False
        pause_cut_seconds_total = round(
            sum(max(0.0, end - start) for start, end in removed_segments), 3
        )
        synthetic_pause_timeline = [
            {
                "start": float(start),
                "end": float(end),
                "duration": round(max(0.0, float(end) - float(start)), 3),
                "energy": 0.0,
                "continuation_bonus": 0.0,
                "silence_type": "dead_air"
                if max(0.0, float(end) - float(start)) >= 2.0
                else "unknown",
                "silence_confidence": 0.70
                if max(0.0, float(end) - float(start)) >= 2.0
                else 0.42,
                "max_allowed_silence": 1.5,
                "trim_allowed": True,
                "decision": "cut",
                "reason": "subtitle_dialogue_gap",
            }
            for start, end in removed_segments
        ]
        pacing_score = _pacing_score_from_pause_timeline(
            synthetic_pause_timeline,
            original_duration=duration,
            output_duration=float(output_duration or 0.0),
            subtitle_signals=dict((subtitle_info or {}).get("signals", {}) or {}),
        )
        trim_silence_in_candidate_ms.last_stats = {
            "pause_removed_segments": list(removed_segments),
            "pause_kept_for_context": [],
            "pause_timeline": synthetic_pause_timeline,
            "pause_cut_count": len(removed_segments),
            "pause_soft_keep_count": 0,
            "pause_story_keep_count": 0,
            "long_pause_cut_seconds_total": pause_cut_seconds_total,
            "story_sensitive_pause_kept_seconds_total": 0.0,
            "trimmed_silence_seconds": pause_cut_seconds_total,
            "silence_type_counts": {"dead_air": len(removed_segments)},
            "silence_trim_events": [
                {
                    "start": round(float(item["start"]), 3),
                    "end": round(float(item["end"]), 3),
                    "duration": round(float(item["duration"]), 3),
                    "silence_type": str(item.get("silence_type", "unknown")),
                    "silence_confidence": round(
                        float(item.get("silence_confidence", 0.0) or 0.0), 4
                    ),
                    "reason": str(item.get("reason", "")),
                }
                for item in synthetic_pause_timeline
            ],
            "pacing_score": pacing_score,
            "pause_policy_applied": True,
            "pause_policy_failed": False,
            "pause_cut_segments_count": len(removed_segments),
            "pause_cut_seconds_total": pause_cut_seconds_total,
            "pause_output_trim_delta_seconds": round(trimmed_delta, 3),
            "pause_story_keep_reasons": [],
        }
        return compacted, True

    def _extract_candidate_wav(self, video_path: str, out_dir: str, idx: int):
        wav = os.path.join(out_dir, f"cand_{idx}.wav")
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-ac",
                "1",
                "-ar",
                "16000",
                "-vn",
                wav,
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            timeout=180,
        )
        return wav if os.path.exists(wav) else None

    def _line_completion_info(self, subtitle_info: dict, duration: float):
        segments = subtitle_info.get("segments", [])
        if not segments:
            return (
                False,
                "no_subtitles",
                {
                    "start_boundary_reason": "missing_subtitles",
                    "end_boundary_reason": "missing_subtitles",
                },
            )
        first = segments[0]
        last = segments[-1]
        language = str(
            subtitle_info.get("language", self.cfg.get("subtitle_language", "auto"))
        ).lower()
        incomplete_endings = (
            INCOMPLETE_ENDINGS_EN
            if language.startswith("en")
            else INCOMPLETE_ENDINGS_RU
        )
        first_text = (first["text"] or "").strip().lower()
        last_text = (last["text"] or "").strip()
        first_token = re.findall(r"[A-Za-zА-Яа-яЁё0-9']+", first_text)
        start_ok = first["start"] <= 0.65 and (
            not first_token or first_token[0] not in incomplete_endings
        )
        punct_ok = last_text.endswith(TERMINAL_PUNCTUATION)
        ending_gap = max(0.0, duration - float(last["end"]))
        incomplete_tail = bool(
            last_text
            and last_text.split()[-1].lower().rstrip(",.:;!?") in incomplete_endings
        )
        semantic_signal = subtitle_info.get("signals", {}) or {}
        sentence_start_safe = bool(semantic_signal.get("sentence_start_safe", start_ok))
        sentence_end_safe = bool(semantic_signal.get("sentence_end_safe", punct_ok))
        hook_ok = float(semantic_signal.get("hook_score", 0.0)) >= float(
            self.cfg.get("hook_score_threshold", 0.34)
        )
        payoff_ok = bool(semantic_signal.get("story_has_payoff", False)) or float(
            semantic_signal.get("closure_score", 0.0)
        ) >= float(self.cfg.get("closure_score_threshold", 0.32))
        end_ok = (
            punct_ok
            or sentence_end_safe
            or (ending_gap >= 0.35 and semantic_signal.get("line_like_closure", False))
        )
        if incomplete_tail:
            end_ok = False
        start_ok = start_ok and sentence_start_safe
        story_boundary_confidence = max(
            0.0,
            min(
                1.0,
                (0.34 if sentence_start_safe else 0.10)
                + (0.28 if sentence_end_safe else 0.10)
                + min(0.16, float(semantic_signal.get("hook_score", 0.0)) * 0.18)
                + min(0.22, float(semantic_signal.get("closure_score", 0.0)) * 0.24),
            ),
        )
        meta = {
            "start_boundary_reason": "ok"
            if start_ok
            else (
                "mid_phrase_start"
                if first["start"] > 0.45 or not sentence_start_safe
                else "weak_hook"
            ),
            "end_boundary_reason": "ok"
            if end_ok and payoff_ok
            else (
                "dialogue_not_complete"
                if incomplete_tail or not payoff_ok
                else "weak_closure"
            ),
            "hook_ok": hook_ok,
            "story_has_payoff": payoff_ok,
            "sentence_start_safe": sentence_start_safe,
            "sentence_end_safe": sentence_end_safe,
            "story_boundary_confidence": round(story_boundary_confidence, 4),
            "boundary_expand_attempted": False,
            "boundary_expand_seconds_left": 0.0,
            "boundary_expand_seconds_right": 0.0,
            "rejected_after_boundary_expansion": False,
        }
        if not start_ok:
            return False, "starts_mid_phrase", meta
        if not end_ok or not payoff_ok:
            return False, "dialogue_not_complete", meta
        return True, "ok", meta

    def _expand_candidate_window_to_min_duration(
        self, candidate: dict, min_duration: float, subtitle_info: dict | None = None
    ):
        refined = dict(candidate)
        start = float(refined.get("start", 0.0) or 0.0)
        end = float(refined.get("end", 0.0) or 0.0)
        window_start = float(refined.get("window_start", start) or start)
        window_end = float(refined.get("window_end", end) or end)
        current_duration = max(0.0, end - start)
        min_duration = max(35.0, float(min_duration or 35.0))
        meta = {
            "duration_floor_seconds": round(min_duration, 3),
            "duration_before_expand": round(current_duration, 3),
            "duration_after_expand": round(current_duration, 3),
            "expanded_for_min_duration": False,
            "expanded_left_seconds": 0.0,
            "expanded_right_seconds": 0.0,
            "hook_shift_applied": False,
        }
        if current_duration >= min_duration:
            refined["duration_expansion_meta"] = meta
            return refined, False, meta

        available_left = max(0.0, start - window_start)
        available_right = max(0.0, window_end - end)
        if available_left + available_right <= 0.0:
            refined["duration_expansion_meta"] = meta
            return refined, False, meta

        signals = dict((subtitle_info or {}).get("signals", {}) or {})
        hook_gap = max(
            0.0, float(refined.get("hook_gap", candidate.get("hook_gap", 0.0)) or 0.0)
        )
        hook_risk = bool(
            signals.get("starts_mid_phrase", False)
            or not bool(signals.get("hook_ok", True))
            or hook_gap
            > max(0.35, float(self.cfg.get("hook_max_lead_seconds", 4.5)) * 0.12)
        )
        left_share = 0.68 if hook_risk else 0.52
        deficit = min_duration - current_duration
        expand_left = min(available_left, deficit * left_share)
        expand_right = min(available_right, max(0.0, deficit - expand_left))
        remaining = max(0.0, deficit - expand_left - expand_right)
        if remaining > 0.0 and available_left > expand_left:
            extra_left = min(available_left - expand_left, remaining)
            expand_left += extra_left
            remaining -= extra_left
        if remaining > 0.0 and available_right > expand_right:
            extra_right = min(available_right - expand_right, remaining)
            expand_right += extra_right
            remaining -= extra_right

        new_start = max(window_start, start - expand_left)
        new_end = min(window_end, end + expand_right)
        if (
            new_end - new_start < min_duration
            and available_left + available_right >= deficit
        ):
            total_target = min(
                window_end - window_start,
                max(min_duration, current_duration + available_left + available_right),
            )
            if hook_risk:
                new_start = max(window_start, new_end - total_target)
            else:
                new_end = min(window_end, new_start + total_target)
            if new_end - new_start < min_duration:
                new_start = window_start
                new_end = min(window_end, window_start + min_duration)

        new_duration = max(0.0, new_end - new_start)
        if new_duration < min_duration - 0.2:
            refined["duration_expansion_meta"] = meta
            return refined, False, meta

        meta["expanded_for_min_duration"] = True
        meta["expanded_left_seconds"] = round(max(0.0, start - new_start), 3)
        meta["expanded_right_seconds"] = round(max(0.0, new_end - end), 3)
        meta["duration_after_expand"] = round(new_duration, 3)
        meta["hook_shift_applied"] = bool(
            hook_risk and meta["expanded_left_seconds"] > 0.0
        )
        refined["start"] = round(new_start, 3)
        refined["end"] = round(new_end, 3)
        refined["duration"] = round(new_duration, 3)
        refined["score_breakdown"] = dict(refined.get("score_breakdown", {}) or {})
        refined["score_breakdown"]["duration"] = round(new_duration, 4)
        refined["duration_expansion_meta"] = meta
        refined["duration_expanded_for_minimum"] = True
        refined["duration_expansion_seconds_left"] = meta["expanded_left_seconds"]
        refined["duration_expansion_seconds_right"] = meta["expanded_right_seconds"]
        refined["duration_expansion_reason"] = "min_story_duration"
        return refined, True, meta

    def _maybe_refine_bounds(
        self, candidate: dict, subtitle_info: dict, current_duration: float
    ):
        if not bool(self.cfg.get("line_completion_required", True)):
            return (
                candidate,
                False,
                {
                    "start_boundary_reason": "skipped",
                    "end_boundary_reason": "skipped",
                    "story_continuation_used": False,
                },
            )
        passed, reason, meta = self._line_completion_info(
            subtitle_info, current_duration
        )
        if passed:
            meta["story_continuation_used"] = False
            return candidate, False, meta
        refined = dict(candidate)
        changed = False
        duration_policy = candidate.get(
            "duration_policy"
        ) or self._candidate_duration_policy(candidate, subtitle_info)
        max_story = min(
            _max_story_duration(self.cfg),
            float(
                duration_policy.get("hard_max_seconds", _max_story_duration(self.cfg))
            ),
        )
        story_continuation_used = False
        boundary_expand_attempted = False
        expand_left = 0.0
        expand_right = 0.0
        if reason == "dialogue_not_complete":
            boundary_expand_attempted = True
            right_cap = max(
                float(self.cfg.get("story_boundary_expand_right_seconds", 3.0)),
                min(12.0, max(3.0, max_story - current_duration)),
            )
            min_completion_extend = max(
                0.75,
                float(
                    self.cfg.get("story_boundary_min_completion_extend_seconds", 0.9)
                ),
            )
            new_end = min(
                candidate.get("window_end", candidate["end"]),
                candidate["start"] + max_story,
                candidate["end"] + max(min_completion_extend, min(3.0, right_cap)),
            )
            if new_end > candidate["end"] + 0.35:
                refined["end"] = round(new_end, 3)
                changed = True
                story_continuation_used = True
                expand_right = round(new_end - candidate["end"], 3)
        if reason in {"weak_hook", "starts_mid_phrase"}:
            boundary_expand_attempted = True
            pullback = float(
                self.cfg.get(
                    "story_boundary_expand_left_seconds",
                    6.0 if reason == "starts_mid_phrase" else 3.0,
                )
            )
            new_start = max(
                candidate.get("window_start", candidate["start"]),
                candidate["start"] - pullback,
            )
            if (
                candidate["end"] - new_start <= max_story
                and new_start < candidate["start"] - 0.45
            ):
                refined["start"] = round(new_start, 3)
                changed = True
                expand_left = round(candidate["start"] - new_start, 3)
        if reason == "starts_mid_phrase" and not changed:
            boundary_expand_attempted = True
            new_end = min(
                candidate.get("window_end", candidate["end"]),
                candidate["start"] + max_story,
                candidate["end"]
                + max(
                    4.0,
                    float(self.cfg.get("story_boundary_expand_right_seconds", 3.0))
                    * 1.5,
                ),
            )
            if (
                new_end > candidate["end"] + 0.45
                and new_end - candidate["start"] <= max_story
            ):
                refined["end"] = round(new_end, 3)
                changed = True
                expand_right = max(expand_right, round(new_end - candidate["end"], 3))
        meta["story_continuation_used"] = story_continuation_used
        meta["boundary_expand_attempted"] = boundary_expand_attempted
        meta["boundary_expand_seconds_left"] = expand_left
        meta["boundary_expand_seconds_right"] = expand_right
        meta["rejected_after_boundary_expansion"] = (
            boundary_expand_attempted and not changed
        )
        return refined, changed, meta

    def process_episode(self, video_path, progress_callback=None, stop_check=None):
        out_dir = self._output_dir(video_path)
        os.makedirs(out_dir, exist_ok=True)
        test_mode_enabled = bool(self.cfg.get("test_mode_enabled", False))
        test_candidate_rank = max(1, int(self.cfg.get("test_candidate_rank", 1) or 1))
        direct_candidate_mode = bool(
            test_mode_enabled and _looks_like_direct_candidate(video_path)
        )
        if direct_candidate_mode:
            self._clean_test_mode_outputs(out_dir)
        report = {
            "source_file": os.path.abspath(video_path),
            "output_dir": out_dir,
            "story_mode": self._story_mode(),
            **self._pipeline_identity,
            "status": "queued",
            "requested_max": 1
            if test_mode_enabled
            else int(self.cfg.get("max_shorts", 3)),
            "selected_candidates": [],
            "rejected_candidates": [],
            "generated_outputs": [],
            "warnings": [],
            "stage_timings": {},
            "stats": {
                "total_windows": 0,
                "total_story_candidates": 0,
                "publishable_candidates": 0,
                "publishable_pool_before_final_visual_gate": 0,
                "story_override_candidates": 0,
                "review_pass_used": False,
                "review_pass_considered": False,
                "review_pass_candidates": 0,
                "review_pass_stitched_candidates": 0,
                "review_pass_rescued_outputs": 0,
                "main_rejection_reason": None,
                "rejection_reasons": {},
                "selection_starvation_reasons": {},
                "selection_starvation_visual": 0,
                "selection_starvation_subtitle": 0,
                "selection_starvation_boundary": 0,
                "selection_starvation_vad": 0,
                "ranking_timeouts": 0,
                "ranking_fallback_used": 0,
                "ranking_failed": 0,
                "semantic_preview_timeouts": 0,
                "semantic_preview_fallback_used": 0,
                "slow_stage_events": 0,
                "hard_timeouts": 0,
                "deferred_candidates": 0,
                "skipped_due_to_timeout": 0,
                "watchdog_fallback_used": 0,
                "final_visual_rejects": 0,
                "silent_parts_removed_total": 0,
                "pause_policy_failed_outputs": 0,
                "kept_micro_pauses": 0,
                "titles_generated": 0,
                "titles_with_hashtags": 0,
                "titles_with_emojis": 0,
                "titles_without_hashtags": 0,
                "title_fallbacks": 0,
            },
        }
        try:
            report["status"] = "analyzing"
            started = _now()
            if direct_candidate_mode:
                has_video, duration = probe_video(video_path)
                if not has_video or duration <= 0.0:
                    report["status"] = "failed"
                    report["warnings"].append("Test mode input is not a valid video")
                    report["stats"]["main_rejection_reason"] = "invalid_test_input"
                    _dump_json(os.path.join(out_dir, "episode_report.json"), report)
                    return report
                candidates = [
                    {
                        "start": 0.0,
                        "end": round(duration, 3),
                        "window_start": 0.0,
                        "window_end": round(duration, 3),
                        "source": "test_mode_direct_candidate",
                        "_rank": 1,
                        "score": 0.5,
                        "story_clarity_score": 0.5,
                        "speech_density": 0.5,
                        "silence_ratio": 0.0,
                        "hook_gap": 0.0,
                        "tail_gap": 0.0,
                        "estimated_turns": 1,
                        "test_mode_synthetic_selection": True,
                        "score_breakdown": {
                            "speech_density": 0.5,
                            "silence_penalty": 0.0,
                            "face_presence": 0.5,
                            "motion": 0.5,
                            "audio_energy": 0.5,
                            "story_clarity_score": 0.5,
                            "visual_subject_score": 0.5,
                            "reframe_feasibility_score": 0.5,
                            "empty_frame_risk": 0.5,
                            "hook_score": 0.5,
                            "closure_score": 0.5,
                        },
                    }
                ]
                rejected = []
                report["stage_timings"]["selection_total_seconds"] = round(
                    _now() - started, 3
                )
                report["stats"].update(
                    {
                        "total_windows": 1,
                        "total_story_candidates": 1,
                        "publishable_candidates": 1,
                        "publishable_pool_before_final_visual_gate": 1,
                        "story_override_candidates": 0,
                        "review_pass_used": False,
                        "review_pass_considered": False,
                        "review_pass_candidates": 0,
                        "review_pass_stitched_candidates": 0,
                        "review_pass_rescued_outputs": 0,
                        "main_rejection_reason": None,
                        "rejection_reasons": {},
                        "selection_starvation_reasons": {},
                        "selection_starvation_visual": 0,
                        "selection_starvation_subtitle": 0,
                        "selection_starvation_boundary": 0,
                        "selection_starvation_vad": 0,
                        "ranking_timeouts": 0,
                        "ranking_fallback_used": 0,
                        "ranking_failed": 0,
                        "semantic_preview_timeouts": 0,
                        "semantic_preview_fallback_used": 0,
                        "slow_stage_events": 0,
                        "hard_timeouts": 0,
                        "deferred_candidates": 0,
                        "skipped_due_to_timeout": 0,
                        "watchdog_fallback_used": 0,
                        "test_mode_enabled": True,
                        "test_candidate_rank": 1,
                    }
                )
            else:
                candidates, rejected = self.pick_candidates(
                    video_path, progress_callback=progress_callback
                )
                report["stage_timings"]["selection_total_seconds"] = round(
                    _now() - started, 3
                )
                for rank_index, candidate in enumerate(candidates, start=1):
                    candidate["_rank"] = rank_index
                if test_mode_enabled:
                    chosen = [
                        item
                        for item in candidates
                        if int(item.get("_rank", 0)) == test_candidate_rank
                    ]
                    if not chosen:
                        report["status"] = "failed"
                        report["stats"].update(self._last_selection_stats)
                        report["stats"]["main_rejection_reason"] = (
                            "test_candidate_missing"
                        )
                        report["story_mode"] = report["stats"].get(
                            "episode_story_mode", report.get("story_mode", "auto")
                        )
                        report["warnings"].append(
                            f"Test mode candidate rank {test_candidate_rank} is not available"
                        )
                        _dump_json(os.path.join(out_dir, "episode_report.json"), report)
                        return report
                    candidates = chosen[:1]
            report["selected_candidates"], report["rejected_candidates"] = (
                candidates,
                rejected,
            )
            if not direct_candidate_mode:
                report["stats"].update(self._last_selection_stats)
            report["story_mode"] = report["stats"].get(
                "episode_story_mode", report.get("story_mode", "auto")
            )
            report["stats"]["test_mode_enabled"] = test_mode_enabled
            report["stats"]["test_candidate_rank"] = (
                test_candidate_rank if test_mode_enabled else None
            )
            report["stats"]["kept_micro_pauses"] = (
                0
                if direct_candidate_mode
                else sum(
                    1
                    for item in candidates
                    if float(item.get("hook_gap", 0.0))
                    <= float(self.cfg.get("keep_dialogue_gap_seconds", 1.0))
                )
            )
            if not candidates:
                report["status"] = "failed"
                report["stats"]["main_rejection_reason"] = (
                    report["stats"].get("main_rejection_reason")
                    or "no_story_candidates"
                )
                report["warnings"].append("No valid story candidates found")
                _dump_json(os.path.join(out_dir, "episode_report.json"), report)
                return report

            review_fast_mode_enabled = bool(
                self.cfg.get("review_fast_mode_enabled", False)
            )
            needs_review = False
            for index, base_candidate in enumerate(candidates, start=1):
                if stop_check and stop_check():
                    report["status"] = "warning"
                    report["warnings"].append("Stopped by user")
                    break

                candidate = dict(base_candidate)
                candidate_rank = int(base_candidate.get("_rank", index))
                needs_review = False
                acceptance_reason = "strong_publishable"
                if not direct_candidate_mode:
                    breakdown = dict(candidate.get("score_breakdown", {}) or {})
                    cold_open_penalty = float(
                        breakdown.get("cold_open_dead_time_penalty", 0.0) or 0.0
                    )
                    hook_gap = max(
                        0.0,
                        float(
                            candidate.get("hook_gap", breakdown.get("hook_gap", 0.0))
                            or 0.0
                        ),
                    )
                    if cold_open_penalty > 0.0 and hook_gap > float(
                        self.cfg.get("cold_open_dead_time_threshold_seconds", 0.45)
                    ):
                        shift = min(max(0.0, hook_gap - 0.18), 2.75)
                        if shift > 0.05 and float(candidate.get("end", 0.0) or 0.0) - (
                            float(candidate.get("start", 0.0) or 0.0) + shift
                        ) >= float(self.cfg.get("target_story_min_seconds", 20)):
                            candidate["start"] = round(
                                float(candidate.get("start", 0.0) or 0.0) + shift, 3
                            )
                            candidate["duration"] = round(
                                max(
                                    0.0,
                                    float(candidate.get("end", 0.0) or 0.0)
                                    - float(candidate["start"]),
                                ),
                                3,
                            )
                            candidate["hook_gap"] = max(0.0, hook_gap - shift)
                            candidate["cold_open_recut_applied"] = True
                            candidate["cold_open_recut_shift_seconds"] = round(shift, 3)
                            breakdown["cold_open_dead_time_penalty"] = 0.0
                            breakdown["hook_gap"] = round(candidate["hook_gap"], 3)
                            breakdown["duration"] = candidate["duration"]
                            candidate["score_breakdown"] = breakdown
                candidate_watchdog_action = "accept"
                candidate_stage_timeout_seconds = {}
                candidate_stage_deferred = bool(
                    base_candidate.get("_deferred_watchdog", False)
                )
                candidate_stage_hard_timeout_triggered = False
                min_publishable_seconds_floor = max(
                    35.0,
                    float(
                        (candidate.get("duration_policy") or {}).get(
                            "min_publishable_seconds",
                            self.cfg.get("min_publishable_seconds", 35),
                        )
                        or self.cfg.get("min_publishable_seconds", 35)
                    ),
                )
                if not direct_candidate_mode:
                    candidate, expanded, expand_meta = (
                        self._expand_candidate_window_to_min_duration(
                            candidate,
                            min_publishable_seconds_floor,
                        )
                    )
                    if expanded:
                        report["warnings"].append(
                            f"Candidate {index} expanded to minimum duration: +{expand_meta.get('expanded_left_seconds', 0.0):.1f}s left / +{expand_meta.get('expanded_right_seconds', 0.0):.1f}s right"
                        )
                _emit(
                    progress_callback,
                    "warning",
                    f"Candidate {index} expanded to meet minimum duration floor",
                )
                _emit(progress_callback, "trimming", f"Preparing candidate {index}")
                stage_start = _now()
                acceptance_reason = "strong_publishable"
                if direct_candidate_mode:
                    trimmed = video_path
                else:
                    trimmed = self.trim_silence_and_limit(
                        video_path,
                        candidate["start"],
                        candidate["end"],
                        out_dir,
                        index,
                        progress_callback,
                    )
                report["stage_timings"][f"candidate_{index}_trim_seconds"] = round(
                    _now() - stage_start, 3
                )
                if not trimmed or not os.path.exists(trimmed):
                    report["warnings"].append(
                        f"Candidate {index} rejected: trim_failed"
                    )
                    continue

                wav = self._extract_candidate_wav(trimmed, out_dir, index)
                trimmed_speech = speech_density(wav) if wav else 0.0
                if trimmed_speech < 0.16:
                    report["warnings"].append(
                        f"Candidate {index} rejected: low speech density after trim"
                    )
                    _emit(
                        progress_callback,
                        "warning",
                        f"Candidate {index} rejected: low speech density",
                    )
                    continue

                _, trimmed_duration = probe_video(trimmed)
                _emit(
                    progress_callback, "subtitling", f"Transcribing candidate {index}"
                )
                stage_start = _now()
                subtitle_timed = _run_in_subprocess_with_timeout(
                    "transcribe_auto_quality",
                    {
                        "cfg": self.cfg,
                        "wav_path": wav,
                        "out_dir": out_dir,
                        "idx": index,
                        "candidate": candidate,
                    },
                    soft_timeout_seconds=float(
                        self.cfg.get("subtitle_soft_timeout_seconds", 90)
                    ),
                    hard_timeout_seconds=float(
                        self.cfg.get("subtitle_hard_timeout_seconds", 180)
                    ),
                    default=None,
                    heartbeat_seconds=float(
                        self.cfg.get("heartbeat_interval_seconds", 30)
                    ),
                    on_heartbeat=self._heartbeat_callback(
                        progress_callback,
                        "subtitling",
                        f"Still transcribing candidate {index}",
                    ),
                    on_soft_timeout=lambda _elapsed: None,
                    on_hard_timeout=lambda _elapsed: self._watchdog_stats.__setitem__(
                        "hard_timeouts",
                        self._watchdog_stats.get("hard_timeouts", 0) + 1,
                    ),
                )
                subtitle_info = (
                    subtitle_timed["result"]
                    if isinstance(subtitle_timed, dict)
                    else None
                )
                if bool((subtitle_timed or {}).get("soft_timeout")):
                    candidate_watchdog_action = "slow_stage"
                    candidate_stage_timeout_seconds["subtitle_soft"] = float(
                        self.cfg.get("subtitle_soft_timeout_seconds", 90)
                    )
                report["stage_timings"][f"candidate_{index}_subtitle_seconds"] = round(
                    _now() - stage_start, 3
                )
                if bool((subtitle_timed or {}).get("hard_timeout")):
                    candidate_watchdog_action = "skip_timeout"
                    candidate_stage_hard_timeout_triggered = True
                    candidate_stage_timeout_seconds["subtitle_hard"] = float(
                        self.cfg.get("subtitle_hard_timeout_seconds", 180)
                    )
                    report["warnings"].append(
                        f"Candidate {index} rejected: subtitle_timeout"
                    )
                    self._watchdog_stats["skipped_due_to_timeout"] = (
                        self._watchdog_stats.get("skipped_due_to_timeout", 0) + 1
                    )
                    _emit(
                        progress_callback,
                        "warning",
                        f"Candidate {index} rejected: subtitle_timeout",
                    )
                    continue
                if not subtitle_info or not subtitle_info.get("srt_path"):
                    report["warnings"].append(
                        f"Candidate {index} rejected: no subtitles"
                    )
                    _emit(
                        progress_callback,
                        "warning",
                        f"Candidate {index} rejected: no subtitles",
                    )
                    continue

                original_trimmed = trimmed
                original_subtitle_info = subtitle_info
                compacted_trimmed, compacted_changed = (
                    self._maybe_compact_dialogue_after_subtitles(
                        trimmed,
                        subtitle_info,
                        out_dir,
                        index,
                        candidate=candidate,
                        progress_callback=progress_callback,
                    )
                )
                if compacted_changed:
                    trimmed = compacted_trimmed
                    _, trimmed_duration = probe_video(trimmed)
                    stage_start = _now()
                    try:
                        if bool(self.cfg.get("subtitle_remap_after_silence_cut", True)):
                            remap_cfg = self._candidate_cfg(candidate, stage="default")
                            subtitle_info = remap_subtitle_info_after_cuts(
                                subtitle_info,
                                list(
                                    (trim_silence_in_candidate_ms.last_stats or {}).get(
                                        "pause_removed_segments", []
                                    )
                                    or []
                                ),
                                out_dir,
                                index,
                                cfg=remap_cfg,
                            )
                        else:
                            subtitle_info = original_subtitle_info
                    except Exception:
                        subtitle_info = original_subtitle_info
                    report["stage_timings"][
                        f"candidate_{index}_subtitle_compact_seconds"
                    ] = round(_now() - stage_start, 3)
                    if not subtitle_info or not subtitle_info.get("srt_path"):
                        report["warnings"].append(
                            f"Candidate {index} subtitle compaction fallback"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} subtitle compaction remap failed; keeping original subtitles",
                        )
                        subtitle_info = original_subtitle_info
                    else:
                        subtitle_info["subtitle_remap_used"] = True

                subtitle_turns = int(subtitle_info.get("line_count", 0))
                subtitle_signals = dict(subtitle_info.get("signals", {}) or {})
                transcript_chars = len(
                    " ".join(
                        item.get("text", "")
                        for item in subtitle_info.get("segments", [])
                    )
                )
                if (
                    subtitle_turns < int(self.cfg.get("min_subtitle_turns", 3))
                    and transcript_chars < 46
                ):
                    report["warnings"].append(
                        f"Candidate {index} rejected: low subtitle turns"
                    )
                    _emit(
                        progress_callback,
                        "warning",
                        f"Candidate {index} rejected: low subtitle turns",
                    )
                    continue

                if direct_candidate_mode:
                    observed_boundary_conf = float(
                        subtitle_signals.get(
                            "story_boundary_confidence",
                            subtitle_signals.get("dialogue_flow_score", 0.72),
                        )
                        or 0.72
                    )
                    line_completion_passed = True
                    line_reason = "test_mode"
                    boundary_meta = {
                        "start_boundary_reason": "test_mode",
                        "end_boundary_reason": "test_mode",
                        "hook_ok": True,
                        "story_has_payoff": True,
                        "sentence_start_safe": True,
                        "sentence_end_safe": True,
                        "story_continuation_used": False,
                        "story_boundary_confidence": round(
                            max(0.35, min(0.95, observed_boundary_conf)), 4
                        ),
                        "boundary_expand_attempted": False,
                        "boundary_expand_seconds_left": 0.0,
                        "boundary_expand_seconds_right": 0.0,
                        "rejected_after_boundary_expansion": False,
                        "test_mode_boundary_override": True,
                    }
                    boundary_refined = False
                    needs_review = False
                    acceptance_reason = "test_mode_visual_only"
                    interestingness_score = float(
                        subtitle_signals.get("interestingness_score", 1.0)
                    )
                    dialogue_exchange_score = float(
                        subtitle_signals.get("dialogue_exchange_score", 1.0)
                    )
                    duration_policy = self._resolve_candidate_duration_policy(
                        candidate, subtitle_info
                    )
                    min_publishable_seconds = float(
                        duration_policy.get(
                            "min_publishable_seconds",
                            self.cfg.get("min_publishable_seconds", 35),
                        )
                        or self.cfg.get("min_publishable_seconds", 35)
                    )
                    exceptional_min = float(
                        duration_policy.get(
                            "min_exceptional_publishable_seconds",
                            self.cfg.get("min_exceptional_publishable_seconds", 20),
                        )
                        or self.cfg.get("min_exceptional_publishable_seconds", 20)
                    )
                    salvage_short_story = False
                else:
                    line_completion_passed, line_reason, boundary_meta = (
                        self._line_completion_info(subtitle_info, trimmed_duration)
                    )
                    boundary_refined = False
                    boundary_retry_limit = max(
                        1, int(self.cfg.get("boundary_retry_limit", 3) or 3)
                    )
                    boundary_attempts = 0
                    boundary_retry_failed = False
                    while (
                        not line_completion_passed
                        and boundary_attempts < boundary_retry_limit
                    ):
                        _emit(
                            progress_callback,
                            "refining_boundaries",
                            f"Refining dialogue boundaries for candidate {index}",
                        )
                        refined_candidate, changed, boundary_meta = (
                            self._maybe_refine_bounds(
                                candidate, subtitle_info, trimmed_duration
                            )
                        )
                        if not changed:
                            break
                        boundary_attempts += 1
                        candidate = refined_candidate
                        boundary_refined = True
                        if direct_candidate_mode:
                            break
                        trimmed = self.trim_silence_and_limit(
                            video_path,
                            candidate["start"],
                            candidate["end"],
                            out_dir,
                            index,
                            progress_callback,
                        )
                        wav = self._extract_candidate_wav(trimmed, out_dir, index)
                        _, trimmed_duration = probe_video(trimmed)
                        retry_subtitle_timed = _run_in_subprocess_with_timeout(
                            "transcribe_auto_quality",
                            {
                                "cfg": self.cfg,
                                "wav_path": wav,
                                "out_dir": out_dir,
                                "idx": index,
                                "candidate": candidate,
                            },
                            soft_timeout_seconds=float(
                                self.cfg.get("subtitle_soft_timeout_seconds", 90)
                            ),
                            hard_timeout_seconds=float(
                                self.cfg.get("subtitle_hard_timeout_seconds", 180)
                            ),
                            default=None,
                            heartbeat_seconds=float(
                                self.cfg.get("heartbeat_interval_seconds", 30)
                            ),
                            on_heartbeat=self._heartbeat_callback(
                                progress_callback,
                                "subtitling",
                                f"Still transcribing candidate {index}",
                            ),
                            on_soft_timeout=lambda _elapsed: None,
                            on_hard_timeout=lambda _elapsed: (
                                self._watchdog_stats.__setitem__(
                                    "hard_timeouts",
                                    self._watchdog_stats.get("hard_timeouts", 0) + 1,
                                )
                            ),
                        )
                        subtitle_info = (
                            retry_subtitle_timed["result"]
                            if isinstance(retry_subtitle_timed, dict)
                            else None
                        )
                        if bool((retry_subtitle_timed or {}).get("soft_timeout")):
                            candidate_watchdog_action = "watchdog_fallback"
                            candidate_stage_timeout_seconds["subtitle_retry_soft"] = (
                                float(self.cfg.get("subtitle_soft_timeout_seconds", 90))
                            )
                        if not subtitle_info:
                            candidate_watchdog_action = "skip_timeout"
                            candidate_stage_hard_timeout_triggered = bool(
                                (retry_subtitle_timed or {}).get("hard_timeout")
                            )
                            if candidate_stage_hard_timeout_triggered:
                                candidate_stage_timeout_seconds[
                                    "subtitle_retry_hard"
                                ] = float(
                                    self.cfg.get("subtitle_hard_timeout_seconds", 180)
                                )
                            report["warnings"].append(
                                f"Candidate {index} rejected: subtitle_timeout"
                            )
                            self._watchdog_stats["skipped_due_to_timeout"] = (
                                self._watchdog_stats.get("skipped_due_to_timeout", 0)
                                + 1
                            )
                            _emit(
                                progress_callback,
                                "warning",
                                f"Candidate {index} rejected: subtitle_timeout",
                            )
                            boundary_retry_failed = True
                            break
                        subtitle_signals = dict(subtitle_info.get("signals", {}) or {})
                        compacted_trimmed, compacted_changed = (
                            self._maybe_compact_dialogue_after_subtitles(
                                trimmed,
                                subtitle_info,
                                out_dir,
                                index,
                                candidate=candidate,
                                progress_callback=progress_callback,
                            )
                        )
                        if compacted_changed:
                            trimmed = compacted_trimmed
                            wav = self._extract_candidate_wav(trimmed, out_dir, index)
                            _, trimmed_duration = probe_video(trimmed)
                            compact_retry_timed = _run_in_subprocess_with_timeout(
                                "transcribe_auto_quality",
                                {
                                    "cfg": self.cfg,
                                    "wav_path": wav,
                                    "out_dir": out_dir,
                                    "idx": index,
                                    "candidate": candidate,
                                },
                                soft_timeout_seconds=float(
                                    self.cfg.get("subtitle_soft_timeout_seconds", 90)
                                ),
                                hard_timeout_seconds=float(
                                    self.cfg.get("subtitle_hard_timeout_seconds", 180)
                                ),
                                default=None,
                                heartbeat_seconds=float(
                                    self.cfg.get("heartbeat_interval_seconds", 30)
                                ),
                                on_heartbeat=self._heartbeat_callback(
                                    progress_callback,
                                    "subtitling",
                                    f"Still transcribing candidate {index} after boundary compaction",
                                ),
                                on_soft_timeout=lambda _elapsed: None,
                                on_hard_timeout=lambda _elapsed: (
                                    self._watchdog_stats.__setitem__(
                                        "hard_timeouts",
                                        self._watchdog_stats.get("hard_timeouts", 0)
                                        + 1,
                                    )
                                ),
                            )
                            subtitle_info = (
                                compact_retry_timed["result"]
                                if isinstance(compact_retry_timed, dict)
                                else None
                            )
                            if not subtitle_info:
                                boundary_retry_failed = True
                                report["warnings"].append(
                                    f"Candidate {index} rejected: subtitle_timeout"
                                )
                                _emit(
                                    progress_callback,
                                    "warning",
                                    f"Candidate {index} rejected: subtitle_timeout",
                                )
                                break
                            subtitle_signals = dict(
                                subtitle_info.get("signals", {}) or {}
                            )
                        line_completion_passed, line_reason, boundary_meta = (
                            self._line_completion_info(subtitle_info, trimmed_duration)
                        )
                    if boundary_retry_failed:
                        report["warnings"].append(
                            f"Candidate {index} downgraded: subtitle_timeout"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} downgraded: subtitle_timeout",
                        )
                        needs_review = True
                    subtitle_info = subtitle_info or {"signals": {}}
                    subtitle_signals = dict(subtitle_info.get("signals", {}) or {})
                    if not line_completion_passed:
                        report["warnings"].append(
                            f"Candidate {index} downgraded: {line_reason}"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} downgraded: {line_reason}",
                        )
                        needs_review = True

                    # Recompute the policy now that subtitles are available.
                    # The pre-ranking pass stores a coarse policy without subtitle evidence,
                    # but final publishability must use the subtitle-aware policy so that
                    # tension episodes do not get evaluated with standard 35s thresholds.
                    duration_policy = self._resolve_candidate_duration_policy(
                        candidate, subtitle_info
                    )
                    min_publishable_seconds = float(
                        duration_policy.get(
                            "min_publishable_seconds",
                            self.cfg.get("min_publishable_seconds", 35),
                        )
                        or self.cfg.get("min_publishable_seconds", 35)
                    )
                    exceptional_min = float(
                        duration_policy.get(
                            "min_exceptional_publishable_seconds",
                            self.cfg.get("min_exceptional_publishable_seconds", 20),
                        )
                        or self.cfg.get("min_exceptional_publishable_seconds", 20)
                    )
                    clarity = float(
                        candidate["score_breakdown"].get("story_clarity_score", 0.0)
                    )
                    strong_hook_score = max(
                        float(
                            candidate["score_breakdown"].get(
                                "visual_premise_strength", 0.0
                            )
                            or 0.0
                        ),
                        float(
                            candidate["score_breakdown"].get(
                                "sound_off_hook_score", 0.0
                            )
                            or 0.0
                        ),
                        float(
                            candidate["score_breakdown"].get(
                                "first_second_hook_score", 0.0
                            )
                            or 0.0
                        ),
                        float(
                            candidate["score_breakdown"].get(
                                "premise_signal_score", 0.0
                            )
                            or 0.0
                        ),
                    )
                    story_interest_pre = max(
                        float(
                            candidate["score_breakdown"].get(
                                "story_interest_score", 0.0
                            )
                            or 0.0
                        ),
                        float(
                            subtitle_signals.get("interestingness_score", 0.0) or 0.0
                        ),
                    )
                    story_completeness_pre = max(
                        float(
                            candidate["score_breakdown"].get(
                                "story_completeness_score", 0.0
                            )
                            or 0.0
                        ),
                        float(subtitle_signals.get("closure_score", 0.0) or 0.0),
                        float(
                            boundary_meta.get("story_boundary_confidence", 0.0) or 0.0
                        ),
                    )
                    salvage_short_story = bool(
                        trimmed_duration >= min_publishable_seconds
                        and (
                            duration_policy.get("band")
                            in {
                                "strong_story",
                                "exceptional_high_interest",
                                "tension_strong",
                                "tension_exceptional",
                            }
                            or (
                                strong_hook_score >= 0.66
                                and clarity >= 0.62
                                and (
                                    story_interest_pre >= 0.50
                                    or story_completeness_pre >= 0.50
                                )
                            )
                            or (
                                strong_hook_score >= 0.72
                                and story_interest_pre >= 0.46
                                and story_completeness_pre >= 0.46
                                and clarity >= 0.60
                            )
                        )
                    )
                    if trimmed_duration < min_publishable_seconds:
                        report["warnings"].append(
                            f"Candidate {index} rejected: insufficient_duration"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} rejected: insufficient_duration",
                        )
                        continue

                    needs_review = bool(needs_review or salvage_short_story)
                    acceptance_reason = (
                        "salvaged_story_window"
                        if needs_review
                        else "strong_publishable"
                    )
                    interestingness_score = float(
                        subtitle_signals.get("interestingness_score", 0.0)
                    )
                    dialogue_exchange_score = float(
                        subtitle_signals.get("dialogue_exchange_score", 0.0)
                    )
                subtitle_info = subtitle_info or {"signals": {}}
                subtitle_signals = dict(subtitle_info.get("signals", {}) or {})
                subtitle_confidence = float(subtitle_info.get("confidence", 0.0))
                if subtitle_confidence < 0.33:
                    needs_review = True
                    report["warnings"].append(
                        f"Candidate {index} marked review_required: subtitle_confidence_low"
                    )
                if (
                    not direct_candidate_mode
                    and candidate["score_breakdown"].get("story_clarity_score", 0.0)
                    < float(self.cfg.get("story_clarity_threshold", 0.56)) + 0.08
                ):
                    needs_review = True
                if not direct_candidate_mode and interestingness_score < float(
                    self.cfg.get("interestingness_threshold", 0.52)
                ):
                    if (
                        float(
                            candidate["score_breakdown"].get("story_clarity_score", 0.0)
                        )
                        < float(self.cfg.get("story_clarity_threshold", 0.56)) + 0.12
                    ):
                        report["warnings"].append(
                            f"Candidate {index} downgraded: low_story_interest"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} downgraded: low_story_interest",
                        )
                        needs_review = True
                    needs_review = True
                story_interest_score = max(
                    float(
                        candidate["score_breakdown"].get("story_interest_score", 0.0)
                        or 0.0
                    ),
                    float(subtitle_signals.get("interestingness_score", 0.0) or 0.0),
                )
                story_completeness_score = max(
                    float(
                        candidate["score_breakdown"].get(
                            "story_completeness_score", 0.0
                        )
                        or 0.0
                    ),
                    float(subtitle_signals.get("closure_score", 0.0) or 0.0),
                    float(boundary_meta.get("story_boundary_confidence", 0.0) or 0.0),
                )
                story_context_score = max(
                    float(
                        candidate["score_breakdown"].get("story_context_score", 0.0)
                        or 0.0
                    ),
                    float(subtitle_signals.get("dialogue_exchange_score", 0.0) or 0.0)
                    * 0.42,
                )
                quality_backfill_keys = (
                    "watchability_score",
                    "recommendation_readiness_score",
                    "packaging_quality_score",
                )
                for key in quality_backfill_keys:
                    policy_value = float(duration_policy.get(key, 0.0) or 0.0)
                    if policy_value > float(
                        candidate["score_breakdown"].get(key, 0.0) or 0.0
                    ):
                        candidate["score_breakdown"][key] = round(policy_value, 4)
                candidate["watchability_score"] = float(
                    candidate["score_breakdown"].get("watchability_score", 0.0) or 0.0
                )
                candidate["recommendation_readiness_score"] = float(
                    candidate["score_breakdown"].get(
                        "recommendation_readiness_score", 0.0
                    )
                    or 0.0
                )
                candidate["packaging_quality_score"] = float(
                    candidate["score_breakdown"].get("packaging_quality_score", 0.0)
                    or 0.0
                )
                candidate["pause_policy_failed"] = bool(
                    (trim_silence_in_candidate_ms.last_stats or {}).get(
                        "pause_policy_failed", False
                    )
                )
                if (
                    not direct_candidate_mode
                    and story_interest_score
                    < float(self.cfg.get("interestingness_threshold", 0.52)) * 0.88
                ):
                    if not salvage_short_story:
                        report["warnings"].append(
                            f"Candidate {index} downgraded: low_story_interest"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} downgraded: low_story_interest",
                        )
                    needs_review = True
                if (
                    not direct_candidate_mode
                    and story_completeness_score
                    < float(self.cfg.get("min_story_payoff_score", 0.40))
                    and float(
                        boundary_meta.get("story_boundary_confidence", 0.0) or 0.0
                    )
                    < float(self.cfg.get("story_boundary_confidence_threshold", 0.58))
                ):
                    if not salvage_short_story:
                        report["warnings"].append(
                            f"Candidate {index} downgraded: low_story_completeness"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} downgraded: low_story_completeness",
                        )
                    needs_review = True
                if (
                    not direct_candidate_mode
                    and trimmed_duration
                    > float(duration_policy.get("hard_max_seconds", 60.0)) + 0.6
                ):
                    if not bool(
                        duration_policy.get("exceptional_duration_used", False)
                    ):
                        report["warnings"].append(
                            f"Candidate {index} downgraded: duration_too_long"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} downgraded: duration_too_long",
                        )
                        needs_review = True
                if not direct_candidate_mode and subtitle_signals.get(
                    "starts_mid_phrase"
                ):
                    needs_review = True
                if needs_review:
                    if bool(
                        boundary_meta.get("story_has_payoff")
                    ) and interestingness_score >= float(
                        self.cfg.get("interestingness_threshold", 0.52)
                    ):
                        acceptance_reason = "complete_but_weak"
                    else:
                        acceptance_reason = "interesting_but_incomplete"

                crop = os.path.join(out_dir, f"cand_{index}_crop.mp4")
                _emit(progress_callback, "reframing", f"Reframing candidate {index}")
                stage_start = _now()
                reframe_debug = {}
                source_face_presence = float(
                    candidate.get("score_breakdown", {}).get("face_presence", 0.0)
                    or 0.0
                )
                source_person_presence = float(
                    candidate.get("score_breakdown", {}).get("person_presence", 0.0)
                    or 0.0
                )
                source_subject_presence = float(
                    candidate.get("score_breakdown", {}).get("subject_presence", 0.0)
                    or 0.0
                )
                source_avg_face_size = float(
                    candidate.get("score_breakdown", {}).get("avg_face_size", 0.0)
                    or 0.0
                )
                source_avg_center_x = float(
                    candidate.get("score_breakdown", {}).get("avg_center_x", 0.5) or 0.5
                )
                source_avg_center_y = float(
                    candidate.get("score_breakdown", {}).get("avg_center_y", 0.5) or 0.5
                )
                source_face_rich = bool(
                    source_face_presence >= 0.18
                    or source_subject_presence >= 0.30
                    or source_person_presence >= 0.18
                )
                reframe_cfg = self._candidate_cfg(candidate, stage="reframe")
                framing_mode, anchor_mode = self._select_framing_plan(
                    candidate,
                    subtitle_turns,
                    subtitle_signals,
                    reframe_cfg,
                    direct_candidate_mode=direct_candidate_mode,
                )
                strict_speaker_only = bool(
                    reframe_cfg.get(
                        "speaker_center_strict_mode",
                        self.cfg.get("speaker_center_strict_mode", True),
                    )
                )
                reframe_soft_timeout_seconds = float(
                    self.cfg.get("reframe_soft_timeout_seconds", 150)
                )
                reframe_hard_timeout_seconds = float(
                    self.cfg.get("reframe_hard_timeout_seconds", 240)
                )
                if review_fast_mode_enabled:
                    reframe_soft_timeout_seconds = min(
                        reframe_soft_timeout_seconds,
                        float(
                            self.cfg.get("review_fast_reframe_soft_timeout_seconds", 24)
                            or 24
                        ),
                    )
                    reframe_hard_timeout_seconds = min(
                        reframe_hard_timeout_seconds,
                        float(
                            self.cfg.get("review_fast_reframe_hard_timeout_seconds", 40)
                            or 40
                        ),
                    )
                if strict_speaker_only:
                    reframe_soft_timeout_seconds = min(
                        reframe_soft_timeout_seconds, 60.0
                    )
                    reframe_hard_timeout_seconds = min(
                        reframe_hard_timeout_seconds, 90.0
                    )
                story_interest_for_hold = max(
                    float(candidate.get("story_interest_score", 0.0) or 0.0),
                    float(
                        candidate.get("score_breakdown", {}).get(
                            "story_interest_score", 0.0
                        )
                        or 0.0
                    ),
                    float(candidate.get("watchability_score", 0.0) or 0.0),
                )
                story_payoff_for_hold = max(
                    float(candidate.get("payoff_strength", 0.0) or 0.0),
                    float(
                        candidate.get("score_breakdown", {}).get("payoff_strength", 0.0)
                        or 0.0
                    ),
                    float(candidate.get("story_completeness_score", 0.0) or 0.0),
                )
                accent_frame_hold_windows = 0
                if not direct_candidate_mode and not bool(
                    candidate.get("selection_visual_soft_gate", True)
                ):
                    accent_frame_hold_windows = 2
                elif story_interest_for_hold >= float(
                    self.cfg.get("accent_frame_hold_story_interest_threshold", 0.74)
                ) and story_payoff_for_hold >= float(
                    self.cfg.get("accent_frame_hold_payoff_threshold", 0.50)
                ):
                    accent_frame_hold_windows = 2
                elif story_interest_for_hold >= max(
                    0.62,
                    float(
                        self.cfg.get("accent_frame_hold_story_interest_threshold", 0.74)
                    )
                    * 0.88,
                ):
                    accent_frame_hold_windows = 1
                accent_frame_hold_windows = min(3, int(accent_frame_hold_windows))
                selected_framing_mode = str(framing_mode)
                if strict_speaker_only and selected_framing_mode != "square_canvas":
                    selected_framing_mode = "face_locked"
                reframe_kwargs = {
                    "video_path": trimmed,
                    "start": None,
                    "end": None,
                    "out_path": crop,
                    "target_w": int(reframe_cfg.get("vertical_w", 720)),
                    "target_h": int(reframe_cfg.get("vertical_h", 1280)),
                    "use_active_speaker": bool(reframe_cfg.get("use_visual_asd", True)),
                    "reframe_mode": str(reframe_cfg.get("reframe_mode", "balanced")),
                    "reframe_transition_mode": "hard_switch"
                    if strict_speaker_only
                    else str(
                        reframe_cfg.get(
                            "reframe_transition_mode",
                            self.cfg.get("strict_reframe_transition_mode", "smooth"),
                        )
                    ),
                    "reframe_anchor_mode": str(anchor_mode),
                    "reframe_subject_mode": str(
                        reframe_cfg.get("reframe_subject_mode", "subject_first")
                    ),
                    "window_sec": float(reframe_cfg.get("crop_window_sec", 2.8)),
                    "sample_fps": int(reframe_cfg.get("face_detection_fps", 2)),
                    "speaker_switch_hold_windows": 0
                    if strict_speaker_only
                    else int(reframe_cfg.get("speaker_switch_hold_windows", 2)),
                    "accent_frame_hold_windows": int(accent_frame_hold_windows),
                    "reframe_switch_min_visibility": float(
                        reframe_cfg.get("reframe_switch_min_visibility", 0.38)
                    ),
                    "reframe_allow_wide_dialogue_center": bool(
                        reframe_cfg.get("reframe_allow_wide_dialogue_center", True)
                    ),
                    "reframe_track_count_limit": int(
                        reframe_cfg.get("reframe_track_count_limit", 3)
                    ),
                    "reframe_dual_face_margin": float(
                        reframe_cfg.get("reframe_dual_face_margin", 0.14)
                    ),
                    "reframe_lost_face_hold_seconds": float(
                        reframe_cfg.get(
                            "reframe_lost_face_hold_seconds",
                            reframe_cfg.get("empty_face_hold_seconds", 1.5),
                        )
                    ),
                    "reframe_scene_interest_fallback": bool(
                        reframe_cfg.get("reframe_scene_interest_fallback", False)
                    ),
                    "scene_interest_fallback_mode": str(
                        reframe_cfg.get(
                            "scene_interest_fallback_mode", "emergency_only"
                        )
                    ),
                    "reframe_listener_face_fallback": bool(
                        reframe_cfg.get("reframe_listener_face_fallback", True)
                    ),
                    "dialogue_two_shot_preferred": bool(
                        reframe_cfg.get("dialogue_two_shot_preferred", True)
                    ),
                    "reframe_priority": str(
                        reframe_cfg.get("reframe_priority", "stability_first")
                    ),
                    "speaker_lock_mode": str(
                        reframe_cfg.get("speaker_lock_mode", "state_machine")
                    ),
                    "speaker_min_hold_seconds": float(
                        reframe_cfg.get("speaker_min_hold_seconds", 1.2)
                    ),
                    "listener_hold_seconds": float(
                        reframe_cfg.get("listener_hold_seconds", 1.0)
                    ),
                    "speaker_center_strict_mode": bool(
                        reframe_cfg.get(
                            "speaker_center_strict_mode",
                            self.cfg.get("speaker_center_strict_mode", True),
                        )
                    ),
                    "speaker_center_max_offset": float(
                        reframe_cfg.get(
                            "speaker_center_max_offset",
                            self.cfg.get("speaker_center_max_offset", 0.18),
                        )
                    ),
                    "speaker_face_lock_min_margin": float(
                        reframe_cfg.get(
                            "speaker_face_lock_min_margin",
                            self.cfg.get("speaker_face_lock_min_margin", 0.10),
                        )
                    ),
                    "dialogue_center_use_threshold": float(
                        reframe_cfg.get(
                            "dialogue_center_use_threshold",
                            self.cfg.get("dialogue_center_use_threshold", 0.70),
                        )
                    ),
                    "listener_fallback_speech_hold_max_seconds": float(
                        reframe_cfg.get(
                            "listener_fallback_speech_hold_max_seconds",
                            self.cfg.get(
                                "listener_fallback_speech_hold_max_seconds", 0.45
                            ),
                        )
                    ),
                    "dialogue_center_min_likelihood": float(
                        reframe_cfg.get("dialogue_center_min_likelihood", 0.48)
                    ),
                    "empty_frame_guard_enabled": bool(
                        reframe_cfg.get("empty_frame_guard_enabled", True)
                    ),
                    "max_crop_delta_per_window": float(
                        reframe_cfg.get("max_crop_delta_per_window", 0.05)
                    ),
                    "motion_blend_normal": float(
                        reframe_cfg.get("motion_blend_normal", 0.2)
                    ),
                    "motion_blend_switch": float(
                        reframe_cfg.get("motion_blend_switch", 0.32)
                    ),
                    "subject_visibility_threshold": float(
                        reframe_cfg.get("subject_visibility_threshold", 0.46)
                    ),
                    "lock_confidence_threshold": float(
                        reframe_cfg.get("lock_confidence_threshold", 0.72)
                    ),
                    "speaker_confidence_threshold": float(
                        reframe_cfg.get("speaker_confidence_threshold", 0.62)
                    ),
                    "handoff_min_hold_windows": int(
                        reframe_cfg.get("handoff_min_hold_windows", 2)
                    ),
                    "confident_lock_min_hold_windows": int(
                        reframe_cfg.get("confident_lock_min_hold_windows", 4)
                    ),
                    "target_deadband_handoff": float(
                        reframe_cfg.get("target_deadband_handoff", 0.028)
                    ),
                    "target_deadband_lock": float(
                        reframe_cfg.get("target_deadband_lock", 0.018)
                    ),
                    "max_delta_handoff": float(
                        reframe_cfg.get("max_delta_handoff", 0.028)
                    ),
                    "max_delta_lock": float(reframe_cfg.get("max_delta_lock", 0.020)),
                    "motion_blend_switch_handoff": float(
                        reframe_cfg.get("motion_blend_switch_handoff", 0.22)
                    ),
                    "motion_blend_normal_handoff": float(
                        reframe_cfg.get("motion_blend_normal_handoff", 0.14)
                    ),
                    "subject_detector_pass": str(
                        reframe_cfg.get("subject_detector_pass", "light")
                    ),
                    "shot_reacquire_boost_windows": int(
                        reframe_cfg.get("shot_reacquire_boost_windows", 2)
                    ),
                    "new_face_fast_acquire_threshold": float(
                        reframe_cfg.get("new_face_fast_acquire_threshold", 0.78)
                    ),
                    "framing_mode": selected_framing_mode,
                    "progress_callback": None,
                    "subtitle_segments": candidate.get("subtitle_segments"),
                }
                reframe_timed = _run_in_subprocess_with_timeout(
                    "create_vertical_crop",
                    {"kwargs": reframe_kwargs},
                    soft_timeout_seconds=reframe_soft_timeout_seconds,
                    hard_timeout_seconds=reframe_hard_timeout_seconds,
                    default={"ok": False, "debug_info": {}},
                    heartbeat_seconds=float(
                        self.cfg.get("heartbeat_interval_seconds", 30)
                    ),
                    on_heartbeat=self._heartbeat_callback(
                        progress_callback,
                        "reframing",
                        f"Still reframing candidate {index}",
                    ),
                    on_soft_timeout=lambda _elapsed: None,
                    on_hard_timeout=lambda _elapsed: self._watchdog_stats.__setitem__(
                        "hard_timeouts",
                        self._watchdog_stats.get("hard_timeouts", 0) + 1,
                    ),
                )
                reframe_result = (
                    reframe_timed["result"]
                    if isinstance(reframe_timed, dict)
                    else {"ok": False, "debug_info": {}}
                )
                reframed = bool((reframe_result or {}).get("ok"))
                reframe_debug = dict((reframe_result or {}).get("debug_info", {}) or {})
                if bool((reframe_timed or {}).get("soft_timeout")):
                    if candidate_watchdog_action == "accept":
                        candidate_watchdog_action = "slow_stage"
                    candidate_stage_timeout_seconds["reframe_soft"] = float(
                        self.cfg.get("reframe_soft_timeout_seconds", 150)
                    )
                hard_timeout_reframe = bool((reframe_timed or {}).get("hard_timeout"))
                if hard_timeout_reframe:
                    candidate_watchdog_action = "watchdog_fallback"
                    candidate_stage_hard_timeout_triggered = True
                    candidate_stage_timeout_seconds["reframe_hard"] = float(
                        self.cfg.get("reframe_hard_timeout_seconds", 240)
                    )
                    reframe_debug["hard_timeout_triggered"] = True
                    reframe_debug["auto_reframe_retry_used"] = False
                    _emit(
                        progress_callback,
                        "warning",
                        f"Candidate {index} reframe_timeout; trying strict fallback",
                    )
                if not hard_timeout_reframe and self._should_retry_reframe(
                    reframe_debug, reframed
                ):
                    retry_debug = {}
                    retry_cfg = self._retry_reframe_cfg()
                    retry_crop = os.path.join(out_dir, f"cand_{index}_crop_retry.mp4")
                    retry_framing_mode, retry_anchor_mode = self._select_framing_plan(
                        candidate,
                        subtitle_turns,
                        subtitle_signals,
                        retry_cfg,
                        direct_candidate_mode=direct_candidate_mode,
                    )
                    retry_kwargs = {
                        "video_path": trimmed,
                        "start": None,
                        "end": None,
                        "out_path": retry_crop,
                        "target_w": int(retry_cfg.get("vertical_w", 720)),
                        "target_h": int(retry_cfg.get("vertical_h", 1280)),
                        "use_active_speaker": bool(
                            retry_cfg.get("use_visual_asd", True)
                        ),
                        "reframe_mode": str(retry_cfg.get("reframe_mode", "balanced")),
                        "reframe_transition_mode": "hard_switch"
                        if bool(
                            retry_cfg.get(
                                "speaker_center_strict_mode",
                                self.cfg.get("speaker_center_strict_mode", True),
                            )
                        )
                        else str(
                            retry_cfg.get(
                                "reframe_transition_mode",
                                self.cfg.get(
                                    "strict_reframe_transition_mode", "smooth"
                                ),
                            )
                        ),
                        "reframe_anchor_mode": str(retry_anchor_mode),
                        "reframe_subject_mode": str(
                            retry_cfg.get("reframe_subject_mode", "subject_first")
                        ),
                        "window_sec": float(retry_cfg.get("crop_window_sec", 0.9)),
                        "sample_fps": int(retry_cfg.get("face_detection_fps", 3)),
                        "speaker_switch_hold_windows": int(
                            retry_cfg.get("speaker_switch_hold_windows", 4)
                        ),
                        "accent_frame_hold_windows": int(accent_frame_hold_windows),
                        "reframe_switch_min_visibility": float(
                            retry_cfg.get("reframe_switch_min_visibility", 0.38)
                        ),
                        "reframe_allow_wide_dialogue_center": bool(
                            retry_cfg.get("reframe_allow_wide_dialogue_center", True)
                        ),
                        "reframe_track_count_limit": int(
                            retry_cfg.get("reframe_track_count_limit", 3)
                        ),
                        "reframe_dual_face_margin": float(
                            retry_cfg.get("reframe_dual_face_margin", 0.14)
                        ),
                        "reframe_lost_face_hold_seconds": float(
                            retry_cfg.get("reframe_lost_face_hold_seconds", 2.6)
                        ),
                        "reframe_scene_interest_fallback": bool(
                            retry_cfg.get("reframe_scene_interest_fallback", False)
                        ),
                        "scene_interest_fallback_mode": str(
                            retry_cfg.get(
                                "scene_interest_fallback_mode", "emergency_only"
                            )
                        ),
                        "reframe_listener_face_fallback": bool(
                            retry_cfg.get("reframe_listener_face_fallback", True)
                        ),
                        "dialogue_two_shot_preferred": bool(
                            retry_cfg.get("dialogue_two_shot_preferred", True)
                        ),
                        "reframe_priority": str(
                            retry_cfg.get("reframe_priority", "stability_first")
                        ),
                        "speaker_lock_mode": str(
                            retry_cfg.get("speaker_lock_mode", "state_machine")
                        ),
                        "speaker_min_hold_seconds": float(
                            retry_cfg.get("speaker_min_hold_seconds", 1.2)
                        ),
                        "listener_hold_seconds": float(
                            retry_cfg.get("listener_hold_seconds", 1.0)
                        ),
                        "dialogue_center_min_likelihood": float(
                            retry_cfg.get("dialogue_center_min_likelihood", 0.48)
                        ),
                        "empty_frame_guard_enabled": bool(
                            retry_cfg.get("empty_frame_guard_enabled", True)
                        ),
                        "max_crop_delta_per_window": float(
                            retry_cfg.get("max_crop_delta_per_window", 0.05)
                        ),
                        "motion_blend_normal": float(
                            retry_cfg.get("motion_blend_normal", 0.2)
                        ),
                        "motion_blend_switch": float(
                            retry_cfg.get("motion_blend_switch", 0.32)
                        ),
                        "subject_visibility_threshold": float(
                            retry_cfg.get("subject_visibility_threshold", 0.46)
                        ),
                        "lock_confidence_threshold": float(
                            retry_cfg.get("lock_confidence_threshold", 0.72)
                        ),
                        "speaker_confidence_threshold": float(
                            retry_cfg.get("speaker_confidence_threshold", 0.62)
                        ),
                        "handoff_min_hold_windows": int(
                            retry_cfg.get("handoff_min_hold_windows", 2)
                        ),
                        "confident_lock_min_hold_windows": int(
                            retry_cfg.get("confident_lock_min_hold_windows", 4)
                        ),
                        "target_deadband_handoff": float(
                            retry_cfg.get("target_deadband_handoff", 0.028)
                        ),
                        "target_deadband_lock": float(
                            retry_cfg.get("target_deadband_lock", 0.018)
                        ),
                        "max_delta_handoff": float(
                            retry_cfg.get("max_delta_handoff", 0.028)
                        ),
                        "max_delta_lock": float(retry_cfg.get("max_delta_lock", 0.020)),
                        "motion_blend_switch_handoff": float(
                            retry_cfg.get("motion_blend_switch_handoff", 0.22)
                        ),
                        "motion_blend_normal_handoff": float(
                            retry_cfg.get("motion_blend_normal_handoff", 0.14)
                        ),
                        "subject_detector_pass": str(
                            retry_cfg.get("subject_detector_pass", "final_clip_strong")
                        ),
                        "shot_reacquire_boost_windows": int(
                            retry_cfg.get("shot_reacquire_boost_windows", 2)
                        ),
                        "new_face_fast_acquire_threshold": float(
                            retry_cfg.get("new_face_fast_acquire_threshold", 0.78)
                        ),
                        "framing_mode": (
                            "square_canvas"
                            if str(retry_framing_mode).lower() == "square_canvas"
                            else (
                                "face_locked"
                                if bool(
                                    retry_cfg.get(
                                        "speaker_center_strict_mode",
                                        self.cfg.get(
                                            "speaker_center_strict_mode", True
                                        ),
                                    )
                                )
                                else str(retry_framing_mode)
                            )
                        ),
                        "progress_callback": None,
                    }
                    retry_timed = _run_in_subprocess_with_timeout(
                        "create_vertical_crop",
                        {"kwargs": retry_kwargs},
                        soft_timeout_seconds=reframe_soft_timeout_seconds,
                        hard_timeout_seconds=reframe_hard_timeout_seconds,
                        default={"ok": False, "debug_info": {}},
                        heartbeat_seconds=float(
                            self.cfg.get("heartbeat_interval_seconds", 30)
                        ),
                        on_heartbeat=self._heartbeat_callback(
                            progress_callback,
                            "reframing",
                            f"Still reframing candidate {index} retry",
                        ),
                        on_soft_timeout=lambda _elapsed: None,
                        on_hard_timeout=lambda _elapsed: (
                            self._watchdog_stats.__setitem__(
                                "hard_timeouts",
                                self._watchdog_stats.get("hard_timeouts", 0) + 1,
                            )
                        ),
                    )
                    retry_result = (
                        retry_timed["result"]
                        if isinstance(retry_timed, dict)
                        else {"ok": False, "debug_info": {}}
                    )
                    retried = bool((retry_result or {}).get("ok"))
                    retry_debug = dict((retry_result or {}).get("debug_info", {}) or {})
                    if bool((retry_timed or {}).get("soft_timeout")):
                        candidate_watchdog_action = "watchdog_fallback"
                        candidate_stage_timeout_seconds["reframe_retry_soft"] = float(
                            self.cfg.get("reframe_soft_timeout_seconds", 150)
                        )
                    retry_better = retried and int(
                        retry_debug.get("anchor_switches", 999) or 999
                    ) <= int(reframe_debug.get("anchor_switches", 999) or 999)
                    if retry_better:
                        try:
                            if os.path.exists(retry_crop):
                                if os.path.exists(crop):
                                    os.remove(crop)
                                os.replace(retry_crop, crop)
                        except Exception:
                            pass
                        reframed = True
                        reframe_debug = retry_debug
                        reframe_debug["auto_reframe_retry_used"] = True
                        candidate_watchdog_action = "watchdog_fallback"
                    else:
                        try:
                            if os.path.exists(retry_crop):
                                os.remove(retry_crop)
                        except Exception:
                            pass
                        reframe_debug["auto_reframe_retry_used"] = False
                else:
                    reframe_debug["auto_reframe_retry_used"] = False
                report["stage_timings"][f"candidate_{index}_reframe_seconds"] = round(
                    _now() - stage_start, 3
                )
                if reframed:
                    reframe_debug = self._probe_final_crop_visual(
                        crop, candidate, reframe_debug
                    )
                reframe_summary = summarize_reframe_debug(reframe_debug)
                quality_governor_decision = self._quality_governor_decision(
                    candidate, subtitle_info, reframe_debug
                )
                if (
                    not direct_candidate_mode
                    and (
                        bool(candidate.get("rejected_for_missing_payoff", False))
                        or bool(candidate.get("rejected_for_topic_jump", False))
                        or bool(candidate.get("rejected_for_confusing_story", False))
                    )
                    and not bool(candidate.get("publishable_story_override", False))
                ):
                    quality_governor_decision = "reject_story"
                if quality_governor_decision == "reject_story" and not bool(
                    candidate.get("publishable_story_override", False)
                ):
                    report["warnings"].append(
                        f"Candidate {index} rejected: low_story_quality"
                    )
                    _emit(
                        progress_callback,
                        "warning",
                        f"Candidate {index} rejected: low_story_quality",
                    )
                    candidate["rejection_reason"] = "low_story_quality"
                    continue
                if (
                    not direct_candidate_mode
                    and quality_governor_decision == "reject_visual"
                    and hard_timeout_reframe
                    and source_face_rich
                ):
                    quality_governor_decision = "retry_reframe_after_timeout"
                if not direct_candidate_mode:
                    if (
                        quality_governor_decision == "retry_reframe_subject_first"
                        and self._should_retry_reframe(reframe_debug, reframed)
                    ):
                        if bool(
                            candidate.get("publishable_story_override", False)
                            or not candidate.get("selection_visual_soft_gate", True)
                        ):
                            quality_governor_decision = "accept"
                            candidate["final_visual_hard_gate"] = False
                        else:
                            quality_governor_decision = "reject_visual"
                    if quality_governor_decision == "expand_story_boundary" and float(
                        boundary_meta.get("story_boundary_confidence", 0.0) or 0.0
                    ) < float(
                        self.cfg.get("story_boundary_confidence_threshold", 0.58)
                    ):
                        report["warnings"].append(
                            f"Candidate {index} downgraded: low_story_boundary_confidence"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} downgraded: low_story_boundary_confidence",
                        )
                        quality_governor_decision = "accept"
                    if quality_governor_decision == "reject_visual":
                        report["stats"]["final_visual_rejects"] = (
                            int(report["stats"].get("final_visual_rejects", 0) or 0) + 1
                        )
                        report["warnings"].append(
                            f"Candidate {index} downgraded: low_visual_viability"
                        )
                        _emit(
                            progress_callback,
                            "warning",
                            f"Candidate {index} downgraded: low_visual_viability",
                        )
                        quality_governor_decision = "accept"
                remote_quality_meta = enhance_clip_metadata(
                    self.cfg,
                    {
                        "subtitle_confidence": subtitle_confidence,
                        "anchor_switches": int(reframe_debug.get("anchor_switches", 0)),
                        "empty_frame_risk": 0.0
                        if bool(
                            reframe_debug.get("listener_face_fallback_used")
                            or reframe_debug.get("dialogue_center_used")
                            or reframe_debug.get("subject_person_fallback_used")
                            or reframe_debug.get("face_preserving_fallback_used")
                        )
                        else 0.35,
                    },
                )
                remote_quality_meta["remote_quality_should_retry"] = (
                    should_use_remote_fallback(
                        self.cfg,
                        {
                            "subtitle_confidence": subtitle_confidence,
                            "anchor_switches": int(
                                reframe_debug.get("anchor_switches", 0)
                            ),
                            "empty_frame_risk": 0.0
                            if bool(
                                reframe_debug.get("listener_face_fallback_used")
                                or reframe_debug.get("dialogue_center_used")
                                or reframe_debug.get("subject_person_fallback_used")
                                or reframe_debug.get("face_preserving_fallback_used")
                            )
                            else 0.35,
                        },
                    )
                )
                if not reframed:
                    strict_fallback_debug = {}
                    strict_fallback_crop = os.path.join(
                        out_dir, f"cand_{index}_crop_strict_fallback.mp4"
                    )
                    strict_fallback_mode, strict_fallback_anchor = (
                        self._select_framing_plan(
                            candidate,
                            subtitle_turns,
                            subtitle_signals,
                            reframe_cfg,
                            direct_candidate_mode=True,
                        )
                    )
                    strict_fallback_kwargs = dict(reframe_kwargs)
                    strict_fallback_kwargs.update(
                        {
                            "out_path": strict_fallback_crop,
                            "reframe_mode": "speaker_focus",
                            "reframe_anchor_mode": "stable_primary",
                            "subject_detector_pass": str(
                                reframe_cfg.get(
                                    "active_speaker_refine_profile", "final_clip_strong"
                                )
                                or "final_clip_strong"
                            ),
                            "speaker_center_strict_mode": False,
                            "speaker_lock_strict_mode": False,
                            "speaker_center_max_offset": min(
                                float(self.cfg.get("speaker_center_max_offset", 0.16)),
                                0.14,
                            ),
                            "speaker_face_lock_min_margin": max(
                                float(
                                    self.cfg.get("speaker_face_lock_min_margin", 0.10)
                                ),
                                0.14,
                            ),
                            "dialogue_center_use_threshold": max(
                                float(
                                    self.cfg.get("dialogue_center_use_threshold", 0.70)
                                ),
                                0.76,
                            ),
                            "listener_fallback_speech_hold_max_seconds": min(
                                float(
                                    self.cfg.get(
                                        "listener_fallback_speech_hold_max_seconds",
                                        0.45,
                                    )
                                ),
                                0.30,
                            ),
                            "force_center_crop": False,
                            "force_face_preserving_crop": False,
                            "face_preserving_anchor_center": (
                                source_avg_center_x,
                                source_avg_center_y,
                            )
                            if source_face_rich
                            else None,
                            "face_preserving_face_size": source_avg_face_size
                            if source_face_rich
                            else 0.0,
                            "face_preserving_safe_margin": float(
                                reframe_cfg.get(
                                    "face_preserving_safe_margin",
                                    self.cfg.get("face_preserving_safe_margin", 0.12),
                                )
                            ),
                            "framing_mode": "square_canvas"
                            if str(selected_framing_mode).lower() == "square_canvas"
                            else "dialogue_dual",
                            "reframe_transition_mode": "hard_switch",
                            "reframe_allow_wide_dialogue_center": False,
                            "dialogue_two_shot_preferred": True,
                            "window_sec": min(
                                float(reframe_cfg.get("crop_window_sec", 0.8)), 0.24
                            ),
                            "sample_fps": max(
                                int(reframe_cfg.get("face_detection_fps", 3)), 8
                            ),
                            "speaker_switch_hold_windows": 0,
                            "accent_frame_hold_windows": int(accent_frame_hold_windows),
                        }
                    )
                    strict_fallback = _run_in_subprocess_with_timeout(
                        "create_vertical_crop",
                        {"kwargs": strict_fallback_kwargs},
                        soft_timeout_seconds=min(reframe_soft_timeout_seconds, 20.0),
                        hard_timeout_seconds=min(reframe_hard_timeout_seconds, 30.0),
                        default={"ok": False, "debug_info": {}},
                        heartbeat_seconds=float(
                            self.cfg.get("heartbeat_interval_seconds", 30)
                        ),
                        on_heartbeat=self._heartbeat_callback(
                            progress_callback,
                            "reframing",
                            f"Still reframing candidate {index} face fallback",
                        ),
                        on_soft_timeout=lambda _elapsed: None,
                        on_hard_timeout=lambda _elapsed: (
                            self._watchdog_stats.__setitem__(
                                "hard_timeouts",
                                self._watchdog_stats.get("hard_timeouts", 0) + 1,
                            )
                        ),
                    )
                    strict_fallback_result = (
                        strict_fallback["result"]
                        if isinstance(strict_fallback, dict)
                        else {"ok": False, "debug_info": {}}
                    )
                    strict_fallback_ok = bool((strict_fallback_result or {}).get("ok"))
                    strict_fallback_debug = dict(
                        (strict_fallback_result or {}).get("debug_info", {}) or {}
                    )
                    strict_fallback_center_safe = (
                        bool(
                            strict_fallback_debug.get(
                                "center_safe_fallback_used", False
                            )
                        )
                        or str(
                            strict_fallback_debug.get("subject_acquisition_state", "")
                        )
                        == "no_visible_subject"
                    )
                    if (
                        strict_fallback_ok
                        and os.path.exists(strict_fallback_crop)
                        and os.path.getsize(strict_fallback_crop) > 1024
                        and not strict_fallback_center_safe
                    ):
                        try:
                            if os.path.exists(crop):
                                os.remove(crop)
                            os.replace(strict_fallback_crop, crop)
                        except Exception:
                            shutil.copy(strict_fallback_crop, crop)
                        reframed = True
                        reframe_debug = strict_fallback_debug
                        reframe_debug["strict_face_fallback_used"] = True
                        reframe_debug["face_preserving_fallback_used"] = bool(
                            strict_fallback_debug.get(
                                "face_preserving_fallback_used", False
                            )
                        )
                        reframe_debug["face_preserving_fallback_reason"] = str(
                            strict_fallback_debug.get(
                                "face_preserving_fallback_reason", ""
                            )
                        )
                        reframe_debug["auto_reframe_retry_used"] = True
                    elif not reframed:
                        center_safe_crop = os.path.join(
                            out_dir, f"cand_{index}_crop_center_safe.mp4"
                        )
                        center_safe_kwargs = dict(reframe_kwargs)
                        center_safe_kwargs.update(
                            {
                                "out_path": center_safe_crop,
                                "speaker_center_strict_mode": False,
                                "speaker_lock_strict_mode": False,
                                "speaker_center_max_offset": min(
                                    float(
                                        self.cfg.get("speaker_center_max_offset", 0.16)
                                    ),
                                    0.14,
                                ),
                                "speaker_face_lock_min_margin": max(
                                    float(
                                        self.cfg.get(
                                            "speaker_face_lock_min_margin", 0.10
                                        )
                                    ),
                                    0.14,
                                ),
                                "dialogue_center_use_threshold": max(
                                    float(
                                        self.cfg.get(
                                            "dialogue_center_use_threshold", 0.70
                                        )
                                    ),
                                    0.76,
                                ),
                                "listener_fallback_speech_hold_max_seconds": min(
                                    float(
                                        self.cfg.get(
                                            "listener_fallback_speech_hold_max_seconds",
                                            0.45,
                                        )
                                    ),
                                    0.30,
                                ),
                                "force_center_crop": False,
                                "framing_mode": "square_canvas"
                                if str(selected_framing_mode).lower() == "square_canvas"
                                else "dialogue_dual",
                                "reframe_transition_mode": "smooth",
                                "reframe_allow_wide_dialogue_center": True,
                                "dialogue_two_shot_preferred": True,
                                "window_sec": min(
                                    float(reframe_cfg.get("crop_window_sec", 0.8)), 0.24
                                ),
                                "sample_fps": max(
                                    int(reframe_cfg.get("face_detection_fps", 3)), 6
                                ),
                                "speaker_switch_hold_windows": 0,
                                "accent_frame_hold_windows": int(
                                    accent_frame_hold_windows
                                ),
                            }
                        )
                        center_safe = _run_in_subprocess_with_timeout(
                            "create_vertical_crop",
                            {"kwargs": center_safe_kwargs},
                            soft_timeout_seconds=min(
                                reframe_soft_timeout_seconds, 12.0
                            ),
                            hard_timeout_seconds=min(
                                reframe_hard_timeout_seconds, 20.0
                            ),
                            default={"ok": False, "debug_info": {}},
                            heartbeat_seconds=float(
                                self.cfg.get("heartbeat_interval_seconds", 30)
                            ),
                            on_heartbeat=self._heartbeat_callback(
                                progress_callback,
                                "reframing",
                                f"Still reframing candidate {index} center fallback",
                            ),
                            on_soft_timeout=lambda _elapsed: None,
                            on_hard_timeout=lambda _elapsed: (
                                self._watchdog_stats.__setitem__(
                                    "hard_timeouts",
                                    self._watchdog_stats.get("hard_timeouts", 0) + 1,
                                )
                            ),
                        )
                        center_safe_result = (
                            center_safe["result"]
                            if isinstance(center_safe, dict)
                            else {"ok": False, "debug_info": {}}
                        )
                        center_safe_ok = bool((center_safe_result or {}).get("ok"))
                        center_safe_debug = dict(
                            (center_safe_result or {}).get("debug_info", {}) or {}
                        )
                        if not center_safe_ok:
                            inline_debug = {}
                            inline_kwargs = dict(center_safe_kwargs)
                            inline_kwargs["debug_info"] = inline_debug
                            center_safe_ok = bool(create_vertical_crop(**inline_kwargs))
                            if center_safe_ok:
                                center_safe_debug = inline_debug
                        if (
                            center_safe_ok
                            and os.path.exists(center_safe_crop)
                            and os.path.getsize(center_safe_crop) > 1024
                        ):
                            try:
                                if os.path.exists(crop):
                                    os.remove(crop)
                                os.replace(center_safe_crop, crop)
                            except Exception:
                                shutil.copy(center_safe_crop, crop)
                            reframed = True
                            reframe_debug = center_safe_debug
                            reframe_debug["center_safe_fallback_used"] = True
                            reframe_debug["center_safe_fallback_reason"] = str(
                                center_safe_debug.get(
                                    "center_safe_fallback_reason",
                                    "strict_face_fallback_failed",
                                )
                            )
                            reframe_debug["auto_reframe_retry_used"] = True
                        else:
                            reframe_debug = dict(center_safe_debug or {})
                            reframe_debug["center_safe_fallback_used"] = True
                            reframe_debug["center_safe_fallback_reason"] = (
                                "center_safe_subprocess_failed"
                            )
                            reframe_debug["auto_reframe_retry_used"] = True
                            reframed = False
                            report["warnings"].append(
                                f"Candidate {index} rejected: center_safe_subprocess_failed"
                            )
                            _emit(
                                progress_callback,
                                "warning",
                                f"Candidate {index} rejected: center_safe_subprocess_failed",
                            )
                            continue

                if reframed:
                    reframe_debug = self._probe_final_crop_visual(
                        crop, candidate, reframe_debug
                    )
                    final_crop_decision = self._quality_governor_decision(
                        candidate, subtitle_info, reframe_debug
                    )
                    if (
                        not direct_candidate_mode
                        and (
                            bool(candidate.get("rejected_for_missing_payoff", False))
                            or bool(candidate.get("rejected_for_topic_jump", False))
                            or bool(
                                candidate.get("rejected_for_confusing_story", False)
                            )
                        )
                        and not bool(candidate.get("publishable_story_override", False))
                    ):
                        final_crop_decision = "reject_story"
                    if not direct_candidate_mode:
                        if final_crop_decision == "reject_visual":
                            report["stats"]["final_visual_rejects"] = (
                                int(report["stats"].get("final_visual_rejects", 0) or 0)
                                + 1
                            )
                            report["warnings"].append(
                                f"Candidate {index} downgraded: low_visual_viability"
                            )
                            _emit(
                                progress_callback,
                                "warning",
                                f"Candidate {index} downgraded: low_visual_viability",
                            )
                            final_crop_decision = "accept"
                        if final_crop_decision == "reject_story":
                            report["warnings"].append(
                                f"Candidate {index} rejected: low_story_quality"
                            )
                            _emit(
                                progress_callback,
                                "warning",
                                f"Candidate {index} rejected: low_story_quality",
                            )
                            candidate["rejection_reason"] = "low_story_quality"
                            continue
                    elif (
                        max(
                            float(
                                reframe_debug.get("final_crop_face_presence", 0.0)
                                or 0.0
                            ),
                            float(
                                reframe_debug.get("final_crop_subject_presence", 0.0)
                                or 0.0
                            ),
                        )
                        >= 0.18
                    ):
                        quality_governor_decision = "accept"
                    if final_crop_decision == "accept":
                        quality_governor_decision = "accept"

                final_path = os.path.join(out_dir, f"short_{index}.mp4")
                tmp_video = final_path + ".tmp.mp4"
                shutil.copy(crop, tmp_video)
                _emit(progress_callback, "exporting", f"Exporting short {index}")
                stage_start = _now()
                subtitle_status = "generated"
                if burn_subtitles_safe(
                    tmp_video, subtitle_info, final_path, self.cfg, progress_callback
                ):
                    subtitle_status = "burned"
                    try:
                        os.remove(tmp_video)
                    except Exception:
                        pass
                else:
                    os.replace(tmp_video, final_path)
                report["stage_timings"][f"candidate_{index}_export_seconds"] = round(
                    _now() - stage_start, 3
                )

                has_video, final_duration = probe_video(final_path)
                if not has_video:
                    report["warnings"].append(
                        f"Candidate {index} rejected: export_failed"
                    )
                    try:
                        if os.path.exists(final_path):
                            os.remove(final_path)
                    except Exception:
                        pass
                    continue
                (
                    has_video_geometry,
                    final_duration,
                    final_video_width,
                    final_video_height,
                ) = probe_video_geometry(final_path)
                if not has_video_geometry:
                    report["warnings"].append(
                        f"Candidate {index} rejected: export_geometry_probe_failed"
                    )
                    try:
                        if os.path.exists(final_path):
                            os.remove(final_path)
                    except Exception:
                        pass
                    continue
                vertical_export_ok = bool(
                    final_video_height > final_video_width
                    and final_video_width > 0
                    and final_video_height > 0
                )
                final_canvas_mode = (
                    "vertical_9_16" if vertical_export_ok else "non_vertical"
                )
                geometry_rejection_reason = ""
                if not vertical_export_ok:
                    geometry_rejection_reason = (
                        f"non_vertical_export_{final_video_width}x{final_video_height}"
                    )
                    candidate["final_visual_hard_gate"] = False
                    candidate.setdefault("rejection_reason", geometry_rejection_reason)
                    report["warnings"].append(
                        f"Candidate {index} rejected: {geometry_rejection_reason}"
                    )
                    try:
                        if os.path.exists(final_path):
                            os.remove(final_path)
                    except Exception:
                        pass
                    continue

                source_window_duration = max(
                    0.0,
                    float(candidate.get("end", 0.0) or 0.0)
                    - float(candidate.get("start", 0.0) or 0.0),
                )
                pacing_score = _pacing_score_from_pause_timeline(
                    list(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_timeline", []
                        )
                        or []
                    ),
                    original_duration=source_window_duration
                    or float(final_duration or 0.0),
                    output_duration=float(final_duration or 0.0),
                    subtitle_signals=subtitle_signals,
                )
                if (
                    float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pacing_score", 0.0
                        )
                        or 0.0
                    )
                    > 0.0
                ):
                    pacing_score = max(
                        pacing_score,
                        float(
                            (trim_silence_in_candidate_ms.last_stats or {}).get(
                                "pacing_score", 0.0
                            )
                            or 0.0
                        ),
                    )

                subtitle_speech_miss_count = int(
                    subtitle_signals.get("subtitle_blackout_count", 0) or 0
                ) + int(subtitle_signals.get("subtitle_visual_drop_count", 0) or 0)
                subtitle_text_recovery_used = bool(
                    subtitle_info.get("subtitle_correction_used", False)
                    or subtitle_info.get("subtitle_quality_retry_used", False)
                    or subtitle_info.get("auto_quality_retry_used", False)
                    or subtitle_info.get("subtitle_alignment_used", False)
                )
                subtitle_quality_gate_status = "ok"
                if (
                    float(subtitle_signals.get("subtitle_quality_score", 0.0) or 0.0)
                    < float(self.cfg.get("subtitle_quality_score_threshold", 0.66))
                    or float(
                        subtitle_signals.get("subtitle_text_sanity_score", 0.0) or 0.0
                    )
                    < float(self.cfg.get("subtitle_text_sanity_threshold", 0.62))
                    or subtitle_speech_miss_count > 0
                ):
                    subtitle_quality_gate_status = "needs_retry"
                subtitle_seed_rejected_for_title = bool(
                    float(
                        subtitle_signals.get("subtitle_text_sanity_score", 0.0) or 0.0
                    )
                    < float(self.cfg.get("subtitle_text_sanity_threshold", 0.62))
                    or float(subtitle_confidence or 0.0)
                    < float(self.cfg.get("subtitle_confidence_threshold", 0.76))
                )
                # Keep the existing review state; do not reset it here.
                cold_open_dead_time_penalty = float(
                    candidate.get("score_breakdown", {}).get(
                        "cold_open_dead_time_penalty", 0.0
                    )
                    or 0.0
                )
                first_frame_clarity_score = round(
                    min(
                        1.0,
                        max(
                            0.0,
                            float(
                                candidate.get("score_breakdown", {}).get(
                                    "first_frame_clarity_score", 0.0
                                )
                                or 0.0
                            )
                            or (
                                float(
                                    candidate.get("score_breakdown", {}).get(
                                        "first_second_hook_score", 0.0
                                    )
                                    or 0.0
                                )
                                * 0.45
                                + float(
                                    candidate.get("score_breakdown", {}).get(
                                        "visual_premise_strength", 0.0
                                    )
                                    or 0.0
                                )
                                * 0.35
                                + max(
                                    0.0,
                                    1.0
                                    - float(
                                        candidate.get("score_breakdown", {}).get(
                                            "cold_open_dead_time_penalty", 0.0
                                        )
                                        or 0.0
                                    ),
                                )
                                * 0.20
                            ),
                        ),
                    ),
                    4,
                )
                visible_stakes_score = round(
                    min(
                        1.0,
                        max(
                            0.0,
                            float(
                                candidate.get("score_breakdown", {}).get(
                                    "visible_stakes_score", 0.0
                                )
                                or 0.0
                            )
                            or (
                                float(
                                    candidate.get("score_breakdown", {}).get(
                                        "visual_premise_strength", 0.0
                                    )
                                    or 0.0
                                )
                                * 0.46
                                + float(
                                    candidate.get("score_breakdown", {}).get(
                                        "premise_signal_score", 0.0
                                    )
                                    or 0.0
                                )
                                * 0.34
                                + float(
                                    candidate.get("score_breakdown", {}).get(
                                        "sound_off_hook_score", 0.0
                                    )
                                    or 0.0
                                )
                                * 0.20
                            ),
                        ),
                    ),
                    4,
                )
                sound_off_premise_score = round(
                    max(
                        float(
                            candidate.get("score_breakdown", {}).get(
                                "sound_off_premise_score", 0.0
                            )
                            or 0.0
                        ),
                        float(
                            candidate.get("score_breakdown", {}).get(
                                "sound_off_hook_score", 0.0
                            )
                            or 0.0
                        ),
                    ),
                    4,
                )
                dialogue_dependency_penalty = round(
                    min(
                        1.0,
                        max(
                            0.0,
                            float(
                                candidate.get("score_breakdown", {}).get(
                                    "dialogue_dependency_penalty", 0.0
                                )
                                or 0.0
                            )
                            or max(
                                0.0,
                                float(
                                    candidate.get("score_breakdown", {}).get(
                                        "dialogue_exchange_score", 0.0
                                    )
                                    or 0.0
                                )
                                - max(
                                    float(
                                        candidate.get("score_breakdown", {}).get(
                                            "visual_premise_strength", 0.0
                                        )
                                        or 0.0
                                    ),
                                    float(
                                        candidate.get("score_breakdown", {}).get(
                                            "sound_off_hook_score", 0.0
                                        )
                                        or 0.0
                                    ),
                                    float(
                                        candidate.get("score_breakdown", {}).get(
                                            "first_second_hook_score", 0.0
                                        )
                                        or 0.0
                                    ),
                                ),
                            ),
                        ),
                    ),
                    4,
                )
                if (
                    visible_stakes_score
                    >= max(first_frame_clarity_score, sound_off_premise_score)
                    and visible_stakes_score >= 0.72
                ):
                    hook_type = "stakes_first"
                    selected_opening_reason = "visible_stakes_are_immediately_readable"
                elif (
                    first_frame_clarity_score >= sound_off_premise_score
                    and first_frame_clarity_score >= 0.72
                ):
                    hook_type = "first_frame_clarity"
                    selected_opening_reason = "first_frame_explains_the_scene"
                elif sound_off_premise_score >= 0.68:
                    hook_type = "sound_off_premise"
                    selected_opening_reason = "sound_off_premise_is_clear"
                elif dialogue_dependency_penalty > 0.42:
                    hook_type = "dialogue_conflict"
                    selected_opening_reason = "dialogue_conflict_with_visible_payoff"
                else:
                    hook_type = "balanced_hook"
                    selected_opening_reason = "balanced_hook_opening"
                duration_policy = candidate.get(
                    "duration_policy"
                ) or self._candidate_duration_policy(candidate, subtitle_info)
                candidate["duration_policy"] = dict(duration_policy)
                candidate["story_thread_id"] = str(
                    candidate.get("story_thread_id")
                    or self._story_thread_id(candidate, subtitle_info)
                )
                candidate["story_coherence_score"] = float(
                    candidate.get(
                        "story_coherence_score",
                        self._candidate_story_coherence(candidate, subtitle_info),
                    )
                    or 0.0
                )
                candidate["coherence_merge_reason"] = str(
                    candidate.get("coherence_merge_reason", "thread_seed")
                )
                candidate["coherence_rejection_reason"] = str(
                    candidate.get("coherence_rejection_reason", "")
                )
                coherence_threshold = float(
                    self.cfg.get("story_coherence_threshold", 0.62)
                )
                if candidate["story_coherence_score"] < coherence_threshold:
                    candidate["coherence_rejection_reason"] = "low_coherence"
                    report["warnings"].append(
                        f"[story] rejected_merge low_coherence={candidate['story_coherence_score']:.2f}"
                    )
                    _emit(
                        progress_callback,
                        "story",
                        f"rejected_merge low_coherence={candidate['story_coherence_score']:.2f}",
                    )
                    needs_review = True
                    if candidate.get("stitched_story_unit", False):
                        acceptance_reason = "coherence_rebuilt"
                story_window_plan = self._build_story_window_plan(
                    candidate, subtitle_info, duration_policy
                )
                candidate["story_window_plan"] = dict(story_window_plan)
                candidate["story_window_segments"] = list(
                    story_window_plan.get("segments", []) or []
                )
                candidate["story_window_assembly_used"] = True
                candidate["clarity_score"] = float(
                    story_window_plan.get(
                        "clarity_score",
                        candidate["score_breakdown"].get("story_clarity_score", 0.0),
                    )
                    or 0.0
                )
                candidate["duration_penalty"] = float(
                    story_window_plan.get(
                        "duration_penalty",
                        candidate["score_breakdown"].get("duration_penalty", 0.0),
                    )
                    or 0.0
                )
                candidate["window_expansion_meta"] = dict(
                    story_window_plan.get("window_expansion_meta", {}) or {}
                )
                candidate["merge_reason"] = str(
                    story_window_plan.get(
                        "merge_reason",
                        candidate.get(
                            "merge_reason",
                            candidate.get("stitch_reason", "story_window_assembly"),
                        ),
                    )
                )
                story_profile = self._story_arc_profile(
                    candidate, subtitle_info, boundary_meta
                )
                candidate["conversation_id"] = story_profile["conversation_id"]
                candidate["story_arc_shape"] = story_profile["story_arc_shape"]
                candidate["story_completion_score"] = float(
                    story_profile["story_completion_score"]
                )
                candidate["context_completeness_score"] = float(
                    story_profile["context_completeness_score"]
                )
                candidate["hook_type"] = story_profile["hook_type"]
                candidate["payoff_type"] = story_profile["payoff_type"]
                candidate["topic_shift_events"] = int(
                    story_profile["topic_shift_events"]
                )
                candidate["rejected_for_missing_payoff"] = bool(
                    story_profile["payoff_type"] == "unfinished"
                    or story_profile["story_completion_score"] < 0.56
                )
                candidate["rejected_for_topic_jump"] = bool(
                    candidate["topic_shift_events"] > 0
                    or candidate["story_coherence_score"] < coherence_threshold
                )
                candidate["rejected_for_confusing_story"] = bool(
                    float(
                        candidate["score_breakdown"].get("story_clarity_score", 0.0)
                        or 0.0
                    )
                    < float(self.cfg.get("story_clarity_threshold", 0.56))
                    or candidate["context_completeness_score"] < 0.46
                )
                story_unit_type = str(
                    candidate.get("score_breakdown", {}).get(
                        "story_unit_type",
                        candidate.get("story_unit_type", "dialogue_cluster"),
                    )
                    or "dialogue_cluster"
                ).lower()
                if story_unit_type in {"rescue_urgency", "danger_escape"}:
                    payoff_type = "rescue_or_escape"
                elif story_unit_type in {"reveal_discovery", "investigation_clue"}:
                    payoff_type = "reveal"
                elif story_unit_type in {
                    "confrontation",
                    "accusation_denial",
                    "impossible_choice",
                }:
                    payoff_type = "conflict"
                elif story_unit_type == "emotional_confession":
                    payoff_type = "emotional"
                else:
                    payoff_type = "scene"
                premise_summary = f"{story_unit_type.replace('_', ' ')}; {selected_opening_reason.replace('_', ' ')}"
                story_card = {
                    "premise_summary": premise_summary,
                    "visible_stakes_score": visible_stakes_score,
                    "first_frame_clarity_score": first_frame_clarity_score,
                    "sound_off_premise_score": sound_off_premise_score,
                    "dialogue_dependency_penalty": dialogue_dependency_penalty,
                    "hook_type": hook_type,
                    "selected_opening_reason": selected_opening_reason,
                    "payoff_type": payoff_type,
                    "story_unit_type": story_unit_type,
                    "story_arc_shape": story_profile["story_arc_shape"],
                    "opening_strength": round(
                        max(
                            first_frame_clarity_score,
                            visible_stakes_score,
                            sound_off_premise_score,
                        )
                        - min(0.30, dialogue_dependency_penalty * 0.12)
                        - min(0.18, cold_open_dead_time_penalty * 0.18),
                        4,
                    ),
                }
                story_assets = _build_story_assets(
                    subtitle_info,
                    conversation_id=story_profile["conversation_id"],
                    source_text=story_card["premise_summary"],
                    language=subtitle_info.get(
                        "language", self.cfg.get("subtitle_language", "auto")
                    ),
                )
                # Override story assessment with StoryChain data when it's more complete.
                # The score_breakdown has a pessimistic estimate (from ranking without subtitles).
                # The StoryChain built from actual subtitle segments is more accurate.
                _sc_dict = dict(story_assets.get("story_chain") or {})
                _ss_dict = dict(story_assets.get("story_summary") or {})
                _chain_arc = str(_sc_dict.get("story_arc_shape") or "")
                _chain_completion = float(
                    _ss_dict.get("story_completion_score")
                    or _ss_dict.get("completion_score")
                    or 0.0
                )
                _chain_is_complete = bool(
                    _ss_dict.get("is_complete")
                    or (_chain_arc == "hook_setup_escalation_payoff")
                )
                _chain_conflict = str(_ss_dict.get("conflict_type") or "")
                _chain_topic = str(_ss_dict.get("topic_phrase") or "")
                # Apply override when chain gives richer data than the ranking estimate
                if _chain_arc and _chain_completion > float(
                    candidate.get("story_completion_score", 0.0) or 0.0
                ):
                    candidate["story_arc_shape"] = _chain_arc
                    candidate["story_completion_score"] = round(_chain_completion, 4)
                    candidate["score_breakdown"]["story_arc_shape"] = _chain_arc
                    candidate["score_breakdown"]["story_completion_score"] = round(
                        _chain_completion, 4
                    )
                # If chain says is_complete, remove missing_payoff rejection flag
                if _chain_is_complete:
                    candidate["rejected_for_missing_payoff"] = False
                speaker_lock_failure_reason = ""
                speaker_lock_state = str(
                    reframe_debug.get(
                        "subject_acquisition_outcome",
                        reframe_debug.get("subject_acquisition_state", ""),
                    )
                    or ""
                )
                if (
                    bool(reframe_debug.get("center_safe_fallback_used", False))
                    and source_face_rich
                ):
                    speaker_lock_failure_reason = "center_safe_despite_source_subject"
                elif (
                    float(reframe_debug.get("speaker_centered_rate", 0.0) or 0.0) <= 0.0
                    and source_face_rich
                ):
                    speaker_lock_failure_reason = (
                        "source_subject_present_but_not_centered"
                    )
                elif float(reframe_debug.get("face_edge_clip_rate", 0.0) or 0.0) > 0.22:
                    speaker_lock_failure_reason = "face_clipped"
                face_present_but_lock_failed = bool(
                    source_face_rich
                    and (
                        bool(reframe_debug.get("center_safe_fallback_used", False))
                        or float(reframe_debug.get("speaker_centered_rate", 0.0) or 0.0)
                        <= 0.0
                    )
                )
                speaker_fallback_mode = (
                    "center_safe"
                    if bool(reframe_debug.get("center_safe_fallback_used", False))
                    else "face_preserving"
                    if bool(reframe_debug.get("face_preserving_fallback_used", False))
                    else "strict_face_success"
                )
                vertical_speaker_crop_ok = bool(
                    vertical_export_ok
                    and not bool(reframe_debug.get("center_safe_fallback_used", False))
                    and float(reframe_debug.get("speaker_centered_rate", 0.0) or 0.0)
                    > 0.0
                    and float(reframe_debug.get("face_edge_clip_rate", 0.0) or 0.0)
                    <= 0.22
                )
                face_clipped_windows = int(
                    round(
                        float(reframe_debug.get("face_edge_clip_rate", 0.0) or 0.0)
                        * max(
                            1,
                            int(
                                reframe_debug.get("speaker_face_centered_windows", 0)
                                or 0
                            )
                            + int(reframe_debug.get("dialogue_center_windows", 0) or 0),
                        )
                    )
                )

                meta = {
                    **self._pipeline_identity,
                    "candidate_id": f"cand_{index:05d}",
                    "candidate_index": int(index),
                    "source_file": os.path.abspath(video_path),
                    "candidate_rank": candidate_rank,
                    "test_mode_enabled": test_mode_enabled,
                    "source_timestamps": [candidate["start"], candidate["end"]],
                    "final_duration": round(final_duration, 3),
                    "story_mode": str(
                        duration_policy.get("story_mode", self._story_mode())
                    ),
                    "tension_mode_active": str(
                        duration_policy.get("story_mode", self._story_mode())
                    )
                    == "tension",
                    "tension_context_score": float(
                        duration_policy.get("tension_context_score", 0.0) or 0.0
                    ),
                    "macro_context_index": int(
                        duration_policy.get("macro_context_index", 0) or 0
                    ),
                    "macro_context_start": float(
                        duration_policy.get("macro_context_start", 0.0) or 0.0
                    ),
                    "macro_context_end": float(
                        duration_policy.get("macro_context_end", 0.0) or 0.0
                    ),
                    "macro_context_window_seconds": float(
                        duration_policy.get(
                            "macro_context_window_seconds",
                            self.cfg.get("tension_context_window_seconds", 1200),
                        )
                        or self.cfg.get("tension_context_window_seconds", 1200)
                    ),
                    "duration_policy_band": str(
                        duration_policy.get("band", "hook_first_short")
                    ),
                    "target_duration_seconds": float(
                        duration_policy.get(
                            "target_seconds", self.cfg.get("target_story_seconds", 45)
                        )
                    ),
                    "min_publishable_seconds": float(
                        duration_policy.get(
                            "min_publishable_seconds",
                            self.cfg.get("min_publishable_seconds", 35),
                        )
                        or self.cfg.get("min_publishable_seconds", 35)
                    ),
                    "duration_soft_max_seconds": float(
                        duration_policy.get(
                            "soft_max_seconds",
                            self.cfg.get("story_soft_max_seconds", 45),
                        )
                    ),
                    "duration_hard_max_seconds": float(
                        duration_policy.get(
                            "hard_max_seconds",
                            self.cfg.get(
                                "story_hard_max_seconds",
                                self.cfg.get("allow_story_extension_seconds", 60),
                            ),
                        )
                    ),
                    "cold_open_recut_applied": bool(
                        candidate.get("cold_open_recut_applied", False)
                    ),
                    "cold_open_recut_shift_seconds": float(
                        candidate.get("cold_open_recut_shift_seconds", 0.0) or 0.0
                    ),
                    "duration_extension_reason": str(
                        duration_policy.get("extension_reason", "hook_first_default")
                    ),
                    "exceptional_duration_used": bool(
                        duration_policy.get("exceptional_duration_used", False)
                    ),
                    "score_breakdown": candidate["score_breakdown"],
                }
                # Inject conflict/topic into meta for hashtag and title use
                if _chain_conflict and _chain_conflict != "none":
                    meta["conflict_type"] = _chain_conflict
                if _chain_topic:
                    meta["topic_phrase"] = _chain_topic
                meta.update({
                    "subtitle_status": subtitle_status,
                    "subtitle_language": subtitle_info.get(
                        "language", self.cfg.get("subtitle_language", "auto")
                    ),
                    "subtitle_confidence": subtitle_confidence,
                    "subtitle_turns": subtitle_turns,
                    "subtitle_render_mode": self.cfg.get(
                        "subtitle_render_mode", "ass_word_highlight"
                    ),
                    "subtitle_display_mode": self.cfg.get(
                        "subtitle_display_mode",
                        self.cfg.get("subtitle_chunk_mode", "sentence_highlight"),
                    ),
                    "subtitle_sentence_count": subtitle_signals.get(
                        "subtitle_sentence_count", subtitle_turns
                    ),
                    "subtitle_active_highlight_color": self.cfg.get(
                        "subtitle_active_word_color", "#FFD54F"
                    ),
                    "subtitle_processing_mode": subtitle_info.get(
                        "subtitle_processing_mode",
                        self.cfg.get("subtitle_processing_mode", "balanced_local"),
                    ),
                    "subtitle_correction_used": bool(
                        subtitle_info.get("subtitle_correction_used", False)
                    ),
                    "subtitle_quality_retry_used": bool(
                        subtitle_info.get("subtitle_quality_retry_used", False)
                    ),
                    "subtitle_alignment_used": bool(
                        subtitle_info.get("subtitle_alignment_used", False)
                    ),
                    "auto_quality_retry_used": bool(
                        subtitle_info.get("auto_quality_retry_used", False)
                    ),
                    "subtitle_renderer_mode": subtitle_signals.get(
                        "subtitle_renderer_mode",
                        self.cfg.get(
                            "subtitle_renderer_mode", "persistent_sentence_layer"
                        ),
                    ),
                    "subtitle_text_sanity_score": float(
                        subtitle_signals.get("subtitle_text_sanity_score", 0.0) or 0.0
                    ),
                    "subtitle_language_consistency": float(
                        subtitle_signals.get("subtitle_language_consistency", 0.0)
                        or 0.0
                    ),
                    "subtitle_quality_score": float(
                        subtitle_signals.get("subtitle_quality_score", 0.0) or 0.0
                    ),
                    "subtitle_speech_miss_count": int(subtitle_speech_miss_count),
                    "subtitle_text_recovery_used": bool(subtitle_text_recovery_used),
                    "subtitle_seed_rejected_for_title": bool(
                        subtitle_seed_rejected_for_title
                    ),
                    "subtitle_quality_gate_status": subtitle_quality_gate_status,
                    "story_card": story_card,
                    "story_summary": story_assets["story_summary"],
                    "story_chain": story_assets["story_chain"],
                    "story_fragments": story_assets["story_fragments"],
                    "story_window_assembly_used": bool(
                        candidate.get("story_window_assembly_used", False)
                    ),
                    "story_window_plan": dict(candidate.get("story_window_plan", {})),
                    "story_window_segments": list(
                        candidate.get("story_window_segments", [])
                    ),
                    "story_thread_id": str(
                        candidate.get(
                            "story_thread_id",
                            self._story_thread_id(candidate, subtitle_info),
                        )
                    ),
                    "conversation_id": str(
                        candidate.get(
                            "conversation_id", story_profile["conversation_id"]
                        )
                    ),
                    "story_arc_shape": str(
                        candidate.get(
                            "story_arc_shape", story_profile["story_arc_shape"]
                        )
                    ),
                    "story_completion_score": float(
                        candidate.get(
                            "story_completion_score",
                            story_profile["story_completion_score"],
                        )
                    ),
                    "context_completeness_score": float(
                        candidate.get(
                            "context_completeness_score",
                            story_profile["context_completeness_score"],
                        )
                    ),
                    "story_coherence_score": float(
                        candidate.get(
                            "story_coherence_score",
                            self._candidate_story_coherence(candidate, subtitle_info),
                        )
                        or 0.0
                    ),
                    "coherence_merge_reason": str(
                        candidate.get("coherence_merge_reason", "")
                    ),
                    "coherence_rejection_reason": str(
                        candidate.get("coherence_rejection_reason", "")
                    ),
                    "clarity_score": float(
                        candidate.get(
                            "clarity_score",
                            candidate["score_breakdown"].get(
                                "story_clarity_score", 0.0
                            ),
                        )
                        or 0.0
                    ),
                    "duration_penalty": float(
                        candidate.get(
                            "duration_penalty",
                            candidate["score_breakdown"].get("duration_penalty", 0.0),
                        )
                        or 0.0
                    ),
                    "window_expansion_meta": dict(
                        candidate.get("window_expansion_meta", {}) or {}
                    ),
                    "merge_reason": str(
                        candidate.get(
                            "merge_reason",
                            candidate.get("stitch_reason", "story_window_assembly"),
                        )
                    ),
                    "pacing_score": round(float(pacing_score), 4),
                    "trimmed_silence_seconds": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "trimmed_silence_seconds", 0.0
                        )
                        or 0.0
                    ),
                    "silence_trim_events": list(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "silence_trim_events", []
                        )
                        or []
                    ),
                    "premise_summary": premise_summary,
                    "hook_type": hook_type,
                    "selected_opening_reason": selected_opening_reason,
                    "payoff_type": payoff_type,
                    "topic_shift_events": int(
                        candidate.get(
                            "topic_shift_events", story_profile["topic_shift_events"]
                        )
                    ),
                    "rejected_for_missing_payoff": bool(
                        candidate.get("rejected_for_missing_payoff", False)
                    ),
                    "rejected_for_topic_jump": bool(
                        candidate.get("rejected_for_topic_jump", False)
                    ),
                    "rejected_for_confusing_story": bool(
                        candidate.get("rejected_for_confusing_story", False)
                    ),
                    "subtitle_visible_block_stats": subtitle_signals.get(
                        "subtitle_visible_block_stats", {}
                    ),
                    "subtitle_event_overlap_count": int(
                        subtitle_signals.get("subtitle_event_overlap_count", 0)
                    ),
                    "subtitle_persisted_gaps_count": int(
                        subtitle_signals.get("subtitle_persisted_gaps_count", 0)
                    ),
                    "subtitle_gap_blink_count": int(
                        subtitle_signals.get("subtitle_gap_blink_count", 0)
                    ),
                    "subtitle_visual_drop_count": int(
                        subtitle_signals.get("subtitle_visual_drop_count", 0)
                    ),
                    "subtitle_phrase_clear_count": int(
                        subtitle_signals.get("subtitle_phrase_clear_count", 0)
                    ),
                    "subtitle_phrase_replace_count": int(
                        subtitle_signals.get("subtitle_phrase_replace_count", 0)
                    ),
                    "subtitle_soft_hold_count": int(
                        subtitle_signals.get("subtitle_soft_hold_count", 0)
                    ),
                    "subtitle_replace_without_clear_count": int(
                        subtitle_signals.get("subtitle_replace_without_clear_count", 0)
                    ),
                    "subtitle_true_clear_count": int(
                        subtitle_signals.get("subtitle_true_clear_count", 0)
                    ),
                    "subtitle_hold_duration_p95": float(
                        subtitle_signals.get("subtitle_hold_duration_p95", 0.0) or 0.0
                    ),
                    "subtitle_continuity_mode": subtitle_signals.get(
                        "subtitle_continuity_mode",
                        self.cfg.get(
                            "subtitle_continuity_mode", "always_on_short_gaps"
                        ),
                    ),
                    "subtitle_persist_gap_seconds": float(
                        subtitle_signals.get(
                            "subtitle_persist_gap_seconds",
                            self.cfg.get("subtitle_persist_gap_seconds", 1.25),
                        )
                    ),
                    "subtitle_anchor_jitter_px": 0
                    if str(
                        self.cfg.get("subtitle_vertical_anchor_mode", "fixed_mid_lower")
                    )
                    == "fixed_mid_lower"
                    else int(self.cfg.get("subtitle_anchor_jitter_tolerance_px", 6)),
                    "subtitle_box_geometry": {
                        "anchor_mode": str(
                            self.cfg.get(
                                "subtitle_vertical_anchor_mode", "fixed_mid_lower"
                            )
                        ),
                        "alignment": 8
                        if str(
                            self.cfg.get(
                                "subtitle_vertical_anchor_mode", "fixed_mid_lower"
                            )
                        )
                        == "fixed_mid_lower"
                        else 2,
                        "position": {"x": 360, "y": 760}
                        if str(
                            self.cfg.get(
                                "subtitle_vertical_anchor_mode", "fixed_mid_lower"
                            )
                        )
                        == "fixed_mid_lower"
                        else None,
                        "max_lines": int(self.cfg.get("subtitle_max_visible_lines", 2)),
                    },
                    "reframe_mode": self.cfg.get("reframe_mode", "balanced"),
                    "reframe_priority": self.cfg.get(
                        "reframe_priority", "stability_first"
                    ),
                    "growth_profile": self.cfg.get(
                        "growth_profile", "youtube_shorts_retention_first"
                    ),
                    "packaging_profile": self.cfg.get(
                        "packaging_profile", "ru_serial_drama"
                    ),
                    "quality_governor_decision": quality_governor_decision,
                    "watchdog_action": "watchdog_fallback"
                    if bool(
                        subtitle_info.get("auto_quality_retry_used", False)
                        or reframe_debug.get("auto_reframe_retry_used", False)
                    )
                    else candidate_watchdog_action,
                    "stage_timeout_seconds": dict(candidate_stage_timeout_seconds),
                    "stage_deferred": candidate_stage_deferred,
                    "stage_hard_timeout_triggered": candidate_stage_hard_timeout_triggered,
                    "local_quality_escalation_used": bool(
                        subtitle_info.get("auto_quality_retry_used", False)
                        or subtitle_info.get("subtitle_processing_mode")
                        == "enhanced_local"
                    ),
                    "speaker_selection_mode": self.cfg.get(
                        "speaker_selection_mode", "evidence_scored"
                    ),
                    "speaker_lock_mode": self.cfg.get(
                        "speaker_lock_mode", "state_machine"
                    ),
                    "speaker_lock_state": speaker_lock_state,
                    "speaker_lock_failure_reason": speaker_lock_failure_reason,
                    "face_present_but_lock_failed": bool(face_present_but_lock_failed),
                    "speaker_fallback_mode": speaker_fallback_mode,
                    "vertical_speaker_crop_ok": bool(vertical_speaker_crop_ok),
                    "face_clipped_windows": int(face_clipped_windows),
                    "speaker_lock_strict_mode": bool(
                        self.cfg.get("speaker_lock_strict_mode", True)
                    ),
                    "speaker_center_strict_mode": bool(
                        self.cfg.get("speaker_center_strict_mode", True)
                    ),
                    "speaker_center_max_offset": float(
                        self.cfg.get("speaker_center_max_offset", 0.16)
                    ),
                    "listener_fallback_max_hold_seconds": float(
                        self.cfg.get("listener_fallback_max_hold_seconds", 0.65)
                    ),
                    "story_boundary_confidence": float(
                        boundary_meta.get("story_boundary_confidence", 0.0) or 0.0
                    ),
                    "boundary_expand_attempted": bool(
                        boundary_meta.get("boundary_expand_attempted", False)
                    ),
                    "boundary_expand_seconds_left": float(
                        boundary_meta.get("boundary_expand_seconds_left", 0.0) or 0.0
                    ),
                    "boundary_expand_seconds_right": float(
                        boundary_meta.get("boundary_expand_seconds_right", 0.0) or 0.0
                    ),
                    "rejected_after_boundary_expansion": bool(
                        boundary_meta.get("rejected_after_boundary_expansion", False)
                    ),
                    "reframe_transition_mode": str(
                        reframe_debug.get(
                            "reframe_transition_mode",
                            self.cfg.get("reframe_transition_mode", "smooth"),
                        )
                    ),
                    "reframe_anchor_strategy": str(
                        reframe_debug.get("reframe_anchor_mode")
                        or reframe_debug.get("framing_mode")
                        or anchor_mode
                    ),
                    "framing_mode": str(
                        reframe_debug.get("framing_mode") or framing_mode
                    ),
                    "reframe_target_selection_mode": str(
                        reframe_debug.get(
                            "target_selection_mode",
                            self.cfg.get("reframe_priority", "stability_first"),
                        )
                    ),
                    "accent_frame_hold_windows": int(accent_frame_hold_windows),
                    "speaker_evidence_summary": dict(
                        reframe_debug.get("speaker_evidence_summary", {}) or {}
                    ),
                    "speaker_lock_state_usage": dict(
                        reframe_debug.get("speaker_lock_state_usage", {}) or {}
                    ),
                    "empty_frame_guard_triggered": bool(
                        reframe_debug.get("empty_frame_guard_triggered", False)
                    ),
                    "dialogue_center_candidate_count": int(
                        reframe_debug.get("dialogue_center_candidate_count", 0)
                    ),
                    "listener_hold_used": bool(
                        reframe_debug.get("listener_hold_used", False)
                    ),
                    "reframe_track_count": int(
                        reframe_debug.get(
                            "track_count", self.cfg.get("reframe_track_count_limit", 3)
                        )
                    ),
                    "reframe_anchor_switches": int(
                        reframe_debug.get("anchor_switches", 0)
                    ),
                    "reframe_dialogue_center_used": bool(
                        reframe_debug.get("dialogue_center_used", False)
                    ),
                    "reframe_speaker_to_listener_switches": int(
                        reframe_debug.get("speaker_to_listener_switches", 0)
                    ),
                    "listener_face_fallback_used": bool(
                        reframe_debug.get("listener_face_fallback_used", False)
                    ),
                    "reframe_listener_face_fallback_used": bool(
                        reframe_debug.get("listener_face_fallback_used", False)
                    ),
                    "subject_person_fallback_used": bool(
                        reframe_debug.get("subject_person_fallback_used", False)
                    ),
                    "subject_person_hold_used": bool(
                        reframe_debug.get("subject_person_hold_used", False)
                    ),
                    "reframe_scene_interest_fallback_used": bool(
                        reframe_debug.get("scene_interest_fallback_used", False)
                    ),
                    "subtitle_blackout_count": int(
                        subtitle_signals.get("subtitle_blackout_count", 0) or 0
                    ),
                    "subtitle_hold_too_long": bool(
                        (
                            subtitle_signals.get("subtitle_visible_block_stats") or {}
                        ).get(
                            "subtitle_hold_too_long",
                            subtitle_signals.get("subtitle_hold_too_long", False),
                        )
                    ),
                    "subtitle_remap_used": bool(
                        subtitle_info.get("subtitle_remap_used", False)
                    ),
                    "subtitle_rewrite_applied": bool(
                        compacted_changed
                        or subtitle_info.get("subtitle_remap_used", False)
                    ),
                    "subtitle_remap_after_silence_cut": bool(
                        subtitle_info.get(
                            "subtitle_remap_after_silence_cut",
                            self.cfg.get("subtitle_remap_after_silence_cut", True),
                        )
                    ),
                    "subtitle_quality_score": float(
                        subtitle_signals.get(
                            "subtitle_quality_score",
                            subtitle_info.get("subtitle_quality_score", 0.0),
                        )
                        or 0.0
                    ),
                    "compaction_integrity_failed": bool(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_policy_failed", False
                        )
                        and not bool(
                            (trim_silence_in_candidate_ms.last_stats or {}).get(
                                "pause_policy_applied", False
                            )
                        )
                    ),
                    "dialogue_center_balance_margin": float(
                        self.cfg.get("dialogue_center_balance_margin", 0.08)
                    ),
                    "handoff_mode": str(
                        reframe_debug.get("handoff_mode", "handoff_glide")
                    ),
                    "confident_lock_used": bool(
                        reframe_debug.get("confident_lock_used", False)
                    ),
                    "lock_confidence_avg": float(
                        reframe_debug.get("lock_confidence_avg", 0.0) or 0.0
                    ),
                    "scene_change_windows": int(
                        reframe_debug.get("scene_change_windows", 0) or 0
                    ),
                    "scene_recenter_count": int(
                        reframe_debug.get("scene_recenter_count", 0) or 0
                    ),
                    "speaker_transition_direct_windows": int(
                        reframe_debug.get("speaker_transition_direct_windows", 0) or 0
                    ),
                    "speaker_switch_latency_windows": int(
                        reframe_debug.get("speaker_switch_latency_windows", 0) or 0
                    ),
                    "subject_mode": str(
                        reframe_debug.get("subject_mode", "safe_center")
                    ),
                    "subject_visibility_ratio": float(
                        reframe_debug.get("subject_visibility_ratio", 0.0) or 0.0
                    ),
                    "face_edge_clip_rate": float(
                        reframe_debug.get("face_edge_clip_rate", 0.0) or 0.0
                    ),
                    "square_reframe_mode_used": bool(
                        reframe_debug.get("square_reframe_mode_used", False)
                    ),
                    "speaker_center_offset_avg": float(
                        reframe_debug.get("speaker_center_offset_avg", 0.0) or 0.0
                    ),
                    "speaker_center_offset_p95": float(
                        reframe_debug.get("speaker_center_offset_p95", 0.0) or 0.0
                    ),
                    "speaker_centered_rate": float(
                        reframe_debug.get("speaker_centered_rate", 0.0) or 0.0
                    ),
                    "speaker_switches": int(
                        reframe_debug.get("speaker_switches", 0) or 0
                    ),
                    "speaker_switch_count": int(
                        reframe_summary.get("speaker_switches", 0) or 0
                    ),
                    "speaker_switch_rate": round(
                        float(reframe_summary.get("speaker_switches", 0) or 0.0)
                        / max(
                            1,
                            int(
                                reframe_debug.get("speaker_face_centered_windows", 0)
                                or 0
                            )
                            + int(reframe_debug.get("dialogue_center_windows", 0) or 0)
                            + int(
                                reframe_debug.get("listener_fallback_windows", 0) or 0
                            )
                            + int(
                                reframe_debug.get("subject_person_fallback_windows", 0)
                                or 0
                            ),
                        ),
                        4,
                    ),
                    "listener_reaction_count": int(
                        reframe_summary.get("listener_reaction_count", 0) or 0
                    ),
                    "face_fallback_rate": round(
                        1.0
                        if bool(
                            reframe_summary.get("face_preserving_fallback_used", False)
                        )
                        else 0.0,
                        4,
                    ),
                    "crop_transition_count": int(
                        reframe_summary.get("speaker_switches", 0) or 0
                    ),
                    "speaker_confidence_score": float(
                        reframe_debug.get("speaker_confidence_score", 0.0) or 0.0
                    ),
                    "visual_conversation_score": float(
                        reframe_debug.get("visual_conversation_score", 0.0) or 0.0
                    ),
                    "reframe_fallback_count": int(
                        reframe_debug.get("reframe_fallback_count", 0) or 0
                    ),
                    "speaker_face_centered_windows": int(
                        reframe_debug.get("speaker_face_centered_windows", 0) or 0
                    ),
                    "dialogue_center_windows": int(
                        reframe_debug.get("dialogue_center_windows", 0) or 0
                    ),
                    "listener_fallback_windows": int(
                        reframe_debug.get("listener_fallback_windows", 0) or 0
                    ),
                    "subject_person_fallback_windows": int(
                        reframe_debug.get("subject_person_fallback_windows", 0) or 0
                    ),
                    "evidence_visible_faces_peak": int(
                        reframe_debug.get("evidence_visible_faces_peak", 0) or 0
                    ),
                    "evidence_recent_face_memory_peak": int(
                        reframe_debug.get("evidence_recent_face_memory_peak", 0) or 0
                    ),
                    "evidence_visible_persons_peak": int(
                        reframe_debug.get("evidence_visible_persons_peak", 0) or 0
                    ),
                    "subject_detector_pass": str(
                        reframe_debug.get(
                            "subject_detector_pass",
                            reframe_cfg.get("subject_detector_pass", "light"),
                        )
                    ),
                    "fast_reacquire_attempted": bool(
                        int(reframe_debug.get("shot_reacquire_windows", 0) or 0) > 0
                    ),
                    "fast_reacquire_success": bool(
                        reframe_debug.get("fast_acquire_used", False)
                        or int(reframe_debug.get("new_face_acquire_count", 0) or 0) > 0
                    ),
                    "dialogue_mode_windows": int(
                        reframe_debug.get("dialogue_mode_windows", 0) or 0
                    ),
                    "scene_interest_windows": int(
                        reframe_debug.get("scene_interest_windows", 0) or 0
                    ),
                    "no_subject_windows": int(
                        reframe_debug.get("no_subject_windows", 0) or 0
                    ),
                    "auto_reframe_retry_used": bool(
                        reframe_debug.get("auto_reframe_retry_used", False)
                    ),
                    "line_completion_passed": line_completion_passed,
                    "end_boundary_completion_ok": bool(line_completion_passed),
                    "incomplete_phrase_end_count": 0 if line_completion_passed else 1,
                    "story_clarity_score": candidate["score_breakdown"].get(
                        "story_clarity_score", 0.0
                    ),
                    "story_interest_score": candidate["score_breakdown"].get(
                        "story_interest_score", story_interest_score
                    ),
                    "story_completeness_score": candidate["score_breakdown"].get(
                        "story_completeness_score", story_completeness_score
                    ),
                    "story_context_score": candidate["score_breakdown"].get(
                        "story_context_score", story_context_score
                    ),
                    "watchability_score": candidate["score_breakdown"].get(
                        "watchability_score", 0.0
                    ),
                    "recommendation_readiness_score": candidate["score_breakdown"].get(
                        "recommendation_readiness_score", 0.0
                    ),
                    "visual_premise_strength": candidate["score_breakdown"].get(
                        "visual_premise_strength", 0.0
                    ),
                    "first_frame_clarity_score": first_frame_clarity_score,
                    "visible_stakes_score": visible_stakes_score,
                    "sound_off_premise_score": sound_off_premise_score,
                    "dialogue_dependency_penalty": dialogue_dependency_penalty,
                    "first_second_hook_score": candidate["score_breakdown"].get(
                        "first_second_hook_score", 0.0
                    ),
                    "sound_off_hook_score": candidate["score_breakdown"].get(
                        "sound_off_hook_score", 0.0
                    ),
                    "premise_signal_score": candidate["score_breakdown"].get(
                        "premise_signal_score", 0.0
                    ),
                    "hook_strength": candidate["score_breakdown"].get(
                        "hook_strength",
                        candidate["score_breakdown"].get("hook_score", 0.0),
                    ),
                    "curiosity_gap_score": candidate["score_breakdown"].get(
                        "curiosity_gap_score", 0.0
                    ),
                    "payoff_strength": candidate["score_breakdown"].get(
                        "payoff_strength",
                        candidate["score_breakdown"].get("closure_score", 0.0),
                    ),
                    "cold_open_dead_time_penalty": candidate["score_breakdown"].get(
                        "cold_open_dead_time_penalty", 0.0
                    ),
                    "packaging_quality_score": candidate["score_breakdown"].get(
                        "packaging_quality_score", 0.0
                    ),
                    "story_unit_type": candidate["score_breakdown"].get(
                        "story_unit_type", candidate.get("story_unit_type")
                    ),
                    "visual_subject_score": candidate["score_breakdown"].get(
                        "visual_subject_score", 0.0
                    ),
                    "reframe_feasibility_score": candidate["score_breakdown"].get(
                        "reframe_feasibility_score", 0.0
                    ),
                    "empty_frame_risk": candidate["score_breakdown"].get(
                        "empty_frame_risk", 0.0
                    ),
                    "interestingness_score": interestingness_score,
                    "dialogue_exchange_score": dialogue_exchange_score,
                    "hook_score": subtitle_signals.get(
                        "hook_score",
                        candidate["score_breakdown"].get("hook_score", 0.0),
                    ),
                    "development_score": subtitle_signals.get(
                        "development_score",
                        candidate["score_breakdown"].get("development_score", 0.0),
                    ),
                    "closure_score": subtitle_signals.get(
                        "closure_score",
                        candidate["score_breakdown"].get("closure_score", 0.0),
                    ),
                    "boundary_refined": boundary_refined,
                    "start_boundary_reason": boundary_meta.get("start_boundary_reason"),
                    "end_boundary_reason": boundary_meta.get("end_boundary_reason"),
                    "hook_ok": boundary_meta.get("hook_ok"),
                    "story_has_payoff": boundary_meta.get("story_has_payoff"),
                    "sentence_start_safe": boundary_meta.get("sentence_start_safe"),
                    "sentence_end_safe": boundary_meta.get("sentence_end_safe"),
                    "story_continuation_used": bool(
                        boundary_meta.get("story_continuation_used")
                        or candidate.get("story_continued_after_pause")
                    ),
                    "end_boundary_completion_ok": bool(line_completion_passed),
                    "incomplete_phrase_end_count": 0 if line_completion_passed else 1,
                    "stitched_story_unit": bool(
                        candidate.get("stitched_story_unit", False)
                    ),
                    "stitched_from_candidates": candidate.get(
                        "stitched_from_candidates", []
                    ),
                    "stitch_reason": candidate.get("stitch_reason"),
                    "publishable_story_override": bool(
                        candidate.get("publishable_story_override", False)
                    ),
                    "selection_visual_soft_gate": bool(
                        candidate.get("selection_visual_soft_gate", True)
                    ),
                    "final_visual_hard_gate": bool(
                        candidate.get("final_visual_hard_gate", True)
                    ),
                    "ranking_mode_used": candidate.get("score_breakdown", {}).get(
                        "ranking_mode_used", "deep_rank"
                    ),
                    "timeout_fallback_used": bool(
                        candidate.get("score_breakdown", {}).get(
                            "timeout_fallback_used", False
                        )
                    ),
                    "pause_removed_segments": list(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_removed_segments", []
                        )
                    ),
                    "pause_kept_for_context": list(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_kept_for_context", []
                        )
                    ),
                    "pause_cut_count": int(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_cut_count", 0
                        )
                        or 0
                    ),
                    "pause_soft_keep_count": int(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_soft_keep_count", 0
                        )
                        or 0
                    ),
                    "pause_story_keep_count": int(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_story_keep_count", 0
                        )
                        or 0
                    ),
                    "pause_policy_applied": bool(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_policy_applied", False
                        )
                    ),
                    "pause_policy_failed": bool(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_policy_failed", False
                        )
                    ),
                    "pause_cut_segments_count": int(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_cut_segments_count", 0
                        )
                        or 0
                    ),
                    "pause_cut_seconds_total": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_cut_seconds_total", 0.0
                        )
                        or 0.0
                    ),
                    "pause_output_trim_delta_seconds": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_output_trim_delta_seconds", 0.0
                        )
                        or 0.0
                    ),
                    "pause_story_keep_reasons": list(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_story_keep_reasons", []
                        )
                    ),
                    "long_pause_cut_seconds_total": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "long_pause_cut_seconds_total", 0.0
                        )
                        or 0.0
                    ),
                    "story_sensitive_pause_kept_seconds_total": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "story_sensitive_pause_kept_seconds_total", 0.0
                        )
                        or 0.0
                    ),
                    "avg_pause_duration_before": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "avg_pause_duration_before", 0.0
                        )
                        or 0.0
                    ),
                    "avg_pause_duration_after": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "avg_pause_duration_after", 0.0
                        )
                        or 0.0
                    ),
                    "trimmed_silence_seconds": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "trimmed_silence_seconds", 0.0
                        )
                        or 0.0
                    ),
                    "silence_trim_events": list(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "silence_trim_events", []
                        )
                        or []
                    ),
                    "silence_type_counts": dict(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "silence_type_counts", {}
                        )
                        or {}
                    ),
                    "pacing_score": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pacing_score", 0.0
                        )
                        or 0.0
                    ),
                    "silent_parts_detected_count": int(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_cut_count", 0
                        )
                        or 0
                    ),
                    "silent_parts_removed_count": int(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_cut_segments_count", 0
                        )
                        or 0
                    ),
                    "silent_parts_removed_seconds_total": float(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_cut_seconds_total", 0.0
                        )
                        or 0.0
                    ),
                    "micro_gaps_kept_count": int(
                        (trim_silence_in_candidate_ms.last_stats or {}).get(
                            "pause_soft_keep_count", 0
                        )
                        or 0
                    ),
                    "subtitle_turn_retire_count": int(
                        (subtitle_signals or {}).get("subtitle_turn_retire_count", 0)
                        or 0
                    ),
                    "dialogue_gap_policy_ok": bool(
                        bool(
                            (trim_silence_in_candidate_ms.last_stats or {}).get(
                                "pause_policy_applied", False
                            )
                        )
                        and not bool(
                            (trim_silence_in_candidate_ms.last_stats or {}).get(
                                "pause_policy_failed", False
                            )
                        )
                    ),
                    "stitched_for_context_continuation": bool(
                        candidate.get("stitched_story_unit", False)
                        and (
                            candidate.get("story_continued_after_pause")
                            or boundary_meta.get("story_continuation_used")
                        )
                    ),
                    "needs_review": needs_review,
                    "acceptance_reason": acceptance_reason,
                    "rejection_reason": None,
                    "active_speaker_fallback_used": bool(
                        (not reframed)
                        or reframe_debug.get("center_safe_fallback_used", False)
                        or reframe_debug.get("face_preserving_fallback_used", False)
                        or str(reframe_debug.get("subject_acquisition_state", ""))
                        == "no_visible_subject"
                    ),
                    "subject_acquisition_state": str(
                        reframe_debug.get("subject_acquisition_state", "")
                    ),
                    "subject_acquisition_outcome": str(
                        reframe_debug.get(
                            "subject_acquisition_outcome",
                            reframe_debug.get("subject_acquisition_state", ""),
                        )
                    ),
                    "subject_acquisition_dense_scan_used": bool(
                        reframe_debug.get("subject_acquisition_dense_scan_used", False)
                    ),
                    "subject_acquisition_rescue_used": bool(
                        reframe_debug.get("subject_acquisition_rescue_used", False)
                    ),
                    "subject_acquisition_face_presence": float(
                        reframe_debug.get("subject_acquisition_face_presence", 0.0)
                        or 0.0
                    ),
                    "subject_acquisition_person_presence": float(
                        reframe_debug.get("subject_acquisition_person_presence", 0.0)
                        or 0.0
                    ),
                    "subject_acquisition_subject_presence": float(
                        reframe_debug.get("subject_acquisition_subject_presence", 0.0)
                        or 0.0
                    ),
                    "subject_acquisition_visible_faces_peak": int(
                        reframe_debug.get("subject_acquisition_visible_faces_peak", 0)
                        or 0
                    ),
                    "subject_acquisition_visible_persons_peak": int(
                        reframe_debug.get("subject_acquisition_visible_persons_peak", 0)
                        or 0
                    ),
                    "source_face_presence": source_face_presence,
                    "source_person_presence": source_person_presence,
                    "source_subject_presence": source_subject_presence,
                    "source_avg_face_size": source_avg_face_size,
                    "source_avg_center_x": source_avg_center_x,
                    "source_avg_center_y": source_avg_center_y,
                    "source_face_rich": source_face_rich,
                    "final_canvas_mode": final_canvas_mode,
                    "final_video_width": int(final_video_width),
                    "final_video_height": int(final_video_height),
                    "vertical_export_ok": bool(vertical_export_ok),
                    "geometry_rejection_reason": geometry_rejection_reason,
                    "square_reframe_mode_used": bool(
                        str(selected_framing_mode).lower() == "square_canvas"
                    ),
                    "center_safe_fallback_used": bool(
                        reframe_debug.get("center_safe_fallback_used", False)
                    ),
                    "center_safe_fallback_reason": str(
                        reframe_debug.get("center_safe_fallback_reason", "")
                    ),
                    "face_preserving_fallback_used": bool(
                        reframe_debug.get("face_preserving_fallback_used", False)
                    ),
                    "face_preserving_fallback_reason": str(
                        reframe_debug.get("face_preserving_fallback_reason", "")
                    ),
                    "face_safe_margin_applied": bool(
                        reframe_debug.get("face_safe_margin_applied", False)
                    ),
                    "strict_face_fallback_used": bool(
                        reframe_debug.get("strict_face_fallback_used", False)
                    ),
                    "remote_quality_fallback": self.cfg.get(
                        "remote_quality_fallback", "off"
                    ),
                    "remote_quality_provider": self.cfg.get(
                        "remote_quality_provider", ""
                    ),
                    "warnings": [
                        item
                        for item in report["warnings"]
                        if f"Candidate {index}" in item
                    ],
                })
                remote_quality_meta = {
                }
                meta.update(remote_quality_meta)
                story_debug_path = os.path.join(
                    out_dir, f"short_{index}_story_debug.json"
                )
                story_summary_path = os.path.join(
                    out_dir, f"story_summary_{index}.json"
                )
                story_chain_path = os.path.join(out_dir, f"story_chain_{index}.json")
                story_fragments_path = os.path.join(
                    out_dir, f"story_fragments_{index}.json"
                )
                story_debug = _story_debug_segments(subtitle_info, story_window_plan)
                story_debug.update(
                    {
                        "candidate_id": meta["candidate_id"],
                        "candidate_rank": candidate_rank,
                        "final_duration": float(final_duration),
                        "story_thread_id": meta["story_thread_id"],
                        "story_arc_shape": meta["story_arc_shape"],
                        "story_completion_score": float(meta["story_completion_score"]),
                        "context_completeness_score": float(
                            meta["context_completeness_score"]
                        ),
                        "hook_type": meta["hook_type"],
                        "payoff_type": meta["payoff_type"],
                        "topic_shift_events": int(meta["topic_shift_events"]),
                        "story_deficient": bool(
                            story_debug.get("story_deficient", False)
                        ),
                        "story_summary": story_assets["story_summary"],
                        "story_chain": story_assets["story_chain"],
                        "story_fragments": story_assets["story_fragments"],
                        "rejected_for_missing_payoff": bool(
                            meta.get("rejected_for_missing_payoff", False)
                        ),
                        "rejected_for_topic_jump": bool(
                            meta.get("rejected_for_topic_jump", False)
                        ),
                        "rejected_for_confusing_story": bool(
                            meta.get("rejected_for_confusing_story", False)
                        ),
                        "subtitle_rewrite_applied": bool(
                            meta.get("subtitle_rewrite_applied", False)
                        ),
                        "subtitle_remap_used": bool(
                            meta.get("subtitle_remap_used", False)
                        ),
                        "subtitle_quality_score": float(
                            meta.get("subtitle_quality_score", 0.0) or 0.0
                        ),
                    }
                )
                _dump_json(story_debug_path, story_debug)
                _dump_json(story_summary_path, story_assets["story_summary"])
                _dump_json(story_chain_path, story_assets["story_chain"])
                _dump_json(story_fragments_path, story_assets["story_fragments"])
                meta["story_debug_path"] = story_debug_path
                meta["story_summary_path"] = story_summary_path
                meta["story_chain_path"] = story_chain_path
                meta["story_fragments_path"] = story_fragments_path
                report["stats"]["silent_parts_removed_total"] = int(
                    report["stats"].get("silent_parts_removed_total", 0) or 0
                ) + int(meta.get("silent_parts_removed_count", 0) or 0)
                if bool(meta.get("pause_policy_failed", False)):
                    report["stats"]["pause_policy_failed_outputs"] = (
                        int(report["stats"].get("pause_policy_failed_outputs", 0) or 0)
                        + 1
                    )
                report["stats"]["speaker_center_offset_total"] = float(
                    report["stats"].get("speaker_center_offset_total", 0.0) or 0.0
                ) + float(meta.get("speaker_center_offset_avg", 0.0) or 0.0)
                report["stats"]["speaker_center_offset_p95_total"] = float(
                    report["stats"].get("speaker_center_offset_p95_total", 0.0) or 0.0
                ) + float(meta.get("speaker_center_offset_p95", 0.0) or 0.0)
                report["stats"]["speaker_face_centered_windows_total"] = int(
                    report["stats"].get("speaker_face_centered_windows_total", 0) or 0
                ) + int(meta.get("speaker_face_centered_windows", 0) or 0)
                report["stats"]["dialogue_center_windows_total"] = int(
                    report["stats"].get("dialogue_center_windows_total", 0) or 0
                ) + int(meta.get("dialogue_center_windows", 0) or 0)
                report["stats"]["listener_fallback_windows_total"] = int(
                    report["stats"].get("listener_fallback_windows_total", 0) or 0
                ) + int(meta.get("listener_fallback_windows", 0) or 0)
                report["stats"]["subject_person_fallback_windows_total"] = int(
                    report["stats"].get("subject_person_fallback_windows_total", 0) or 0
                ) + int(meta.get("subject_person_fallback_windows", 0) or 0)
                if bool(self.cfg.get("title_generation_enabled", True)):
                    _emit(
                        progress_callback,
                        "titling",
                        f"Generating title for short {index}",
                    )
                    stage_start = _now()
                    title_subtitle_info = subtitle_info
                    if subtitle_seed_rejected_for_title:
                        title_subtitle_info = dict(subtitle_info or {})
                        title_summary = dict(title_subtitle_info.get("summary") or {})
                        title_context_hint = _clean_text(
                            meta.get("premise_summary")
                            or meta.get("selected_opening_reason")
                            or title_summary.get("summary_text")
                            or meta.get("story_unit_type")
                            or ""
                        )
                        title_summary["summary_text"] = (
                            title_context_hint
                            or title_summary.get("summary_text")
                            or ""
                        )
                        title_summary["context_hint"] = title_context_hint
                        title_summary["keywords"] = list(
                            title_summary.get("keywords") or []
                        )
                        title_subtitle_info["summary"] = title_summary
                        meta["title_context_hint"] = title_context_hint
                    title_payload = generate_context_title(
                        title_subtitle_info, meta, self.cfg
                    )
                    report["stage_timings"][f"candidate_{index}_titling_seconds"] = (
                        round(_now() - stage_start, 3)
                    )
                    renamed_output = maybe_rename_output(
                        final_path, index, title_payload
                    )
                    meta["generated_title"] = title_payload.get("title")
                    meta["hook_line"] = title_payload.get("hook_line")
                    meta["title_variant_a"] = title_payload.get("title_variant_a")
                    meta["title_variant_b"] = title_payload.get("title_variant_b")
                    meta["description_seed"] = title_payload.get("description_seed")
                    meta["keyword_cluster"] = title_payload.get("keyword_cluster", [])
                    meta["series_mood"] = title_payload.get("series_mood")
                    meta["retention_soft_score"] = title_payload.get(
                        "retention_soft_score",
                        title_payload.get("viral_soft_score", 0.0),
                    )
                    meta["viral_soft_score"] = title_payload.get(
                        "viral_soft_score",
                        title_payload.get("retention_soft_score", 0.0),
                    )
                    meta["packaging_quality_score"] = max(
                        float(meta.get("packaging_quality_score", 0.0) or 0.0),
                        float(title_payload.get("packaging_quality_score", 0.0) or 0.0),
                    )
                    meta["generated_hashtags"] = title_payload.get("hashtags", [])
                    meta["generated_emojis"] = title_payload.get("emojis", [])
                    meta["title_style"] = title_payload.get(
                        "style", self.cfg.get("title_style", "context_clean")
                    )
                    meta["title_generation_confidence"] = title_payload.get(
                        "confidence", 0.0
                    )
                    meta["title_quality_score"] = title_payload.get(
                        "title_quality_score", 0.0
                    )
                    meta["renamed_output_path"] = renamed_output
                    meta["title_generation_mood"] = title_payload.get("mood")
                    meta["title_generation_keywords"] = title_payload.get(
                        "keywords", []
                    )
                    meta["title_encoding_ok"] = True
                    meta["title_sanitization_applied"] = bool(
                        title_payload.get("title_cleanup_applied", False)
                        or os.path.basename(renamed_output)
                        != os.path.basename(final_path)
                    )
                    final_path = renamed_output
                    report["stats"]["titles_generated"] += 1
                    if meta["generated_hashtags"]:
                        report["stats"]["titles_with_hashtags"] += 1
                    else:
                        report["stats"]["titles_without_hashtags"] += 1
                    if meta["generated_emojis"]:
                        report["stats"]["titles_with_emojis"] += 1
                    if not meta["generated_title"]:
                        report["stats"]["title_fallbacks"] += 1
                else:
                    meta["generated_title"] = None
                    meta["hook_line"] = None
                    meta["title_variant_a"] = None
                    meta["title_variant_b"] = None
                    meta["description_seed"] = None
                    meta["keyword_cluster"] = []
                    meta["series_mood"] = None
                    meta["retention_soft_score"] = 0.0
                    meta["viral_soft_score"] = 0.0
                    meta["generated_hashtags"] = []
                    meta["generated_emojis"] = []
                    meta["title_style"] = self.cfg.get("title_style", "context_clean")
                    meta["title_generation_confidence"] = 0.0
                    meta["title_quality_score"] = 0.0
                    meta["renamed_output_path"] = final_path
                    meta["title_encoding_ok"] = True
                    meta["title_sanitization_applied"] = False
                meta_path = os.path.join(out_dir, f"short_{index}.json")
                _dump_json(meta_path, meta)
                report["generated_outputs"].append(
                    {"video": final_path, "metadata": meta_path}
                )
                _emit(progress_callback, "done", f"Generated {final_path}")

            report["status"] = "done" if report["generated_outputs"] else "failed"
            if (
                report["generated_outputs"]
                and len(report["generated_outputs"]) < report["requested_max"]
            ):
                reasons = {}
                for item in report["rejected_candidates"]:
                    reason = item.get("reason", "unknown")
                    reasons[reason] = reasons.get(reason, 0) + 1
                if reasons:
                    summary = ", ".join(
                        f"{key}={value}" for key, value in sorted(reasons.items())
                    )
                    report["warnings"].append(
                        f"Generated {len(report['generated_outputs'])}/{report['requested_max']} publishable shorts; rejected candidates: {summary}"
                    )
            if (
                not report["generated_outputs"]
                and "No valid story candidates found" not in report["warnings"]
            ):
                report["stats"]["main_rejection_reason"] = (
                    report["stats"].get("main_rejection_reason")
                    or "no_publishable_outputs"
                )
                report["warnings"].append(
                    "Episode finished without publishable outputs"
                )
            report["stats"]["slow_stage_events"] = int(
                self._watchdog_stats.get("slow_stage_events", 0)
            )
            report["stats"]["hard_timeouts"] = int(
                self._watchdog_stats.get("hard_timeouts", 0)
            )
            report["stats"]["deferred_candidates"] = int(
                self._watchdog_stats.get("deferred_candidates", 0)
            )
            report["stats"]["skipped_due_to_timeout"] = int(
                self._watchdog_stats.get("skipped_due_to_timeout", 0)
            )
            report["stats"]["watchdog_fallback_used"] = int(
                self._watchdog_stats.get("watchdog_fallback_used", 0)
            )
            numeric_timings = [
                value
                for value in report["stage_timings"].values()
                if isinstance(value, (int, float))
            ]
            report["stage_timings"]["median_stage_seconds"] = _median_or_zero(
                numeric_timings
            )
            report["runtime_seconds"] = round(_now() - started, 3)
            report["gui_summary"] = {
                "status": report["status"],
                "requested_max": report["requested_max"],
                "outputs": len(report["generated_outputs"]),
                "test_mode_enabled": test_mode_enabled,
                "test_candidate_rank": test_candidate_rank
                if test_mode_enabled
                else None,
                "main_rejection_reason": report["stats"].get("main_rejection_reason"),
                "total_windows": report["stats"].get("total_windows", 0),
                "total_story_candidates": report["stats"].get(
                    "total_story_candidates", 0
                ),
                "publishable_candidates": report["stats"].get(
                    "publishable_candidates", 0
                ),
                "publishable_pool_before_final_visual_gate": report["stats"].get(
                    "publishable_pool_before_final_visual_gate", 0
                ),
                "story_override_candidates": report["stats"].get(
                    "story_override_candidates", 0
                ),
                "review_pass_used": report["stats"].get("review_pass_used", False),
                "review_pass_considered": report["stats"].get(
                    "review_pass_considered", False
                ),
                "review_pass_candidates": report["stats"].get(
                    "review_pass_candidates", 0
                ),
                "review_pass_stitched_candidates": report["stats"].get(
                    "review_pass_stitched_candidates", 0
                ),
                "review_pass_rescued_outputs": report["stats"].get(
                    "review_pass_rescued_outputs", 0
                ),
                "main_rejection_bucket": report["stats"].get("main_rejection_bucket"),
                "selection_starvation_reasons": report["stats"].get(
                    "selection_starvation_reasons", {}
                ),
                "selection_starvation_visual": report["stats"].get(
                    "selection_starvation_visual", 0
                ),
                "selection_starvation_subtitle": report["stats"].get(
                    "selection_starvation_subtitle", 0
                ),
                "selection_starvation_boundary": report["stats"].get(
                    "selection_starvation_boundary", 0
                ),
                "selection_starvation_vad": report["stats"].get(
                    "selection_starvation_vad", 0
                ),
                "avg_speaker_center_offset": round(
                    float(
                        report["stats"].get("speaker_center_offset_total", 0.0) or 0.0
                    )
                    / max(1, len(report["generated_outputs"])),
                    3,
                ),
                "avg_speaker_center_offset_p95": round(
                    float(
                        report["stats"].get("speaker_center_offset_p95_total", 0.0)
                        or 0.0
                    )
                    / max(1, len(report["generated_outputs"])),
                    3,
                ),
                "avg_speaker_face_centered_windows": round(
                    float(
                        report["stats"].get("speaker_face_centered_windows_total", 0)
                        or 0
                    )
                    / max(1, len(report["generated_outputs"])),
                    3,
                ),
                "avg_dialogue_center_windows": round(
                    float(report["stats"].get("dialogue_center_windows_total", 0) or 0)
                    / max(1, len(report["generated_outputs"])),
                    3,
                ),
                "avg_listener_fallback_windows": round(
                    float(
                        report["stats"].get("listener_fallback_windows_total", 0) or 0
                    )
                    / max(1, len(report["generated_outputs"])),
                    3,
                ),
                "avg_subject_person_fallback_windows": round(
                    float(
                        report["stats"].get("subject_person_fallback_windows_total", 0)
                        or 0
                    )
                    / max(1, len(report["generated_outputs"])),
                    3,
                ),
                "ranking_timeouts": report["stats"].get("ranking_timeouts", 0),
                "ranking_fallback_used": report["stats"].get(
                    "ranking_fallback_used", 0
                ),
                "ranking_failed": report["stats"].get("ranking_failed", 0),
                "semantic_preview_timeouts": report["stats"].get(
                    "semantic_preview_timeouts", 0
                ),
                "semantic_preview_fallback_used": report["stats"].get(
                    "semantic_preview_fallback_used", 0
                ),
                "slow_stage_events": report["stats"].get("slow_stage_events", 0),
                "hard_timeouts": report["stats"].get("hard_timeouts", 0),
                "deferred_candidates": report["stats"].get("deferred_candidates", 0),
                "skipped_due_to_timeout": report["stats"].get(
                    "skipped_due_to_timeout", 0
                ),
                "watchdog_fallback_used": report["stats"].get(
                    "watchdog_fallback_used", 0
                ),
                "final_visual_rejects": report["stats"].get("final_visual_rejects", 0),
                "silent_parts_removed_total": report["stats"].get(
                    "silent_parts_removed_total", 0
                ),
                "pause_policy_failed_outputs": report["stats"].get(
                    "pause_policy_failed_outputs", 0
                ),
                "square_reframe_mode_outputs": sum(
                    1
                    for item in report["generated_outputs"]
                    if bool(
                        json.loads(
                            Path(item["metadata"]).read_text(encoding="utf-8")
                        ).get("square_reframe_mode_used", False)
                    )
                )
                if report["generated_outputs"]
                else 0,
                "end_boundary_completion_ok_outputs": sum(
                    1
                    for item in report["generated_outputs"]
                    if bool(
                        json.loads(
                            Path(item["metadata"]).read_text(encoding="utf-8")
                        ).get("end_boundary_completion_ok", False)
                    )
                )
                if report["generated_outputs"]
                else 0,
                "incomplete_phrase_end_outputs": sum(
                    1
                    for item in report["generated_outputs"]
                    if int(
                        json.loads(
                            Path(item["metadata"]).read_text(encoding="utf-8")
                        ).get("incomplete_phrase_end_count", 0)
                        or 0
                    )
                    > 0
                )
                if report["generated_outputs"]
                else 0,
                "titles_generated": report["stats"].get("titles_generated", 0),
                "interestingness_avg": round(
                    sum(
                        load.get("interestingness_score", 0.0)
                        for load in [
                            json.loads(
                                Path(item["metadata"]).read_text(encoding="utf-8")
                            )
                            for item in report["generated_outputs"]
                            if os.path.exists(item["metadata"])
                        ]
                    )
                    / max(
                        1,
                        len(
                            [
                                item
                                for item in report["generated_outputs"]
                                if os.path.exists(item["metadata"])
                            ]
                        ),
                    ),
                    4,
                )
                if report["generated_outputs"]
                else None,
                "warnings_count": len(report["warnings"]),
                "median_stage_seconds": report["stage_timings"].get(
                    "median_stage_seconds"
                ),
            }
            reject_report = _summarize_reject_paths(report)
            report["reject_report"] = reject_report
            _dump_json(os.path.join(out_dir, "reject_report.json"), reject_report)
            _dump_json(os.path.join(out_dir, "episode_report.json"), report)
            return report
        except Exception as exc:
            report["status"] = "failed"
            report["warnings"].append(str(exc))
            report["stats"]["main_rejection_reason"] = (
                report["stats"].get("main_rejection_reason") or "exception"
            )
            report["runtime_seconds"] = (
                round(_now() - started, 3) if "started" in locals() else None
            )
            _dump_json(os.path.join(out_dir, "episode_report.json"), report)
            _emit(progress_callback, "failed", str(exc))
            return report


def create_shorts_from_video(video_path, out_dir, cfg):
    pipe = Pipeline(cfg or {})
    pipe.cfg["output_root"] = str(Path(out_dir).resolve().parent)
    report = pipe.process_episode(video_path, progress_callback=None)
    return [item["video"] for item in report.get("generated_outputs", [])]

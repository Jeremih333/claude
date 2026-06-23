from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

try:
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import ContentDetector
except Exception:
    SceneManager = None
    ContentDetector = None
    open_video = None


_CACHE_PATH = Path(tempfile.gettempdir()) / "shorts_factory_scene_detect_cache.json"


def _load_cache() -> dict:
    try:
        if _CACHE_PATH.exists():
            payload = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _cache_key(path: str, threshold: float, min_scene_len: float) -> str:
    try:
        stat = Path(path).stat()
        return f"{Path(path).resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{threshold:.3f}|{min_scene_len:.3f}"
    except Exception:
        return f"{Path(path).resolve()}|0|0|{threshold:.3f}|{min_scene_len:.3f}"


def _probe_duration(path: str) -> float:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return max(0.0, float(proc.stdout.strip().splitlines()[0]))
    except Exception:
        pass
    return 0.0


def detect_scenes(path: str, threshold: float = 27.0, min_scene_len: float = 1.5):
    """Return list of (start_sec, end_sec) scene boundaries, never an empty list."""
    cache_key = _cache_key(path, threshold, min_scene_len)
    cache = _load_cache()
    cached = cache.get(cache_key)
    if isinstance(cached, list) and cached:
        try:
            return [(float(item[0]), None if item[1] is None else float(item[1])) for item in cached]
        except Exception:
            pass

    duration = _probe_duration(path)
    if duration <= 0:
        return [(0.0, None)]
    if SceneManager is None or ContentDetector is None or open_video is None:
        return [(0.0, duration)]
    try:
        video = open_video(path)
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=int(min_scene_len * 24)))
        manager.detect_scenes(video=video)
        scenes = manager.get_scene_list()
        result = []
        for start_tc, end_tc in scenes:
            start = start_tc.get_seconds()
            end = end_tc.get_seconds()
            if end > start:
                result.append((start, end))
        final = result or [(0.0, duration)]
        cache[cache_key] = [[float(start), None if end is None else float(end)] for start, end in final]
        _save_cache(cache)
        return final
    except Exception:
        return [(0.0, duration)]

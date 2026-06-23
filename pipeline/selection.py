"""
selection.py
Functions to pick "interesting" segments from a video using lightweight, CPU-friendly metrics:
- frame-difference motion
- audio-energy (proxy for speech/activity)
- simple scene-change detection via histogram differences
This is designed to work on CPU-only machines and to be optional (no heavy ML required).
"""
from moviepy import VideoFileClip
import numpy as np
import math, os, tempfile

def _frame_brightness(frame):
    # frame is RGB numpy array
    return np.mean(frame.astype(np.float32), axis=2)

def _hist_diff(a, b, bins=16):
    ha, _ = np.histogram(a.flatten(), bins=bins, range=(0,255), density=True)
    hb, _ = np.histogram(b.flatten(), bins=bins, range=(0,255), density=True)
    return np.sum(np.abs(ha - hb))

def score_segments(video_path, cfg):
    """
    Returns list of candidate segments: [{'start':, 'end':, 'score':}, ...]
    cfg may contain: sample_rate, window_sec, motion_weight, audio_weight, scene_weight, max_shorts
    """
    cfg = dict(cfg or {})
    sample_rate = cfg.get('sample_rate', 1.0)  # frames per second to sample
    window_sec = cfg.get('window_sec', 3.0)    # window size to consider a segment
    motion_weight = cfg.get('motion_weight', 1.0)
    audio_weight = cfg.get('audio_weight', 1.0)
    scene_weight = cfg.get('scene_weight', 0.5)
    max_shorts = cfg.get('max_shorts', 50)

    clip = VideoFileClip(video_path)
    duration = clip.duration
    # compute audio energy per small frame (window_sec)
    audio_energies = []
    times = []
    step = window_sec
    t = 0.0
    while t < duration:
        start = t
        end = min(duration, t + window_sec)
        # sample audio frames (may be empty)
        try:
            audio = clip.audio.subclip(start, end).to_soundarray(fps=8000)
            if audio.size == 0:
                energy = 0.0
            else:
                energy = float(np.mean(np.square(audio)))
        except Exception:
            energy = 0.0
        audio_energies.append(energy)
        times.append((start, end))
        t += step

    # compute motion/scene scores by sampling mid-frames of windows
    motion_scores = []
    scene_scores = []
    for (start, end) in times:
        mid = min(duration, (start + end) / 2.0)
        try:
            frame = clip.get_frame(mid)
            frame = frame.astype(np.uint8)
        except Exception:
            frame = None
        if frame is None:
            motion_scores.append(0.0)
            scene_scores.append(0.0)
            continue
        # sample previous frame a bit earlier to compute difference
        prev_t = max(0, mid - max(0.5, step/2.0))
        try:
            prev = clip.get_frame(prev_t).astype(np.uint8)
        except Exception:
            prev = frame
        # motion via mean absolute difference
        mad = float(np.mean(np.abs(frame.astype(np.float32) - prev.astype(np.float32))))
        motion_scores.append(mad)
        # scene change via histogram difference between frames
        histd = _hist_diff(frame, prev)
        scene_scores.append(histd)

    # normalize
    def _norm(arr):
        arr = np.array(arr, dtype=np.float32)
        mx = arr.max() if arr.size>0 else 1.0
        mn = arr.min() if arr.size>0 else 0.0
        rng = mx - mn if (mx-mn)>1e-6 else 1.0
        return (arr - mn) / rng

    m_norm = _norm(motion_scores)
    a_norm = _norm(audio_energies)
    s_norm = _norm(scene_scores)

    candidates = []
    for i, (start,end) in enumerate(times):
        score = motion_weight * float(m_norm[i]) + audio_weight * float(a_norm[i]) + scene_weight * float(s_norm[i])
        candidates.append({'start': start, 'end': end, 'score': float(score), 'motion': float(m_norm[i]), 'audio': float(a_norm[i]), 'scene': float(s_norm[i])})

    # sort and pick top non-overlapping segments (greedy)
    candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)
    picked = []
    occupied = []
    for c in candidates:
        if len(picked) >= max_shorts:
            break
        # check overlap with picked (if overlap >50% skip)
        overlap = False
        for p in picked:
            inter = max(0, min(p['end'], c['end']) - max(p['start'], c['start']))
            union = max(p['end'], c['end']) - min(p['start'], c['start'])
            if union>0 and (inter/union) > 0.4:
                overlap = True
                break
        if not overlap:
            picked.append(c)
    # sort picked by time
    picked = sorted(picked, key=lambda x: x['start'])
    try:
        clip.close()
    except Exception:
        pass
    return picked

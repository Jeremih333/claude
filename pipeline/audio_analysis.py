"""
pipeline/audio_analysis.py

Audio utility functions used by the ShortsFactory pipeline.

Provides:
- extract_audio_to_wav(video_path, out_wav)
- compute_rms(wav_path)
- speech_density(wav_path, ...)  -- uses webrtcvad if available, otherwise RMS-based fallback
- detect_silence_ffmpeg(wav_path, silence_thresh_db=-40.0, min_silence_len=0.5)

Notes:
- Functions are defensive: they handle missing dependencies and return safe defaults.
- Recommended WAV format: mono, 16000 Hz (the extractor does that by default).
"""

import os
import wave
import contextlib
import math
import subprocess
import tempfile
import warnings
from typing import List, Tuple

# Optional imports
try:
    warnings.filterwarnings(
        "ignore",
        message="pkg_resources is deprecated as an API.*",
        category=UserWarning,
        module="webrtcvad",
    )
    import webrtcvad
    _HAS_WEBRTC_VAD = True
except Exception:
    webrtcvad = None
    _HAS_WEBRTC_VAD = False

try:
    import numpy as np
except Exception:
    np = None

# pydub for simple RMS windows
try:
    from pydub import AudioSegment
    _HAS_PYDUB = True
except Exception:
    AudioSegment = None
    _HAS_PYDUB = False


def _run_cmd(cmd: list, timeout: int = 120) -> Tuple[int, str, str]:
    """
    Run command list (no shell), return (returncode, stdout, stderr).
    """
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=timeout)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def extract_audio_to_wav(video_path: str, out_wav: str) -> str:
    """
    Extract audio from video to WAV (mono, 16k) using ffmpeg.
    Returns path to WAV (out_wav) or raises if ffmpeg not available.
    """
    out_dir = os.path.dirname(out_wav)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ac", "1",
        "-ar", "16000",
        "-vn",
        out_wav,
        "-hide_banner",
        "-loglevel", "error"
    ]
    rc, out, err = _run_cmd(cmd, timeout=180)
    if rc != 0:
        # If extraction failed, raise descriptive error
        raise RuntimeError(f"ffmpeg failed to extract audio: {err.strip()[:200]}")
    return out_wav


def compute_rms(wav_path: str) -> List[float]:
    """
    Compute RMS per 1-second window for the given WAV file.
    Returns list of RMS values (integers/floats). If failed, returns empty list.
    """
    if not _HAS_PYDUB:
        # fallback: try using wave + numpy if available
        try:
            with contextlib.closing(wave.open(wav_path, 'rb')) as wf:
                sr = wf.getframerate()
                frames = wf.getnframes()
                duration = frames / float(sr)
                # read whole audio
                wf.rewind()
                raw = wf.readframes(frames)
                if np is None:
                    return []
                audio = np.frombuffer(raw, dtype='<i2')  # assume 16-bit PCM
                # mono or stereo?
                nch = wf.getnchannels()
                if nch > 1:
                    audio = audio.reshape(-1, nch).mean(axis=1)
                samples_per_win = int(sr)
                rms_list = []
                for i in range(0, len(audio), samples_per_win):
                    win = audio[i:i+samples_per_win]
                    if len(win) == 0:
                        continue
                    rms_val = float(np.sqrt((win.astype('float64')**2).mean()))
                    rms_list.append(rms_val)
                return rms_list
        except Exception:
            return []
    # pydub path (preferred)
    try:
        audio = AudioSegment.from_wav(wav_path)
    except Exception:
        return []
    dur_s = math.ceil(len(audio) / 1000.0)
    rms = []
    for i in range(int(dur_s)):
        seg = audio[i*1000:(i+1)*1000]
        try:
            rms.append(float(seg.rms))
        except Exception:
            rms.append(0.0)
    return rms


def speech_density(wav_path: str, frame_ms: int = 30, aggr_window_s: int = 3, vad_mode: int = 2) -> float:
    """
    Estimate fraction of seconds that contain speech in the wav file.

    Strategy:
    - If webrtcvad is available, use it for a more accurate voiced/unvoiced decision.
    - Otherwise, fall back to RMS energy heuristic (compute_rms).

    Returns a float in [0.0, 1.0] representing fraction of 1-second windows that are voiced.
    """
    # Prefer webrtcvad if available and file is 16k mono
    if _HAS_WEBRTC_VAD:
        try:
            # load wave and check format
            with contextlib.closing(wave.open(wav_path, 'rb')) as wf:
                sr = wf.getframerate()
                nch = wf.getnchannels()
                sampwidth = wf.getsampwidth()
            if sr not in (8000, 16000, 32000, 48000) or nch != 1 or sampwidth != 2:
                # webrtcvad expects specific formats; if not matching, rely on RMS fallback
                raise RuntimeError("WAV not in required format for WebRTC VAD")
            vad = webrtcvad.Vad(vad_mode)
            # Read audio as bytes in short frames of frame_ms
            voiced_frames = 0
            total_frames = 0
            frame_bytes = int(sr * (frame_ms / 1000.0) * 2)  # 2 bytes per sample for 16-bit
            with open(wav_path, 'rb') as fh:
                # skip WAV header using wave module to get raw frames
                with contextlib.closing(wave.open(wav_path, 'rb')) as wf:
                    raw = wf.readframes(wf.getnframes())
                # iterate by frame_bytes
                for i in range(0, len(raw), frame_bytes):
                    chunk = raw[i:i+frame_bytes]
                    if len(chunk) < frame_bytes:
                        break
                    try:
                        is_speech = vad.is_speech(chunk, sr)
                    except Exception:
                        is_speech = False
                    if is_speech:
                        voiced_frames += 1
                    total_frames += 1
            if total_frames == 0:
                return 0.0
            # convert frame ratio to per-second approximate by considering how many frames per second
            frames_per_sec = 1000.0 / frame_ms
            # voiced_frames / total_frames approximates speech fraction of audio duration
            density = float(voiced_frames) / float(total_frames)
            # clamp
            return max(0.0, min(1.0, density))
        except Exception:
            # fallback to RMS method below
            pass

    # RMS fallback: compute per-second RMS and threshold
    rms = compute_rms(wav_path)
    if not rms:
        return 0.0
    # robust threshold: use median * factor or absolute floor
    try:
        import statistics
        med = statistics.median(rms) if len(rms) > 0 else 0.0
    except Exception:
        med = (sum(rms)/len(rms)) if len(rms) else 0.0
    thresh = max(400.0, med * 1.2)  # heuristic threshold; tuneable
    voiced = sum(1 for r in rms if r > thresh)
    return float(voiced) / float(len(rms))


def detect_silence_ffmpeg(wav_path: str, silence_thresh_db: float = -40.0, min_silence_len: float = 0.5) -> List[Tuple[float, float]]:
    """
    Use ffmpeg silencedetect to list silent intervals in WAV (seconds).
    Returns list of (start_sec, end_sec). Returns [] on failure / no silences.
    """
    if not os.path.exists(wav_path):
        return []

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", wav_path,
        "-af", f"silencedetect=noise={silence_thresh_db}dB:d={min_silence_len}",
        "-f", "null", "-"
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _, err = proc.communicate(timeout=60)
    except Exception:
        return []

    silences = []
    open_start = None
    for line in (err or "").splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                t = float(line.split("silence_start:")[1].strip())
                open_start = t
            except Exception:
                open_start = None
        elif "silence_end:" in line:
            try:
                after = line.split("silence_end:")[1].strip()
                # first token is end time
                end_str = after.split()[0]
                t = float(end_str)
                if open_start is None:
                    silences.append((0.0, t))
                else:
                    silences.append((open_start, t))
                    open_start = None
            except Exception:
                # ignore parse error
                pass

    # if there's an open silence, try to close it using wav duration
    if open_start is not None:
        try:
            with contextlib.closing(wave.open(wav_path, 'rb')) as wf:
                sr = wf.getframerate()
                total = wf.getnframes() / float(sr)
                silences.append((open_start, total))
        except Exception:
            pass

    # normalize/clamp values
    clean = []
    for s, e in silences:
        try:
            ss = max(0.0, float(s))
            ee = max(ss, float(e))
            clean.append((ss, ee))
        except Exception:
            continue
    return clean

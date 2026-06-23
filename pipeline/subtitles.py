"""
subtitles.py
Lightweight subtitles generation wrapper. Tries to use faster_whisper if available for local CPU operation.
If no ASR library is present, produces simple timestamp placeholders.
"""
from pathlib import Path
import os, tempfile, subprocess, json

def generate_subtitles_for_clip(clip_path, cfg=None):
    """
    Returns list of subtitle dicts: [{'start':, 'end':, 'text':}, ...]
    Tries faster_whisper -> vosk -> fallback
    """
    cfg = dict(cfg or {})
    # Try faster_whisper
    try:
        from faster_whisper import WhisperModel
        model_size = cfg.get('whisper_model','tiny')  # tiny/base/small/etc
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(clip_path, beam_size=5)
        subs = []
        for seg in segments:
            subs.append({'start': seg.start, 'end': seg.end, 'text': seg.text})
        return subs
    except Exception:
        pass
    # Try Vosk
    try:
        from vosk import Model, KaldiRecognizer
        import wave
        wf = wave.open(clip_path, "rb")
        model = Model(lang="en-us")
        rec = KaldiRecognizer(model, wf.getframerate())
        subs = []
        buf = ""
        while True:
            data = wf.readframes(4000)
            if len(data)==0:
                break
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                if 'text' in res and res['text'].strip():
                    subs.append({'start': 0.0, 'end': 0.0, 'text': res['text']})
        # final partial
        final = json.loads(rec.FinalResult())
        if 'text' in final and final['text'].strip():
            subs.append({'start': 0.0, 'end': 0.0, 'text': final['text']})
        return subs
    except Exception:
        pass
    # Fallback: no ASR available: produce placeholder segments by duration
    import moviepy.editor as mpy
    clip = mpy.AudioFileClip(clip_path) if clip_path.lower().endswith((".mp3",".wav")) else None
    duration = 0.0
    try:
        if clip is not None:
            duration = clip.duration
            clip.close()
    except Exception:
        duration = 0.0
    if duration <= 0:
        # try probing via ffprobe
        return [{'start':0.0, 'end':0.0, 'text': '[no-subtitles-available]'}]
    # chunk into 5s placeholders
    subs = []
    t = 0.0
    chunk = 5.0
    while t < duration:
        subs.append({'start': t, 'end': min(duration, t+chunk), 'text': ''})
        t += chunk
    return subs

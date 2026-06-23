"""
Test Whisper transcription with proxy disabled.
"""

import os

# Disable proxy for this session
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'
if 'HTTP_PROXY' in os.environ:
    del os.environ['HTTP_PROXY']
if 'HTTPS_PROXY' in os.environ:
    del os.environ['HTTPS_PROXY']
if 'http_proxy' in os.environ:
    del os.environ['http_proxy']
if 'https_proxy' in os.environ:
    del os.environ['https_proxy']

# Force offline mode for HuggingFace
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

print("Proxy disabled, HF offline mode enabled")
print()

from faster_whisper import WhisperModel

wav_path = "_debug_transcription/cand_0.wav"

if not os.path.exists(wav_path):
    print(f"WAV not found: {wav_path}")
    exit(1)

print("Loading Whisper model (offline mode)...")
try:
    model = WhisperModel("base", device="cpu", compute_type="int8", local_files_only=True)
    print("✓ Model loaded successfully!\n")
except Exception as e:
    print(f"❌ Failed to load model: {e}")
    exit(1)

print("Transcribing with VAD...")
segments, info = model.transcribe(
    wav_path,
    language="ru",
    beam_size=5,
    vad_filter=True,
    condition_on_previous_text=True,
    word_timestamps=True,
)

segments_list = list(segments)
print(f"Segments: {len(segments_list)}")

if segments_list:
    print("\nFirst 5 segments:")
    for i, seg in enumerate(segments_list[:5]):
        print(f"  [{i}] {seg.start:.2f}-{seg.end:.2f}: {seg.text}")
else:
    print("❌ NO SEGMENTS with VAD")
    
    # Try without VAD
    print("\nRetrying without VAD...")
    segments2, info2 = model.transcribe(
        wav_path,
        language="ru",
        beam_size=5,
        vad_filter=False,
        word_timestamps=True,
    )
    
    segments_list2 = list(segments2)
    print(f"Segments without VAD: {len(segments_list2)}")
    
    if segments_list2:
        print("\nFirst 5 segments:")
        for i, seg in enumerate(segments_list2[:5]):
            print(f"  [{i}] {seg.start:.2f}-{seg.end:.2f}: {seg.text}")
        
        print("\n🔴 CONFIRMED: VAD filter is blocking all speech")
        print("   Solution: Disable VAD in transcription config")
    else:
        print("\n🔴 Even without VAD, no segments found")

"""
Deep transcription debugging script.

Goal: Find the exact line where segments disappear.
Compare candidate-level vs episode-level transcription.
"""

import json
import os
import sys
from pathlib import Path

# Monkey-patch transcribe_segment to add detailed logging
original_transcribe = None

def debug_transcribe_segment(wav_path: str, out_dir: str, idx: int, cfg=None):
    """Wrapped version with detailed logging."""
    print("\n" + "="*80)
    print(f"TRANSCRIBE_SEGMENT CALLED (idx={idx})")
    print("="*80)
    
    # Log input parameters
    print(f"WAV Path: {wav_path}")
    print(f"WAV Exists: {os.path.exists(wav_path)}")
    
    if os.path.exists(wav_path):
        wav_size = os.path.getsize(wav_path)
        print(f"WAV Size: {wav_size:,} bytes ({wav_size/1024/1024:.2f} MB)")
        
        # Check WAV properties
        try:
            import wave
            with wave.open(wav_path, 'rb') as wf:
                print(f"Sample Rate: {wf.getframerate()} Hz")
                print(f"Channels: {wf.getnchannels()}")
                print(f"Sample Width: {wf.getsampwidth()} bytes")
                print(f"Frames: {wf.getnframes():,}")
                duration = wf.getnframes() / wf.getframerate()
                print(f"Duration: {duration:.2f} seconds")
        except Exception as e:
            print(f"WAV Analysis Error: {e}")
    
    print(f"Output Dir: {out_dir}")
    print(f"Config Language: {cfg.get('subtitle_language', 'auto') if cfg else 'auto'}")
    print(f"Config Profile: {cfg.get('transcription_profile', 'balanced') if cfg else 'balanced'}")
    
    # Call original function
    print("\nCALLING ORIGINAL transcribe_segment()...")
    result = original_transcribe(wav_path, out_dir, idx, cfg)
    
    # Log output
    print("\nRESULT:")
    print(f"Segments Count: {len(result.get('segments', []))}")
    print(f"Line Count: {result.get('line_count', 0)}")
    print(f"Confidence: {result.get('confidence', 0.0):.3f}")
    print(f"Language: {result.get('language', 'unknown')}")
    
    if result.get('segments'):
        print(f"\nFirst 3 segments:")
        for i, seg in enumerate(result['segments'][:3]):
            print(f"  [{i}] {seg.get('start', 0):.2f}-{seg.get('end', 0):.2f}: {seg.get('text', '')[:60]}")
    else:
        print("\n⚠️ NO SEGMENTS RETURNED!")
    
    print("="*80 + "\n")
    
    return result


def test_candidate_transcription(video_path, cfg):
    """Test how candidate-level transcription works (legacy path)."""
    from pipeline.highlight import Pipeline
    
    print("\n" + "#"*80)
    print("# TEST 1: CANDIDATE-LEVEL TRANSCRIPTION (Legacy Path)")
    print("#"*80 + "\n")
    
    # Temporarily disable story mode
    cfg_legacy = cfg.copy()
    cfg_legacy['use_story_centric_pipeline'] = False
    
    pipe = Pipeline(cfg_legacy)
    
    # Get one candidate window
    windows = list(pipe._candidate_windows(video_path))
    if not windows:
        print("❌ No windows found")
        return None
    
    window_start, window_end, source = windows[0]
    print(f"Testing window: {window_start:.2f}-{window_end:.2f}")
    
    # Extract audio for this window
    print("\nExtracting candidate audio...")
    temp_dir = Path("_debug_transcription")
    temp_dir.mkdir(exist_ok=True)
    
    wav_path = pipe._extract_candidate_wav(video_path, str(temp_dir), idx=0)
    
    if not wav_path or not os.path.exists(wav_path):
        print(f"❌ WAV not created: {wav_path}")
        return None
    
    print(f"✓ WAV created: {wav_path}")
    
    # Now transcribe it
    from pipeline.subtitle import transcribe_segment
    result = transcribe_segment(wav_path, str(temp_dir), idx=0, cfg=cfg)
    
    return {
        'window': (window_start, window_end),
        'wav_path': wav_path,
        'result': result,
    }


def test_episode_transcription(video_path, cfg):
    """Test how episode-level transcription works (new Sprint 1.6 path)."""
    from pipeline.highlight import Pipeline
    
    print("\n" + "#"*80)
    print("# TEST 2: EPISODE-LEVEL TRANSCRIPTION (Sprint 1.6 Path)")
    print("#"*80 + "\n")
    
    cfg_story = cfg.copy()
    cfg_story['use_story_centric_pipeline'] = True
    
    pipe = Pipeline(cfg_story)
    
    # Call the new method
    result = pipe._transcribe_full_episode(video_path)
    
    return {
        'result': result,
    }


def compare_transcriptions(candidate_data, episode_data):
    """Compare the two approaches."""
    print("\n" + "#"*80)
    print("# COMPARISON")
    print("#"*80 + "\n")
    
    c_result = candidate_data.get('result', {}) if candidate_data else {}
    e_result = episode_data.get('result', {}) if episode_data else {}
    
    c_segments = len(c_result.get('segments', []))
    e_segments = len(e_result.get('segments', []))
    
    print(f"Candidate-level segments: {c_segments}")
    print(f"Episode-level segments:   {e_segments}")
    print(f"Delta:                    {e_segments - c_segments:+d}")
    
    if c_segments > 0 and e_segments == 0:
        print("\n🔴 HYPOTHESIS CONFIRMED:")
        print("   Candidate transcription WORKS, episode transcription FAILS")
        print("   → Problem is in Sprint 1.6 implementation")
    elif c_segments == 0 and e_segments == 0:
        print("\n🟡 BOTH FAIL:")
        print("   Neither approach works")
        print("   → Whisper infrastructure problem")
    elif e_segments > 0:
        print("\n🟢 BOTH WORK:")
        print("   Episode transcription is functional")
        print("   → Validation script may have other issue")
    
    print("\n")


def main():
    video_path = "episode01_test.avi"
    
    if not os.path.exists(video_path):
        print(f"❌ Video not found: {video_path}")
        return 1
    
    print("="*80)
    print("TRANSCRIPTION DEBUG SESSION")
    print("="*80)
    print(f"Video: {video_path}")
    print(f"Size: {os.path.getsize(video_path):,} bytes")
    print()
    
    # Load config
    from pipeline.config import load_config
    cfg = load_config("settings.yaml")
    
    # Install debug wrapper
    global original_transcribe
    from pipeline import subtitle
    original_transcribe = subtitle.transcribe_segment
    subtitle.transcribe_segment = debug_transcribe_segment
    
    # Test 1: Candidate-level
    candidate_data = test_candidate_transcription(video_path, cfg)
    
    # Test 2: Episode-level  
    episode_data = test_episode_transcription(video_path, cfg)
    
    # Compare
    compare_transcriptions(candidate_data, episode_data)
    
    # Save detailed report
    report = {
        'video': video_path,
        'candidate': candidate_data,
        'episode': episode_data,
    }
    
    report_path = "_debug_transcription/debug_report.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"Detailed report saved to: {report_path}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())

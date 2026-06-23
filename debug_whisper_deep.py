"""
Deep Whisper debugging - trace inside transcribe_segment.

Goal: Find where segments are lost in the transcription pipeline.
"""

import os
import sys
from pathlib import Path


def test_whisper_directly():
    """Test Whisper directly on the WAV file."""
    
    wav_path = "_debug_transcription/cand_0.wav"
    
    if not os.path.exists(wav_path):
        print(f"❌ WAV not found: {wav_path}")
        return
    
    print("="*80)
    print("DIRECT WHISPER TEST")
    print("="*80)
    print(f"WAV: {wav_path}")
    print(f"Size: {os.path.getsize(wav_path):,} bytes")
    print()
    
    try:
        from faster_whisper import WhisperModel
        
        print("Loading Whisper model...")
        model = WhisperModel("base", device="cpu", compute_type="int8")
        print("✓ Model loaded\n")
        
        print("Transcribing...")
        segments, info = model.transcribe(
            wav_path,
            language="ru",
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=True,
            word_timestamps=True,
        )
        
        print(f"✓ Transcription complete")
        print(f"Detected language: {info.language if hasattr(info, 'language') else 'unknown'}")
        print(f"Language probability: {info.language_probability if hasattr(info, 'language_probability') else 0:.3f}")
        print()
        
        # Convert generator to list
        segments_list = list(segments)
        
        print(f"Raw segments from Whisper: {len(segments_list)}")
        
        if segments_list:
            print("\nFirst 5 segments:")
            for i, seg in enumerate(segments_list[:5]):
                print(f"  [{i}] {seg.start:.2f}-{seg.end:.2f}: {seg.text}")
                if hasattr(seg, 'words') and seg.words:
                    print(f"      Words: {len(seg.words)}")
        else:
            print("\n❌ Whisper returned ZERO segments!")
            print("\nPossible reasons:")
            print("  1. VAD filter too aggressive (filtering all audio as silence)")
            print("  2. Audio is actually silent/corrupted")
            print("  3. Wrong language setting")
            print("  4. Model not detecting any speech")
        
        # Test without VAD
        print("\n" + "-"*80)
        print("RETRY WITHOUT VAD FILTER")
        print("-"*80)
        
        segments2, info2 = model.transcribe(
            wav_path,
            language="ru",
            beam_size=5,
            vad_filter=False,  # Disable VAD
            condition_on_previous_text=True,
            word_timestamps=True,
        )
        
        segments_list2 = list(segments2)
        print(f"Segments without VAD: {len(segments_list2)}")
        
        if segments_list2:
            print("\nFirst 5 segments:")
            for i, seg in enumerate(segments_list2[:5]):
                print(f"  [{i}] {seg.start:.2f}-{seg.end:.2f}: {seg.text}")
        
        # Test with auto language
        print("\n" + "-"*80)
        print("RETRY WITH AUTO LANGUAGE")
        print("-"*80)
        
        segments3, info3 = model.transcribe(
            wav_path,
            language=None,  # Auto-detect
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=True,
            word_timestamps=True,
        )
        
        segments_list3 = list(segments3)
        print(f"Segments with auto language: {len(segments_list3)}")
        print(f"Detected language: {info3.language if hasattr(info3, 'language') else 'unknown'}")
        
        if segments_list3:
            print("\nFirst 5 segments:")
            for i, seg in enumerate(segments_list3[:5]):
                print(f"  [{i}] {seg.start:.2f}-{seg.end:.2f}: {seg.text}")
        
        # Summary
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        print(f"With VAD + language=ru:    {len(segments_list)} segments")
        print(f"Without VAD + language=ru: {len(segments_list2)} segments")
        print(f"Without VAD + language=auto: {len(segments_list3)} segments")
        print()
        
        if len(segments_list) == 0 and len(segments_list2) > 0:
            print("🔴 PROBLEM IDENTIFIED: VAD filter is TOO AGGRESSIVE")
            print("   It filters out all speech as 'silence'")
            print("   Solution: Disable VAD or adjust VAD parameters")
        elif len(segments_list) == 0 and len(segments_list3) > 0:
            print("🔴 PROBLEM IDENTIFIED: Language mismatch")
            print(f"   Expected: ru, Actual: {info3.language}")
            print("   Solution: Use auto-detect or correct language")
        elif len(segments_list) == 0 and len(segments_list2) == 0 and len(segments_list3) == 0:
            print("🔴 PROBLEM IDENTIFIED: Audio issue or model problem")
            print("   Whisper cannot find ANY speech in the audio")
            print("   Solution: Check if audio is valid, try different model")
        else:
            print("🟢 Transcription works!")
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()


def check_audio_content():
    """Check if audio file actually contains speech."""
    
    wav_path = "_debug_transcription/cand_0.wav"
    
    print("\n" + "="*80)
    print("AUDIO CONTENT CHECK")
    print("="*80)
    
    try:
        import wave
        import struct
        
        with wave.open(wav_path, 'rb') as wf:
            sample_rate = wf.getframerate()
            n_channels = wf.getnchannels()
            n_frames = wf.getnframes()
            
            # Read first 1 second
            frames_to_read = min(sample_rate, n_frames)
            audio_data = wf.readframes(frames_to_read)
            
            # Convert to samples
            fmt = f"{frames_to_read * n_channels}h"
            samples = struct.unpack(fmt, audio_data)
            
            # Calculate RMS
            rms = (sum(s*s for s in samples) / len(samples)) ** 0.5
            
            print(f"First second RMS: {rms:.2f}")
            
            if rms < 100:
                print("⚠️ Audio seems very quiet or silent")
            else:
                print("✓ Audio contains signal")
            
            # Check for peaks
            max_val = max(abs(s) for s in samples)
            print(f"Peak amplitude: {max_val} / 32768 ({max_val/32768*100:.1f}%)")
            
    except Exception as e:
        print(f"Error checking audio: {e}")


if __name__ == '__main__':
    check_audio_content()
    test_whisper_directly()

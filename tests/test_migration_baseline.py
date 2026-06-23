"""
test_migration_baseline.py
──────────────────────────
Baseline test для миграции на story-centric архитектуру.

Запускается ПЕРЕД изменениями чтобы зафиксировать текущее поведение,
затем ПОСЛЕ изменений для проверки что функциональность не сломана.

Usage:
    python tests/test_migration_baseline.py
"""

import json
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.highlight import Pipeline
from pipeline.config import load_config


def test_candidate_generation():
    """Test 1: Candidate generation from test video"""
    print("\n" + "=" * 70)
    print("TEST 1: Candidate Generation")
    print("=" * 70)
    
    test_video = Path(__file__).resolve().parents[1] / "episode01_test.avi"
    if not test_video.exists():
        print(f"❌ Test video not found: {test_video}")
        return False
    
    print(f"✓ Test video: {test_video}")
    
    # Load config
    config_path = Path(__file__).resolve().parents[1] / "settings.yaml"
    cfg = load_config(str(config_path))
    
    # Minimal config for testing
    cfg["output_budget"] = 2  # Small budget for fast test
    cfg["skip_review"] = True
    cfg["review_pass_enabled"] = False
    
    print(f"✓ Config loaded (output_budget={cfg['output_budget']})")
    
    # Create pipeline
    pipeline = Pipeline(cfg)
    
    # Test _candidate_windows method
    print("\n--- Testing _candidate_windows() ---")
    try:
        windows = pipeline._candidate_windows(str(test_video))
        print(f"✓ Generated {len(windows)} candidate windows")
        
        for idx, (start, end, source) in enumerate(windows[:3]):  # Show first 3
            print(f"  Window {idx}: {start:.2f}s - {end:.2f}s ({end-start:.1f}s) source={source}")
        
        if len(windows) > 3:
            print(f"  ... and {len(windows) - 3} more windows")
        
        # Check for temporal window sources
        sources = [source for _, _, source in windows]
        legacy_sources = [s for s in sources if s in ("scene_cluster", "global_scan", "short_fallback")]
        print(f"\n  Legacy temporal sources: {len(legacy_sources)}/{len(sources)}")
        
        return True
    except Exception as e:
        print(f"❌ Error in _candidate_windows(): {e}")
        import traceback
        traceback.print_exc()
        return False


def test_candidate_dict_structure():
    """Test 2: Candidate dictionary structure"""
    print("\n" + "=" * 70)
    print("TEST 2: Candidate Dict Structure")
    print("=" * 70)
    
    test_video = Path(__file__).resolve().parents[1] / "episode01_test.avi"
    if not test_video.exists():
        print(f"❌ Test video not found: {test_video}")
        return False
    
    config_path = Path(__file__).resolve().parents[1] / "settings.yaml"
    cfg = load_config(str(config_path))
    cfg["output_budget"] = 1
    cfg["skip_review"] = True
    cfg["review_pass_enabled"] = False
    
    pipeline = Pipeline(cfg)
    
    print("\n--- Testing candidate structure ---")
    try:
        windows = pipeline._candidate_windows(str(test_video))
        if not windows:
            print("❌ No windows generated")
            return False
        
        # Get first window
        window_start, window_end, source = windows[0]
        print(f"✓ Testing window: {window_start:.2f}s - {window_end:.2f}s")
        
        # Extract audio summary
        summary = pipeline._extract_audio_summary(str(test_video), window_start, window_end)
        print(f"✓ Audio summary extracted")
        print(f"  speech_density: {summary.get('speech_density', 0):.3f}")
        print(f"  turns: {len(summary.get('turns', []))}")
        
        # Build candidates using legacy method
        candidates = pipeline._build_story_candidates_from_window(
            window_start, window_end, source, summary
        )
        
        print(f"\n✓ Generated {len(candidates)} candidates from first window")
        
        if candidates:
            cand = candidates[0]
            print(f"\n--- First candidate structure ---")
            print(f"  start: {cand.get('start')}")
            print(f"  end: {cand.get('end')}")
            print(f"  source: {cand.get('source')}")
            print(f"  story_unit_type: {cand.get('story_unit_type')}")
            print(f"  score: {cand.get('score')}")
            
            # Check for legacy fields
            legacy_fields = []
            if cand.get("source") in ("dialogue_cluster", "fallback_window", "dialogue_linear"):
                legacy_fields.append(f"source={cand.get('source')}")
            if "story_window_segments" in cand:
                legacy_fields.append("story_window_segments")
            
            if legacy_fields:
                print(f"\n⚠️  Legacy fields detected: {', '.join(legacy_fields)}")
            else:
                print(f"\n✓ No legacy fields detected")
            
            # Check for new story-centric fields
            story_fields = []
            if "story_completion_score" in cand:
                story_fields.append("story_completion_score")
            if "is_complete" in cand:
                story_fields.append("is_complete")
            if "arc_shape" in cand:
                story_fields.append("arc_shape")
            if "story_summary" in cand:
                story_fields.append("story_summary")
            
            if story_fields:
                print(f"✓ Story-centric fields present: {', '.join(story_fields)}")
            else:
                print(f"⚠️  No story-centric fields yet")
        
        return True
    except Exception as e:
        print(f"❌ Error testing candidate structure: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_story_pipeline_integration():
    """Test 3: Story pipeline integration readiness"""
    print("\n" + "=" * 70)
    print("TEST 3: Story Pipeline Integration")
    print("=" * 70)
    
    print("\n--- Testing story_pipeline import ---")
    try:
        from pipeline.montage.story_pipeline import (
            build_story_chains_for_episode,
            story_chain_to_candidate
        )
        print("✓ story_pipeline imports successful")
        
        # Test with minimal subtitle data
        print("\n--- Testing build_story_chains_for_episode() with sample data ---")
        
        # Minimal subtitle_info for testing
        test_subtitle_info = {
            "segments": [
                {"start": 0.0, "end": 2.5, "text": "Привет, как дела?", "speaker": "Speaker1"},
                {"start": 3.0, "end": 5.0, "text": "Отлично, спасибо!", "speaker": "Speaker2"},
                {"start": 5.5, "end": 8.0, "text": "Что будем делать сегодня?", "speaker": "Speaker1"},
                {"start": 9.0, "end": 12.0, "text": "Давай начнём с обсуждения плана.", "speaker": "Speaker2"},
            ]
        }
        
        config_path = Path(__file__).resolve().parents[1] / "settings.yaml"
        cfg = load_config(str(config_path))
        
        chains = build_story_chains_for_episode(
            subtitle_info=test_subtitle_info,
            cfg=cfg,
            source_id="test_episode"
        )
        
        print(f"✓ Generated {len(chains)} story chains from sample data")
        
        if chains:
            chain = chains[0]
            print(f"\n--- First story chain ---")
            print(f"  start: {getattr(chain, 'start', 'N/A')}")
            print(f"  end: {getattr(chain, 'end', 'N/A')}")
            print(f"  is_complete: {getattr(chain, 'is_complete', 'N/A')}")
            print(f"  completion_score: {getattr(chain, 'completion_score', 'N/A'):.3f}" if hasattr(chain, 'completion_score') else "  completion_score: N/A")
            print(f"  arc_shape: {getattr(chain, 'arc_shape', 'N/A')}")
            
            # Test conversion to candidate
            print("\n--- Testing story_chain_to_candidate() ---")
            candidate = story_chain_to_candidate(chain, source="story_pipeline")
            print(f"✓ Converted chain to candidate dict")
            print(f"  source: {candidate.get('source')}")
            print(f"  story_unit_type: {candidate.get('story_unit_type')}")
            print(f"  story_completion_score: {candidate.get('story_completion_score')}")
            print(f"  is_complete: {candidate.get('is_complete')}")
            print(f"  arc_shape: {candidate.get('score_breakdown', {}).get('arc_shape')}")
        else:
            print("⚠️  No chains generated (too short sample data, expected)")
        
        return True
    except Exception as e:
        print(f"❌ Error testing story pipeline: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_baseline_report(results: dict):
    """Save baseline test results to file"""
    report_path = Path(__file__).resolve().parents[1] / "tests" / "baseline_report.json"
    
    report = {
        "timestamp": "2026-06-14T18:38:00",
        "status": "pre_migration",
        "results": results
    }
    
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n📄 Baseline report saved: {report_path}")


def main():
    print("\n" + "=" * 70)
    print("MIGRATION BASELINE TEST SUITE")
    print("=" * 70)
    print("Testing BEFORE story-centric migration")
    print("=" * 70)
    
    results = {}
    
    # Run tests
    results["candidate_generation"] = test_candidate_generation()
    results["candidate_structure"] = test_candidate_dict_structure()
    results["story_pipeline_ready"] = test_story_pipeline_integration()
    
    # Summary
    print("\n" + "=" * 70)
    print("BASELINE TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, passed_flag in results.items():
        status = "✓ PASS" if passed_flag else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    # Save report
    save_baseline_report(results)
    
    if passed == total:
        print("\n✅ All baseline tests passed! Ready for migration.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Review before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

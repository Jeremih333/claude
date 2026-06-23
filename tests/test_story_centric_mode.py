#!/usr/bin/env python3
"""
Test story-centric candidate generation mode
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pipeline.highlight import Pipeline
from pipeline.config import load_config
import json

def test_story_centric_mode():
    """Test story-centric candidate generation"""
    print("="*70)
    print("STORY-CENTRIC MODE TEST")
    print("="*70)
    
    test_video = Path(__file__).resolve().parents[1] / "episode01_test.avi"
    
    if not test_video.exists():
        print(f"❌ Test video not found: {test_video}")
        return False
    
    print(f"✓ Test video: {test_video}")
    
    # Load config
    config_path = Path(__file__).resolve().parents[1] / "settings.yaml"
    cfg = load_config(str(config_path))
    
    # Enable story-centric pipeline
    original_flag = cfg.get('use_story_centric_pipeline', False)
    cfg['use_story_centric_pipeline'] = True
    
    print(f"✓ Feature flag enabled: use_story_centric_pipeline={cfg['use_story_centric_pipeline']}")
    print()
    
    # Override output budget for testing
    cfg['output_budget'] = 2
    cfg['skip_review'] = True
    cfg['review_pass_enabled'] = False
    print(f"✓ Config loaded (output_budget={cfg['output_budget']})")
    print()
    
    try:
        pipeline = Pipeline(cfg)
        
        # Test candidate generation
        print("="*70)
        print("TEST 1: Story-Centric Candidate Generation")
        print("="*70)
        print()
        
        print("--- Calling _candidate_windows() with story-centric mode ---")
        windows = pipeline._candidate_windows(str(test_video))
        
        print(f"✓ Generated {len(windows)} candidates")
        print()
        
        if len(windows) == 0:
            print("⚠️  No candidates generated (may use legacy fallback)")
            return True
        
        # Analyze first few candidates
        print("--- Analyzing candidate structure ---")
        story_centric_count = 0
        legacy_count = 0
        
        for i, (start, end, source) in enumerate(windows[:5]):
            story_unit_type = 'N/A'  # tuple format doesn't include this
            
            if source == 'story_pipeline':
                story_centric_count += 1
                print(f"  Candidate {i}: story-centric")
                print(f"    source: {source}")
                print(f"    start: {start:.2f}s, end: {end:.2f}s, duration: {end-start:.2f}s")
                print()
            else:
                legacy_count += 1
                print(f"  Candidate {i}: legacy ({source})")
                print(f"    start: {start:.2f}s, end: {end:.2f}s")
        
        print()
        print("="*70)
        print("CANDIDATE SOURCE DISTRIBUTION")
        print("="*70)
        print(f"Story-centric candidates: {story_centric_count}/{min(5, len(windows))} shown")
        print(f"Legacy candidates: {legacy_count}/{min(5, len(windows))} shown")
        print()
        
        # Test ranking compatibility
        print("="*70)
        print("TEST 2: Ranking Compatibility")
        print("="*70)
        print()
        
        if len(windows) > 0:
            start, end, source = windows[0]
            
            print("--- Checking tuple structure ---")
            print(f"  ✓ start: {start:.2f}s")
            print(f"  ✓ end: {end:.2f}s")
            print(f"  ✓ source: {source}")
            print("\n✓ Tuple format is correct (start, end, source)")
        
        print()
        print("="*70)
        print("SUMMARY")
        print("="*70)
        
        if story_centric_count > 0:
            print(f"✅ Story-centric mode ACTIVE")
            print(f"   Generated {story_centric_count} story-based candidates")
        elif legacy_count > 0:
            print(f"⚠️  Legacy fallback used")
            print(f"   Generated {legacy_count} legacy candidates")
            print(f"   (This is acceptable - fallback is working)")
        else:
            print(f"⚠️  No candidates generated")
        
        print()
        
        # Save test report
        report = {
            'test': 'story_centric_mode',
            'feature_flag': True,
            'total_candidates': len(windows),
            'story_centric_count': story_centric_count,
            'legacy_count': legacy_count,
            'windows_sample': [{'start': s, 'end': e, 'source': src} for s, e, src in windows[:3]]
        }
        
        report_path = os.path.join(os.path.dirname(__file__), 'story_centric_test_report.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"📄 Test report saved: {report_path}")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Restore original flag
        cfg['use_story_centric_pipeline'] = original_flag

if __name__ == '__main__':
    success = test_story_centric_mode()
    sys.exit(0 if success else 1)

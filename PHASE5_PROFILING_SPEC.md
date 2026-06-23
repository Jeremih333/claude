# PHASE 5 — PROFILING EXPANSION SPEC
## Enhanced Metrics for Optimization Validation

**Date:** 2026-06-22  
**Status:** SPEC COMPLETE  
**Purpose:** Track optimization impact with granular metrics

---

## 🎯 PROFILING PHILOSOPHY

**Current profiling is too coarse:**
- `face_detection_sec = 22.4s` — single monolithic metric
- No breakdown of WHERE time is spent
- Can't measure optimization impact precisely

**New profiling must reveal:**
- Cache hit rates and savings
- Adaptive sampling distribution
- Early exit effectiveness
- Per-optimization attribution

---

## 📊 EXPANDED METRIC STRUCTURE

### Level 1: Existing Metrics (KEEP)
```python
{
    "episode_title": "Episode 42",
    "total_sec": 24.1,
    "face_detection_sec": 22.4,  # TOTAL FACE WORK
    "candidates_evaluated": 15,
    "shorts_generated": 3
}
```

---

### Level 2: Face Detection Breakdown (NEW)
```python
{
    "face_detection_breakdown": {
        # Time Attribution
        "detector_init_sec": 0.18,           # Detector loading
        "frame_decode_sec": 4.2,             # Video frame extraction
        "mediapipe_inference_sec": 15.8,     # Actual ML inference
        "haar_fallback_sec": 0.6,            # OpenCV fallback
        "track_building_sec": 1.2,           # Track merging/building
        "track_merge_sec": 0.3,              # Overlap resolution
        "crop_target_compute_sec": 0.3,      # Crop coordinate calc
        
        # Frame Counts
        "frames_decoded": 1350,
        "frames_mediapipe": 1280,
        "frames_haar_fallback": 70,
        "frames_person_detection": 0,
        
        # Detection Rates
        "avg_mediapipe_ms_per_frame": 12.3,
        "avg_haar_ms_per_frame": 8.6,
        "avg_track_build_ms": 0.9
    }
}
```

---

### Level 3: Cache Performance (NEW)
```python
{
    "cache_performance": {
        # Hit Rates
        "cache_enabled": True,
        "cache_hits": 8,                     # Exact interval matches
        "cache_partial_hits": 4,             # Partial overlaps
        "cache_misses": 3,                   # No overlap
        "hit_rate": 0.80,                    # (8+4) / 15
        
        # Overlap Reuse
        "overlap_reuse_seconds": 180.0,      # Total overlap time reused
        "overlap_reuse_percent": 40.0,       # 40% of work avoided
        
        # Frame Savings
        "frames_computed_new": 810,          # New frames scanned
        "frames_reused_cache": 540,          # Frames from cache
        "frames_total_requested": 1350,
        "redundancy_eliminated": 0.40,       # 40% reduction
        
        # Cache Statistics
        "cache_size_mb": 145.2,
        "cache_entries": 12,
        "cache_evictions": 2,
        
        # Time Savings
        "time_saved_by_cache_sec": 8.9,     # Estimated time saved
        "time_without_cache_sec": 31.3,     # Hypothetical no-cache time
        "cache_speedup_factor": 1.40         # 1.40× faster
    }
}
```

---

### Level 4: Adaptive Sampling (NEW)
```python
{
    "adaptive_sampling": {
        # Sampling Distribution
        "adaptive_enabled": True,
        "frames_dense_sampled": 180,         # 6fps zones
        "frames_moderate_sampled": 540,      # 3fps zones
        "frames_sparse_sampled": 90,         # 1fps zones
        "total_frames": 810,
        
        # FPS Metrics
        "avg_fps_dense_zones": 6.0,
        "avg_fps_moderate_zones": 3.0,
        "avg_fps_sparse_zones": 1.2,
        "avg_fps_overall": 2.7,              # Down from 3.0
        
        # Zone Metrics
        "dense_zone_seconds": 30.0,          # Turn boundaries
        "moderate_zone_seconds": 180.0,      # Normal dialogue
        "sparse_zone_seconds": 75.0,         # Stable locks
        
        # Guard Triggers
        "turn_boundary_dense_triggers": 12,
        "confidence_drop_triggers": 3,
        "motion_spike_triggers": 5,
        
        # Savings
        "frames_saved_by_adaptive": 135,     # vs fixed 3fps
        "time_saved_sec": 1.8,
        "sampling_efficiency": 0.17          # 17% reduction
    }
}
```

---

### Level 5: Early Exit Performance (NEW)
```python
{
    "early_exit": {
        # Exit Statistics
        "early_exit_enabled": True,
        "early_exit_hits": 6,
        "early_exit_attempts": 15,
        "exit_success_rate": 0.40,           # 40% of candidates
        
        # Exit Duration
        "avg_exit_duration_sec": 8.5,
        "total_exit_time_sec": 51.0,
        "total_scan_time_sec": 450.0,
        "exit_time_ratio": 0.11,             # 11% of time in exit
        
        # Blockers
        "blocked_by_turn_boundary": 4,
        "blocked_by_confidence_drop": 2,
        "blocked_by_multi_speaker": 2,
        "blocked_by_motion": 1,
        
        # Reasons
        "exit_reasons": {
            "high_confidence_lock": 3,
            "stable_framing": 2,
            "turn_continuity": 1
        },
        
        "resume_reasons": {
            "turn_approaching": 3,
            "confidence_drop": 1,
            "motion_increase": 1,
            "conditions_not_met": 1
        },
        
        # Savings
        "frames_skipped": 128,
        "time_saved_sec": 1.7,
        "early_exit_efficiency": 0.10        # 10% reduction
    }
}
```

---

### Level 6: Quality Validation (NEW)
```python
{
    "quality_validation": {
        # Invariants
        "turn_first_compliance": 1.00,       # MUST be 1.00
        "switch_capture_rate": 1.00,         # MUST be 1.00
        "track_continuity_score": 0.97,      # ≥0.95
        "crop_stability_score": 0.94,        # ≥0.92
        "reaction_capture_rate": 0.62,       # ≥0.50
        "dual_speaker_recall": 0.88,         # ≥0.85
        "handoff_precision_avg_ms": 280,     # ≤500ms
        
        # Violations
        "invariant_violations": 0,           # MUST be 0
        "constraint_violations": 0,          # MUST be 0
        
        # Degradation Detection
        "quality_degradation_detected": False,
        "rollback_recommended": False,
        
        # Speaker Switching
        "speaker_switches_detected": 12,
        "speaker_switches_expected": 12,
        "switches_missed": 0,                # MUST be 0
        
        # Turn Boundaries
        "turn_boundaries_total": 18,
        "turn_boundaries_dense_sampled": 18, # MUST equal total
        "turn_sampling_failures": 0          # MUST be 0
    }
}
```

---

### Level 7: Optimization Attribution (NEW)
```python
{
    "optimization_attribution": {
        # Time Saved Breakdown
        "baseline_time_sec": 31.3,           # Hypothetical no-opt
        "optimized_time_sec": 22.4,          # Actual with opts
        "total_savings_sec": 8.9,
        "total_speedup_factor": 1.40,        # 1.40× faster
        
        # Per-Optimization Attribution
        "track_cache_savings_sec": 6.2,      # 70% of savings
        "adaptive_sampling_savings_sec": 1.8, # 20% of savings
        "early_exit_savings_sec": 0.9,       # 10% of savings
        "detector_pooling_savings_sec": 0.0, # Negligible
        
        # Percentage Breakdown
        "track_cache_contribution": 0.70,
        "adaptive_sampling_contribution": 0.20,
        "early_exit_contribution": 0.10,
        
        # Efficiency Ratios
        "track_cache_efficiency": 0.35,      # 35% reduction
        "adaptive_sampling_efficiency": 0.17, # 17% reduction
        "early_exit_efficiency": 0.10,       # 10% reduction
        "combined_efficiency": 0.51          # 51% total reduction
    }
}
```

---

### Level 8: Per-Candidate Breakdown (NEW)
```python
{
    "per_candidate_metrics": [
        {
            "candidate_id": "cand_001",
            "start_sec": 120.0,
            "end_sec": 150.0,
            "duration_sec": 30.0,
            
            # Detection Metrics
            "frames_scanned": 65,
            "detection_time_sec": 1.8,
            "cache_hit": True,
            "cache_overlap_percent": 50.0,
            
            # Sampling
            "avg_fps": 2.2,
            "dense_frames": 15,
            "moderate_frames": 40,
            "sparse_frames": 10,
            
            # Early Exit
            "early_exit_triggered": True,
            "early_exit_duration_sec": 8.0,
            
            # Quality
            "speaker_switches": 2,
            "track_continuity": 0.98,
            "crop_stability": 0.95
        },
        # ... more candidates
    ]
}
```

---

## 🔬 IMPLEMENTATION POINTS

### Point 1: `pipeline/benchmarking.py` Extension
```python
# Add to FaceDetectionProfiler class
class FaceDetectionProfiler:
    def __init__(self):
        self.timers = {
            'detector_init': Timer(),
            'frame_decode': Timer(),
            'mediapipe_inference': Timer(),
            'haar_fallback': Timer(),
            'track_building': Timer(),
            'track_merge': Timer(),
            'crop_compute': Timer()
        }
        
        self.counters = {
            'frames_decoded': 0,
            'frames_mediapipe': 0,
            'frames_haar': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            # ... more counters
        }
    
    def record_cache_hit(self, overlap_percent):
        self.counters['cache_hits'] += 1
        self.cache_overlaps.append(overlap_percent)
    
    def record_adaptive_sample(self, fps, reason):
        self.adaptive_samples.append({'fps': fps, 'reason': reason})
    
    def record_early_exit(self, duration, reason):
        self.early_exits.append({'duration': duration, 'reason': reason})
    
    def generate_report(self):
        return {
            'face_detection_breakdown': self._compute_breakdown(),
            'cache_performance': self._compute_cache_stats(),
            'adaptive_sampling': self._compute_sampling_stats(),
            'early_exit': self._compute_exit_stats(),
            'quality_validation': self._validate_quality(),
            'optimization_attribution': self._attribute_savings()
        }
```

---

### Point 2: Context Manager for Timing
```python
class SectionTimer:
    def __init__(self, profiler, section_name):
        self.profiler = profiler
        self.section = section_name
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, *args):
        elapsed = time.time() - self.start_time
        self.profiler.timers[self.section].add(elapsed)

# Usage:
with SectionTimer(profiler, 'mediapipe_inference'):
    faces = detector.process(frame)
```

---

### Point 3: Cache Instrumentation
```python
# In IntervalTrackCache.get_or_compute()
def get_or_compute(self, video_path, start, end, fps, profile, compute_fn):
    # ... existing logic ...
    
    if exact_hit:
        profiler.record_cache_hit(overlap_percent=100.0)
    elif partial_hit:
        profiler.record_cache_hit(overlap_percent=overlap_percent)
    else:
        profiler.record_cache_miss()
    
    # ... continue ...
```

---

### Point 4: Adaptive Sampling Instrumentation
```python
# In AdaptiveSampler.get_next_sample_time()
def get_next_sample_time(self, current_time, context):
    fps = self._determine_fps(context)
    reason = self._get_sample_reason(context)
    
    profiler.record_adaptive_sample(fps, reason)
    
    return current_time + (1.0 / fps)
```

---

### Point 5: Early Exit Instrumentation
```python
# In EarlyExitController._enter_exit_mode()
def _enter_exit_mode(self, current_time, context):
    self.exit_start = current_time
    # ... existing logic ...

# In EarlyExitController._resume_detection()
def _resume_detection(self, reason):
    duration = time.time() - self.exit_start
    profiler.record_early_exit(duration, reason)
    # ... existing logic ...
```

---

## 📈 PROFILING OUTPUT FORMAT

### JSON Structure:
```json
{
  "episode_metadata": {
    "title": "Episode 42",
    "duration_sec": 2400,
    "generated_at": "2026-06-22T16:12:00Z"
  },
  
  "summary": {
    "total_sec": 24.1,
    "face_detection_sec": 22.4,
    "candidates_evaluated": 15,
    "shorts_generated": 3,
    "optimization_enabled": true,
    "speedup_factor": 1.40
  },
  
  "face_detection_breakdown": { ... },
  "cache_performance": { ... },
  "adaptive_sampling": { ... },
  "early_exit": { ... },
  "quality_validation": { ... },
  "optimization_attribution": { ... },
  "per_candidate_metrics": [ ... ]
}
```

---

## 🎯 VALIDATION QUERIES

### Query 1: Is cache working?
```python
hit_rate = metrics['cache_performance']['hit_rate']
assert hit_rate > 0.50, f"Cache hit rate too low: {hit_rate}"
```

### Query 2: Is adaptive sampling safe?
```python
turn_compliance = metrics['quality_validation']['turn_first_compliance']
assert turn_compliance == 1.00, f"Turn sampling compromised: {turn_compliance}"
```

### Query 3: Which optimization helps most?
```python
attribution = metrics['optimization_attribution']
print(f"Track cache: {attribution['track_cache_contribution']:.0%}")
print(f"Adaptive sampling: {attribution['adaptive_sampling_contribution']:.0%}")
print(f"Early exit: {attribution['early_exit_contribution']:.0%}")
```

### Query 4: Are we meeting the 40-60% goal?
```python
efficiency = metrics['optimization_attribution']['combined_efficiency']
assert 0.40 <= efficiency <= 0.70, \
    f"Efficiency {efficiency:.0%} outside target range [40-60%]"
```

---

## 📊 PROFILING DASHBOARD

### Console Output:
```
=== PHASE 5 PROFILING REPORT ===

Episode: Episode 42
Duration: 40:00
Candidates: 15 → Shorts: 3

Performance:
  Total Time:           24.1s
  Face Detection:       22.4s (93%)
  Speedup vs Baseline:  1.40× (baseline: 31.3s)

Optimization Breakdown:
  ✅ Track Cache:         -6.2s (35% reduction)
  ✅ Adaptive Sampling:   -1.8s (17% reduction)
  ✅ Early Exit:          -0.9s (10% reduction)
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Total Savings:          -8.9s (51% reduction)

Cache Performance:
  Hit Rate:             80% (12/15 candidates)
  Overlap Reuse:        40% (540/1350 frames)
  Cache Size:           145 MB

Adaptive Sampling:
  Dense (6fps):         180 frames (22%)
  Moderate (3fps):      540 frames (67%)
  Sparse (1fps):        90 frames (11%)
  Avg FPS:              2.7 (vs 3.0 baseline)

Early Exit:
  Success Rate:         40% (6/15 candidates)
  Avg Exit Duration:    8.5s
  Frames Skipped:       128

Quality Validation:
  ✅ Turn Compliance:     100%
  ✅ Switch Capture:      100%
  ✅ Track Continuity:    97%
  ✅ Crop Stability:      94%
  ✅ Invariants:          0 violations

Status: ✅ ALL TARGETS MET
```

---

**End of Profiling Spec**

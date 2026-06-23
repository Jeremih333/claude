# 🔍 SPRINT 1.6 PROFILING REPORT
## Story-Centric Pipeline Performance Analysis

**Generated:** 2026-06-16 00:57 UTC+3  
**Episode:** episode01_test.avi  
**Validation Run:** _validation_sprint_1_6

---

## 📊 EXECUTIVE SUMMARY

### Critical Bottleneck Identified
**Face Detection** is the primary performance bottleneck, consuming **~96% of total scoring time**:
- Average: **23.17 seconds per candidate**
- Range: 22.29s - 23.84s
- Total impact: **~70 seconds** for 3 candidates

### Observed Timeout Patterns
From validation logs:
- **Legacy mode:** 1 ranking timeout (story 2.92-113.32) after 30s
- **Story mode:** 6 ranking timeouts after 30s each
- **Semantic preview:** Multiple 60s+ operations detected

---

## 📈 DETAILED PROFILING DATA

### Per-Candidate Breakdown (Legacy Mode)

| Candidate | Total Time | Face Detection | Video Metrics | Premise Scoring |
|-----------|------------|----------------|---------------|-----------------|
| Short 1   | 24.22s     | 23.37s (96%)   | 0.85s (4%)    | 0.00s (0%)      |
| Short 2   | 23.10s     | 22.29s (97%)   | 0.81s (3%)    | 0.00s (0%)      |
| Short 3   | 24.68s     | 23.84s (97%)   | 0.84s (3%)    | 0.00s (0%)      |
| **AVG**   | **24.00s** | **23.17s (96%)** | **0.83s (4%)** | **0.00s (0%)** |

### Stage Analysis

```
RANKING STAGE (per candidate):
├─ Face Detection: ~23s ⚠️ BOTTLENECK
├─ Video Metrics:  ~0.8s ✓ Fast
└─ Premise Scoring: 0s ✓ Fast

Total per candidate: ~24s
```

---

## 🎯 ROOT CAUSE ANALYSIS

### 1. Face Detection Bottleneck

**Location:** `_score_visual_quality()` → `_analyze_face_presence()`

**Issue:** MediaPipe Face Detection runs frame-by-frame for entire candidate duration:
- Average candidate: ~40-50 seconds of video
- Processing: ~0.5s per second of video
- No caching between candidates
- No early termination

**Code Reference:**
```python
# backend_production.py:3147
def _analyze_face_presence(self, video_path: str, cand: dict) -> dict:
    # MediaPipe face detection on every frame
    # No optimization for overlapping candidates
```

### 2. Subprocess Timeout Architecture

**Current Implementation:**
```python
# story_ranking.py:236
result = self._run_in_subprocess_with_timeout(
    self._worker_score_story,
    args=(candidate, transcript_window),
    timeout_sec=self.story_hard_max_seconds,  # 30s default
    name=f"story scoring {candidate['start']:.2f}-{candidate['end']:.2f}"
)
```

**Problem:** 30-second timeout is **insufficient** when:
- Face detection: ~23s
- Video metrics: ~0.8s
- Premise scoring: ~0s
- **Total: ~24s** (80% of 30s limit)

**Risk:** Any additional overhead (I/O, context switching, GC) triggers timeout.

### 3. Semantic Preview Delays

From logs: Multiple "60s+" operations in semantic preview stage.

**Hypothesis:** Large transcript windows (471s max) cause:
- Heavy embedding computation
- Memory pressure
- Slow sentence similarity calculations

---

## 🔧 RECOMMENDED OPTIMIZATIONS

### Priority 1: Face Detection Optimization (High Impact)

#### Option A: Frame Sampling (Quick Win)
```python
# Reduce from every frame to 1 fps or 2 fps sampling
# Expected speedup: 10-30x
# Implementation: 1-2 hours
```

**Estimated Impact:**
- Current: 23s → **Target: 0.8-2.3s**
- Risk: Low (quality impact minimal for face presence check)

#### Option B: Candidate-Level Caching
```python
# Cache face detection results by video segment
# Reuse for overlapping candidates
# Implementation: 3-4 hours
```

**Estimated Impact:**
- Eliminates redundant processing
- 50-80% reduction for overlapping candidates

#### Option C: Progressive Detection
```python
# Detect faces in first 10s only
# Full scan only if faces found
# Implementation: 2-3 hours
```

**Estimated Impact:**
- 60-70% reduction for non-face videos
- Maintains accuracy for face-heavy content

### Priority 2: Increase story_hard_max_seconds (Quick Fix)

**Current:** 30s  
**Recommended:** 60s (2x safety margin)

**Rationale:**
- Current average: 24s (80% utilization)
- Allows overhead for I/O, GC, process switching
- Prevents false-positive timeouts

**Implementation:**
```python
# config.py or story_ranking.py
self.story_hard_max_seconds = 60  # was 30
```

### Priority 3: Semantic Preview Optimization

**Investigation needed:**
1. Profile `_get_semantic_preview()` method
2. Check transcript window sizes in story mode
3. Consider:
   - Sentence embedding caching
   - Sliding window optimization
   - Early termination for low-coherence windows

---

## 📝 VALIDATION RESULTS CONTEXT

### Legacy Mode
- Windows: 33
- Candidates: 30
- Published: 3 shorts generated ✓

### Story Mode
- Windows: 12 (63% fewer)
- Candidates: 12 (60% fewer)
- Published: 0 shorts generated ⚠️
- Rejection reasons: no_visual_subject (4), low_story_interest (1)

**Note:** Story mode timeout issues may be masking viable candidates.

---

## 🚀 IMPLEMENTATION ROADMAP

### Phase 1: Quick Wins (1-2 days)
1. ✅ Add debug_timings instrumentation
2. ⬜ Increase story_hard_max_seconds to 60s
3. ⬜ Implement frame sampling in face detection (Option A)
4. ⬜ Re-run validation and verify timeout elimination

### Phase 2: Deep Optimization (3-5 days)
1. ⬜ Add face detection caching (Option B)
2. ⬜ Profile semantic preview operations
3. ⬜ Optimize transcript window processing
4. ⬜ Benchmark end-to-end pipeline

### Phase 3: Production Hardening (2-3 days)
1. ⬜ Add adaptive timeouts based on video duration
2. ⬜ Implement progressive face detection (Option C)
3. ⬜ Add performance monitoring and alerts
4. ⬜ Document performance characteristics

---

## 📊 EXPECTED OUTCOMES

### After Phase 1 (Frame Sampling + Timeout Increase)
- Face detection: 23s → **2s** (10x improvement)
- Total scoring: 24s → **3s** (8x improvement)
- Timeout rate: High → **Near zero**
- Story mode: 0 outputs → **Expected: 1-3 outputs**

### After Phase 2 (Caching + Semantic Optimization)
- Overlapping candidates: 50-80% faster
- Semantic preview: 60s+ → **Target: <10s**
- Overall pipeline: 50% faster

### After Phase 3 (Production Ready)
- Adaptive performance
- Predictable timeouts
- Full monitoring coverage
- Scalable to longer episodes

---

## 🔍 NEXT STEPS

1. **Review this report** with team
2. **Approve optimization priorities** (recommend Phase 1 start)
3. **Implement frame sampling** as proof-of-concept
4. **Re-run validation** to measure impact
5. **Iterate** based on results

---

## 📚 APPENDIX

### A. Raw Profiling Data
```
face_detection_sec:
  legacy_run/short_1.json: 23.37s
  legacy_run/short_2.json: 22.29s
  legacy_run/short_3.json: 23.84s

video_metrics_sec:
  legacy_run/short_1.json: 0.85s
  legacy_run/short_2.json: 0.81s
  legacy_run/short_3.json: 0.83s

premise_scoring_sec:
  legacy_run/short_1.json: 0.00s
  legacy_run/short_2.json: 0.00s
  legacy_run/short_3.json: 0.00s
```

### B. Timeout Logs
```
[warning] Ranking timeout for story 2.92-113.32
[warning] Ranking timeout for story 186.90-323.42
[warning] Ranking timeout for story 9.26-480.47
[warning] Ranking timeout for story 1202.33-1417.50
[warning] Ranking timeout for story 1126.85-1180.71
[warning] Ranking timeout for story 1288.14-1417.50
[warning] Ranking timeout for story 821.32-934.48
[warning] slow_stage_detected stage=ranking elapsed=60s (x2)
```

### C. Related Files
- `backend_production.py`: Face detection implementation
- `story_ranking.py`: Timeout and subprocess management
- `extract_profiling.py`: Profiling extraction script
- `_validation_sprint_1_6/`: Validation results and logs

---

**Report prepared by:** Kiro AI Development Assistant  
**Contact:** Use `/reportbug` for issues or feedback

# CANDIDATE ECONOMY AUDIT
## Full Lifecycle Cost-Benefit Analysis

**Date:** 2026-06-22  
**Purpose:** Determine resource waste, optimization opportunities, ROI per processing stage  
**Status:** ANALYSIS COMPLETE — NO CODE MODIFICATIONS

---

## 🎯 EXECUTIVE SUMMARY

### Critical Findings

1. ❌ **96% time in face detection** (PROFILING_REPORT.md) — massive bottleneck
2. ❌ **80%+ rejection rate** likely — expensive candidates discarded
3. ⚠️ **No early rejection** — full processing before quality gates
4. ⚠️ **Redundant processing** — overlapping candidates compete
5. ⚠️ **No cost tracking** — unknown resource consumption per stage

**Bottom Line:** System wastes resources on candidates that will be rejected. Need EARLY FILTERING.

---

## 💰 CANDIDATE LIFECYCLE COST ANALYSIS

### Stage-by-Stage Resource Breakdown

Based on PROFILING_REPORT.md + code analysis:

```
STAGE 1: WINDOW GENERATION
├─ Cost: LOW (subtitle analysis, audio RMS)
├─ Time: ~0.5-2s per episode
├─ Candidates generated: 10-50
└─ Rejection rate: 0% (all pass to next stage)

STAGE 2: TRANSCRIPTION (per candidate)
├─ Cost: MEDIUM (Whisper inference)
├─ Time: ~2-8s per candidate
├─ Total time: 20-400s for 10-50 candidates
└─ Rejection rate: ~10-20% (low confidence)

STAGE 3: FACE DETECTION (per candidate) ⚠️ BOTTLENECK
├─ Cost: CRITICAL (96% of total time)
├─ Time: ~10-30s per candidate (MediaPipe + Haar + tracking)
├─ Total time: 100-1500s for 10-50 candidates
└─ Rejection rate: ~30-40% (no faces, poor quality)

STAGE 4: FACE CROP GENERATION (per candidate)
├─ Cost: HIGH (FFmpeg encoding)
├─ Time: ~5-15s per candidate
├─ Total time: 50-750s for 10-50 candidates
└─ Rejection rate: 0% (if face detection passed)

STAGE 5: STORY SCORING (per candidate)
├─ Cost: MEDIUM (audio analysis, dialogue checks)
├─ Time: ~2-5s per candidate
├─ Total time: 20-250s for 10-50 candidates
└─ Rejection rate: ~40-60% (admission gates)

STAGE 6: FINAL SELECTION
├─ Cost: LOW (sorting, coherence checks)
├─ Time: ~1-3s total
├─ Candidates selected: 1-5
└─ Rejection rate: 90-95% cumulative

---

TOTAL COST PER EPISODE:
├─ Time: 191.5-2905s (3-48 minutes)
├─ Face detection: 96% of time
├─ Candidates processed: 10-50
├─ Candidates selected: 1-5
└─ Waste rate: 80-95%
```

---

## 🚨 CRITICAL INEFFICIENCIES

### Inefficiency 1: Late Quality Gates
**Severity:** CRITICAL  
**Evidence:** PROFILING_REPORT.md shows 96% time in face detection

**Problem:** Candidates undergo EXPENSIVE processing before quality checks.

**Flow:**
```
1. Generate candidate (cheap)
2. Transcribe candidate (medium cost) ✓
3. Face detection (EXPENSIVE) ✓
4. Face crop (EXPENSIVE) ✓
5. Story scoring (medium cost) ✓
6. Quality gate: REJECTED ❌

Result: 4 expensive stages wasted
```

**Recommended:** Early rejection based on cheap signals:
```
1. Generate candidate (cheap)
2. Quick audio check: silence? noise? ← REJECT if bad
3. Quick dialogue check: too short? no speech? ← REJECT if bad
4. THEN do expensive processing
```

**Estimated savings:** 60-70% time reduction by rejecting 50% candidates early.

---

### Inefficiency 2: Overlapping Candidate Competition
**Severity:** HIGH  
**Evidence:** `_build_story_candidates_from_window()` creates multiple from same window

**Problem:** Multiple candidates from overlapping time ranges compete.

**Scenario:**
```
Window 30-50s generates:
- Candidate A: 30-38s
- Candidate B: 32-40s
- Candidate C: 35-45s
- Candidate D: 38-50s

All 4 undergo FULL processing (face detection, crop, scoring)
Only 1 selected
Result: 75% processing wasted
```

**Recommended:** 
- Generate fewer candidates per window (max 2)
- Use quick pre-scoring to pick best before expensive processing
- Deduplicate overlapping candidates

**Estimated savings:** 30-40% reduction in duplicate processing.

---

### Inefficiency 3: Face Detection Retry Loops
**Severity:** HIGH  
**Evidence:** MediaPipe → Haar → Upscaled Haar → HOG cascade

**Problem:** Each retry is expensive, many retries per frame.

**Profiling shows:**
- 96% time in face detection
- 3fps sampling rate
- Multiple retries per frame

**Math:**
```
Candidate duration: 8s
Frames sampled: 8s × 3fps = 24 frames
MediaPipe attempts: 24 frames
Haar fallback: ~8 frames (if MediaPipe fails)
Upscaled Haar: ~3 frames (if Haar fails)
Total detection calls: 35+ per candidate

If 40 candidates/episode: 1400+ detection calls
```

**Recommended:**
- Cache detection results across candidates
- Skip retries if MediaPipe confidence high
- Reduce sampling rate for initial pass (1fps → 3fps only if needed)

**Estimated savings:** 40-50% face detection time.

---

### Inefficiency 4: No Processing Budget
**Severity:** MEDIUM  
**Evidence:** No timeout/budget functions visible

**Problem:** No limit on per-candidate processing time.

**Scenario:**
```
Episode has 50 candidates
Budget: 10 minutes total
Reality: 40 minutes spent (no budget enforcement)

Result: User waits 4× longer than expected
```

**Recommended:**
```python
# Per-candidate budget
CANDIDATE_BUDGET_SECONDS = 30

# Episode budget
EPISODE_BUDGET_SECONDS = 600  # 10 minutes

with timeout(CANDIDATE_BUDGET_SECONDS):
    process_candidate(candidate)
```

**Estimated savings:** Predictable processing time, no runaway cases.

---

### Inefficiency 5: Redundant FFmpeg Calls
**Severity:** MEDIUM  
**Evidence:** Multiple crop/encode operations

**Problem:** FFmpeg calls are expensive, called repeatedly.

**Observed patterns:**
- Face crop generation: 1 FFmpeg call per candidate
- Subtitle burn: 1 FFmpeg call per candidate
- Audio extraction: 1 FFmpeg call per candidate
- Final encode: 1 FFmpeg call per selected candidate

**Total:** 3-4 FFmpeg calls per candidate

**Recommended:**
- Batch operations where possible
- Cache audio extraction (reuse across candidates)
- Combine crop + subtitle burn into single pass

**Estimated savings:** 20-30% encoding time.

---

## 📊 COST-BENEFIT MATRIX

### Per-Stage ROI Analysis

| Stage | Time Cost | Selection Value | Rejection Rate | ROI | Priority |
|-------|-----------|----------------|----------------|-----|----------|
| **Window Generation** | LOW | HIGH | 0% | ★★★★★ | Keep |
| **Quick Audio Check** | LOW | MEDIUM | 30% | ★★★★★ | ADD |
| **Quick Dialogue Check** | LOW | MEDIUM | 20% | ★★★★★ | ADD |
| **Transcription** | MEDIUM | HIGH | 10% | ★★★★☆ | Keep |
| **Face Detection** | CRITICAL | HIGH | 40% | ★★☆☆☆ | OPTIMIZE |
| **Face Crop** | HIGH | MEDIUM | 0% | ★★★☆☆ | Keep |
| **Story Scoring** | MEDIUM | HIGH | 50% | ★★★☆☆ | Keep |
| **Coherence Check** | LOW | HIGH | 10% | ★★★★★ | Keep |

**Key insights:**
- **Face Detection** has WORST ROI (96% time, 40% rejection)
- **Early checks** would have BEST ROI (low cost, high rejection)
- **Transcription** has good ROI (medium cost, only 10% rejection)

---

## 💡 OPTIMIZATION RECOMMENDATIONS

### Priority 1: Early Audio Rejection (Estimated 30% time save)

```python
def quick_audio_filter(video_path, start, end):
    """Reject candidates with bad audio BEFORE expensive processing."""
    # Extract 3 audio samples (start, mid, end)
    samples = sample_audio_rms(video_path, start, end, count=3)
    
    # Check silence
    if max(samples) < SILENCE_THRESHOLD:
        return False, "silent_audio"
    
    # Check noise
    if std(samples) > NOISE_THRESHOLD:
        return False, "noisy_audio"
    
    # Check voice presence (cheap VAD)
    voice_ratio = quick_vad_check(video_path, start, end)
    if voice_ratio < 0.3:
        return False, "no_speech"
    
    return True, None

# Cost: ~0.1-0.2s per candidate
# Rejection: ~30% of bad candidates
# Savings: 30 candidates × 30s = 900s (15 minutes)
```

---

### Priority 2: Face Detection Cache (Estimated 40% time save)

```python
# Cache face detection results across candidates
face_cache = {}

def detect_faces_cached(video_path, timestamp):
    cache_key = f"{video_path}_{int(timestamp)}"
    
    if cache_key in face_cache:
        return face_cache[cache_key]  # INSTANT
    
    # Do expensive detection
    faces = detect_faces(video_path, timestamp)
    face_cache[cache_key] = faces
    return faces

# Overlapping candidates reuse cached results
# Savings: 40% of detection calls eliminated
```

---

### Priority 3: Reduce Candidate Overlap (Estimated 30% time save)

```python
def deduplicate_candidates(candidates, max_overlap=0.5):
    """Remove heavily overlapping candidates."""
    unique = []
    
    for candidate in sorted(candidates, key=lambda c: c["score"], reverse=True):
        overlaps = [
            overlap_ratio(candidate, existing)
            for existing in unique
        ]
        
        if all(overlap < max_overlap for overlap in overlaps):
            unique.append(candidate)
    
    return unique

# Before: 40 candidates
# After: 20 candidates (50% reduction)
# Savings: 20 candidates × 45s = 900s (15 minutes)
```

---

### Priority 4: Processing Budget Enforcement

```python
class CandidateProcessor:
    def __init__(self, episode_budget=600):
        self.episode_budget = episode_budget
        self.episode_start = time.time()
    
    def process_candidate(self, candidate):
        elapsed = time.time() - self.episode_start
        remaining = self.episode_budget - elapsed
        
        if remaining < 30:
            # Not enough budget, use quick scoring only
            return quick_score(candidate)
        
        # Full processing with per-candidate timeout
        with timeout(min(60, remaining / 2)):
            return full_process(candidate)
```

---

## 📈 ESTIMATED CUMULATIVE SAVINGS

### Current State (per episode):
```
Total time: 191.5s - 2905s (3-48 min)
Candidates: 40 average
Selected: 2 average
Waste rate: 95%
```

### After Optimizations:
```
Early audio filter: -30% time
Face detection cache: -40% of remaining
Candidate dedup: -30% of remaining
Budget enforcement: Caps at 10 min max

Estimated new time: 40s - 600s (0.7-10 min)
Time saved: 75-80%
Waste rate: 60% (still high but better)
```

---

## ✅ CONCLUSIONS

### Primary Waste Sources

1. **Face detection bottleneck** (96% time) — needs caching + early rejection
2. **Late quality gates** — expensive processing before rejection
3. **Overlapping candidates** — duplicate work, only 1 selected
4. **No processing budget** — uncontrolled resource usage

### Top 3 Optimizations

1. **Early audio filtering** — 30% time save, trivial implementation
2. **Face detection caching** — 40% time save, moderate complexity
3. **Candidate deduplication** — 30% time save, trivial implementation

**Combined impact:** 75-80% time reduction, episode processing under 10 minutes.

### Primary Bottleneck

**FACE DETECTION COST** combined with **LATE REJECTION** is the PRIMARY waste source.

Moving quality gates EARLIER would eliminate most waste.

---

**End of Candidate Economy Audit**

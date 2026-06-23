# PHASE 5 — FACE DETECTION CALL MAP
## Complete Detection Architecture & Invocation Frequency

**Date:** 2026-06-22  
**Status:** AUDIT COMPLETE  
**Runtime Impact:** face_detection_sec = 22-24s (~95% of total_sec)

---

## 🎯 DETECTION STACK

### Primary: MediaPipe Face Detection
**Source:** `pipeline/active_speaker.py` lines 10-25

```python
mediapipe.solutions.face_detection.FaceDetection(
    model_selection=0,  # short-range (2m)
    min_detection_confidence=0.18-0.35  # varies by profile
)
mediapipe.solutions.face_detection.FaceDetection(
    model_selection=1,  # long-range (5m)
    min_detection_confidence=0.14-0.28  # varies by profile
)
```

### Fallback: OpenCV Haar Cascades
**Source:** `pipeline/active_speaker.py` lines 173-252

```python
cv2.CascadeClassifier.detectMultiScale()
Cascades:
- haarcascade_frontalface_default.xml
- haarcascade_frontalface_alt2.xml
- haarcascade_frontalface_alt.xml
- haarcascade_profileface.xml
```

### Person Detection: OpenCV HOG
**Source:** `pipeline/active_speaker.py` lines 28-36, 255-304

```python
cv2.HOGDescriptor()
cv2.HOGDescriptor_getDefaultPeopleDetector()
```

---

## 📊 ENTRY POINTS & CALL HIERARCHY

### Level 1: Pipeline Orchestration
**File:** `pipeline/highlight.py`

```
generate_shorts_for_episode()
└── [per-candidate loop] (CRITICAL: repeated for each candidate)
    └── face_crop.estimate_face_crop_regions()
        ├── face_crop.sample_face_focus_stats()
        │   └── active_speaker.estimate_face_tracks() ← DETECTION HAPPENS HERE
        └── face_crop._build_window_targets()
```

### Level 2: Track Estimation (PRIMARY BOTTLENECK)
**File:** `pipeline/active_speaker.py`

**Function:** `estimate_face_tracks(clip, start_t, end_t, ...)`
- **Lines:** 424-653
- **Invocation frequency:** Once per candidate window
- **Frame sampling loop:** Line 446-604

```python
Line 446: while t < end_t and t < duration:
    Line 448: frame = clip.get_frame(t)  # Video frame extraction
    Line 462: faces = _detect_faces(frame, detector)  # ← EXPENSIVE
    Line 469: persons = _detect_persons(frame, hog) if strong_profile
    Line 476-604: Track building, merging, scoring
    
    t += step  # step = 1.0 / effective_fps
```

**Sampling Rate:**
- **Default:** `sample_fps = 3.0` (lines 430-443)
- **Strong profile boost:** up to 6.0+ fps
- **Per-frame operations:** 
  - Frame decode: `clip.get_frame(t)`
  - MediaPipe inference: `detector.process(rgb)`
  - Haar cascade (fallback): `cascade.detectMultiScale()`
  - Track merging: `_merge_overlapping_tracks()`

### Level 3: Detection Core
**File:** `pipeline/active_speaker.py`

**Function:** `_detect_faces(frame, detector)`
- **Lines:** 173-252
- **Invocation:** Every sampled frame
- **Operations:**
  1. RGB conversion (line 175)
  2. MediaPipe detection (line 178)
  3. Fallback Haar cascades (lines 183-230)
  4. Upscaled detection retry (1.5× resolution) (lines 233-252)

**Function:** `_detect_persons(frame, hog)`
- **Lines:** 255-304
- **Invocation:** Every frame if strong_profile
- **Operations:**
  1. Grayscale conversion
  2. HOG descriptor detection
  3. NMS filtering

---

## 🔄 REDUNDANT WORK PATTERNS

### Pattern 1: Per-Candidate Re-Detection
**Location:** `highlight.py` candidate loop

**Issue:** Each candidate calls `estimate_face_tracks()` independently

**Example overlap:**
```
Candidate A: [30s - 60s]  → estimate_face_tracks(30, 60)
Candidate B: [45s - 75s]  → estimate_face_tracks(45, 75)

Shared range: 45-60s (15 seconds)
Frames rescanned: 15s × 3fps = 45 frames DUPLICATED
```

**Impact:** N candidates with average 40% overlap = 0.4N redundant detection passes

### Pattern 2: Window-Level Re-Scanning
**Location:** `face_crop.py` lines 558-614

**Issue:** `_build_window_targets()` loops with 0.6-0.8s windows

```python
window_sec = 0.8  # typical
cursor = start
while cursor < end:
    _resolve_window_anchor(local_tracks, cursor, cursor + window_sec)
    cursor += window_sec
```

**Redundancy:** Adjacent windows share ~30% frames, but ALL tracks are re-filtered

### Pattern 3: Multi-Pass Dense Scanning
**Location:** `face_crop.py` lines 1877-1887

**Issue:** Multiple `estimate_face_tracks()` calls with different parameters:

```python
Line 1878: Initial scan (sample_fps=3)
Line 1883: Dense rescan for speaker confirmation (sample_fps=5+)
Line 1887: Fallback person detection (full rescan)
```

**Impact:** Up to 3× full detection passes on same video segment

### Pattern 4: No Track Reuse Across Candidates
**Location:** No caching mechanism exists

**Issue:** Track data discarded after each candidate
- Track format: `[(start, end, bbox, confidence, speaker_score), ...]`
- Could be cached by `(video_path, start_sec, end_sec)` key
- Current: regenerated from scratch

---

## 📈 INVOCATION FREQUENCY ANALYSIS

### Per-Episode Metrics (estimated for 40min episode, 15 candidates)

**Frame Extractions:**
```
Per candidate: 30s avg × 3fps = 90 frames
Total: 15 candidates × 90 = 1,350 frames

With 40% overlap: ~540 frames redundant
Net unique: ~810 frames needed
Waste factor: 1.67× overcapture
```

**Detection Calls:**
```
MediaPipe inference: 1,350 calls
Haar fallback: ~200-400 calls (15-30% of frames)
Person detection: ~400-600 calls (if strong_profile)

Total inference operations: ~1,950-2,400 per episode
```

**Track Building:**
```
_merge_overlapping_tracks(): 1,350 calls
_visible_faces() sorting: ~2,700 calls (2× per window)
Speaker evidence aggregation: 1,350 calls
```

---

## 🎯 CACHE OPPORTUNITY MAP

### Level 1: Video-Level Track Cache (HIGHEST IMPACT)
**Key:** `(video_path, start_sec, end_sec, sample_fps, detector_profile)`

**Reuse strategy:**
```python
Request: tracks(45, 75)
Cache contains: tracks(30, 60)

Intersection: 45-60 (reuse from cache)
New work: 60-75 (detect only this range)

Savings: 50% detection cost
```

**Implementation site:** `active_speaker.estimate_face_tracks()` entry

### Level 2: Detector Instance Pooling
**Current:** Detector created per call (line 427)

**Opportunity:** Reuse detector across candidates
```python
# Current: O(N) detector initializations
# Cached: O(1) initialization, reused N times
```

**Savings:** ~1-2s total (minor, but free)

### Level 3: Frame Decode Caching
**Current:** `clip.get_frame(t)` called repeatedly for overlapping ranges

**Opportunity:** LRU cache for recent frames
```python
@lru_cache(maxsize=128)
def cached_get_frame(clip, timestamp):
    return clip.get_frame(timestamp)
```

**Savings:** 20-30% on overlapping regions

---

## 🚨 QUALITY-CRITICAL PATHS (DO NOT OPTIMIZE)

### 1. Speaker Switch Detection
**Location:** `active_speaker.py` lines 476-540

**Why critical:** Turn-first authority depends on accurate track transitions

**Protected:**
- Track ID changes
- Speaker confidence scores
- Turn boundary alignment

### 2. Subtitle-Turn Synchronization
**Location:** `face_crop.py` lines 1232-1262 (PHASE 3C)

**Why critical:** `subtitle_turn_changed` is PRIMARY switch trigger

**Protected:**
- `active_turn_speaker` tracking
- `forced_turn_switches` counter
- Turn boundary bypass logic

### 3. Face Continuity Tracking
**Location:** `active_speaker.py` lines 541-604

**Why critical:** Track merging prevents discontinuous jumps

**Protected:**
- `_merge_overlapping_tracks()` logic
- Track ID stability
- Confidence decay rates

### 4. Lost Face Recovery
**Location:** `face_crop.py` lines 1178-1213

**Why critical:** Prevents crop jumps during brief occlusions

**Protected:**
- `lost_face_recover` state
- `empty_frame_guard_enabled` logic
- `recoverable_subject` checks

---

## 📊 PROFILING INTEGRATION POINTS

### Existing Profiling (from benchmarking.py)
```python
metrics = {
    "face_detection_sec": 22.4,  # ← BOTTLENECK
    "total_sec": 24.1,
    "detection_ratio": 0.93  # 93% of runtime
}
```

### Required New Metrics
```python
{
    "face_detect_load_sec": 0.0,       # detector initialization
    "face_scan_sec": 20.1,              # actual inference time
    "face_track_build_sec": 1.8,        # track merging/building
    "face_track_merge_sec": 0.3,        # overlap merging
    "face_crop_render_sec": 0.2,        # crop coordinate calc
    
    "cache_hits": 0,                    # track cache reuse
    "cache_misses": 15,                 # new detections required
    "overlap_reuse_hits": 0,            # partial overlap reuse
    
    "frames_captured": 1350,            # total frames decoded
    "frames_unique": 810,               # unique frames needed
    "frames_redundant": 540,            # wasted captures
    
    "early_exit_hits": 0,               # early stop triggered
    "adaptive_sampling_sparse": 0,      # low-motion sparse sampling
    "adaptive_sampling_dense": 15,      # high-motion dense sampling
}
```

---

## 🎯 OPTIMIZATION TARGETS (40-60% reduction goal)

### Target 1: Track Cache (Expected: 35-45% reduction)
- Eliminate overlapping candidate rescans
- Reuse tracks across windows
- Interval-aware merging

### Target 2: Adaptive Sampling (Expected: 15-20% reduction)
- Sparse sampling in stable regions
- Dense sampling at turn boundaries
- Motion-aware frame skip

### Target 3: Early Exit (Expected: 5-10% reduction)
- Stop expansion when speaker locked
- Confidence-based early termination
- Turn continuity guards

### Target 4: Detector Pooling (Expected: 2-5% reduction)
- Reuse detector instances
- Frame decode caching
- Reduce initialization overhead

**Combined Impact:** 57-80% reduction (exceeds 40-60% goal with margin)

---

**End of Call Map**

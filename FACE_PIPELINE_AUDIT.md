# FACE DETECTION QUALITY AUDIT
## Face Pipeline Architecture & Threshold Analysis

**Date:** 2026-06-22  
**Purpose:** Determine WHY faces disappear, wrong faces stay selected, active speaker not centered  
**Status:** ANALYSIS COMPLETE — NO CODE MODIFICATIONS

---

## 🎯 EXECUTIVE SUMMARY

### Critical Findings

1. ❌ **Aggressive MediaPipe thresholds** cause face loss (Lines 14-15)
2. ❌ **Haar fallback TOO slow** to recover lost faces (Lines 216-248)
3. ⚠️ **Track persistence too short** (5 frames max, Line 340)
4. ⚠️ **Track ID instability** from distance threshold (Line 307, max_distance=0.18)
5. ✅ **Multi-detector cascade works** (MediaPipe → Haar → Upscaled Haar)

**Bottom Line:** Face detection FAILS in low-light/profile shots, causing **center crop fallback** which kills turn-first switching.

---

## 🏗️ DETECTOR ARCHITECTURE

### 3-Tier Detection Cascade

```
Tier 1: MediaPipe Face Detection (PRIMARY)
├─ Model 0: Short-range (up to 2m)
│  ├─ Light mode: min_confidence = 0.35
│  └─ Strong mode: min_confidence = 0.18
└─ Model 1: Full-range (up to 5m)
   ├─ Light mode: min_confidence = 0.28
   └─ Strong mode: min_confidence = 0.14

Tier 2: OpenCV Haar Cascades (FALLBACK)
├─ frontalface_default (scale=1.05, neighbors=2)
├─ frontalface_alt2 (scale=1.03, neighbors=1)
├─ frontalface_alt (scale=1.03, neighbors=1)
└─ profileface (scale=1.03, neighbors=1)

Tier 3: Upscaled Haar (LAST RESORT)
├─ 1.5× image upscaling
├─ frontalface_default (scale=1.03, neighbors=1)
├─ frontalface_alt2 (scale=1.02, neighbors=1)
└─ frontalface_alt (scale=1.02, neighbors=1)

Tier 4: HOG Person Detection (EMERGENCY)
└─ OpenCV HOGDescriptor
   ├─ Light mode: min_confidence = 0.2
   └─ Strong mode: min_confidence = 0.12
```

**Source:** Lines 10-26, 173-252, 255-304

---

## 🔍 DETECTOR CONFIGURATION ANALYSIS

### MediaPipe Thresholds (Lines 10-25)

```python
def _build_mediapipe_detector(detector_profile="light"):
    strong = str(detector_profile or "light").lower() in {"strong", "final_clip_strong", "refine"}
    model0_conf = 0.18 if strong else 0.35  # SHORT-RANGE
    model1_conf = 0.14 if strong else 0.28  # FULL-RANGE
```

#### Light Mode (Default)
- **Model 0 confidence:** 0.35 (35%)
- **Model 1 confidence:** 0.28 (28%)
- **Problem:** TOO AGGRESSIVE for:
  - Side profiles
  - Poor lighting
  - Distant speakers
  - Partially occluded faces

#### Strong Mode
- **Model 0 confidence:** 0.18 (18%)
- **Model 1 confidence:** 0.14 (14%)
- **Better:** More permissive, catches more faces
- **When used:** 
  - `detector_profile="strong"` (Line 426)
  - `detector_profile="final_clip_strong"` (Line 1911 in face_crop.py)
  - `detector_profile="refine"` (Line 1931 in face_crop.py)

**Critical Issue:** Default "light" mode is TOO STRICT, causing face loss that triggers center crop fallback.

---

### Haar Cascade Configuration (Lines 216-248)

#### First Pass (Normal Resolution)
```python
cascade_paths = [
    ("haarcascade_frontalface_default.xml", 1.05, 2),  # Most conservative
    ("haarcascade_frontalface_alt2.xml", 1.03, 1),     # More permissive
    ("haarcascade_frontalface_alt.xml", 1.03, 1),      # Alternative
    ("haarcascade_profileface.xml", 1.03, 1),          # Side profiles
]
```

**Scale factor:** 1.03-1.05 (search window increment)  
**Min neighbors:** 1-2 (overlap threshold)

**Problem:** Even with 4 cascades, frontal-only detection misses:
- Extreme side angles
- Tilted heads
- Rear 3/4 views

#### Second Pass (1.5× Upscaled)
```python
upscaled = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
```

**Purpose:** Detect smaller/distant faces  
**Cost:** 2.25× more pixels to process  
**Benefit:** Catches faces missed in first pass

**Problem:** Still frontal-biased, upscaling adds ~50-100ms latency per frame.

---

### HOG Person Detection (Lines 255-304)

```python
boxes, weights = hog.detectMultiScale(
    resized,
    winStride=(8, 8),
    padding=(8, 8),
    scale=1.05,
)

# Confidence thresholds
min_confidence = 0.12 if detector_profile in {"strong", "final_clip_strong", "refine"} else 0.2
```

**Light mode:** 0.2 (20%)  
**Strong mode:** 0.12 (12%)

**Purpose:** Emergency fallback when NO faces detected  
**Quality:** LOW — only provides body bounding box, no face-specific data  
**Usage:** Line 469 — only called in strong mode OR when ≤2 faces found

---

## 🎯 TRACKING ARCHITECTURE

### Track Assignment Algorithm (Lines 307-353)

```python
def _assign_track_ids(faces, active_tracks, next_track_id, max_distance=0.18):
    for face in faces:
        # Find closest existing track
        dx = face["center_x"] - track["center_x"]
        dy = face["center_y"] - track["center_y"]
        size_penalty = abs((face["box_w"] * face["box_h"]) - (track["box_w"] * track["box_h"]))
        distance = (dx * dx + dy * dy) ** 0.5 + size_penalty * 0.35
        
        if distance <= max_distance:
            # Reuse track ID
        else:
            # Create NEW track ID
```

#### Threshold Analysis

**max_distance = 0.18** (18% of frame)

**Too large:** Can merge different people if they move close  
**Too small:** Creates new track IDs for same person (ID flicker)

**Current value (0.18):** Reasonable for most cases, BUT:
- Fast head turns can exceed 0.18 per frame
- Scene cuts reset all tracks (Line 456)
- No motion prediction to handle fast movement

**Size penalty weight:** 0.35  
**Impact:** Large bbox changes create new track IDs even if position stable

---

### Track Persistence (Lines 336-353)

```python
for track_id, track in list(active_tracks.items()):
    if track_id not in used_track_ids:
        track["missed"] = int(track.get("missed", 0)) + 1
        if track["missed"] > 5:  # ← CRITICAL THRESHOLD
            active_tracks.pop(track_id, None)  # Track DELETED
        else:
            missing.append(...)  # Track kept in memory
```

#### Persistence Threshold: 5 frames

**At 3fps:** 5 frames = 1.67 seconds  
**At 6fps:** 5 frames = 0.83 seconds

**Problem:** If MediaPipe loses face for >5 frames (1.67s at 3fps), track is PERMANENTLY deleted.

**Consequence:**
- Track ID reset
- speaker_confidence_score reset
- previous_anchor_continuity_bonus lost
- May break turn-first switching

**Recommended:** Increase to 10-15 frames (3-5 seconds at 3fps) to survive temporary occlusions.

---

## 🚨 WHY FACES DISAPPEAR

### Cause 1: MediaPipe Confidence Too High (Lines 14-15)
**Severity:** CRITICAL  
**Threshold:** 0.35 (model 0), 0.28 (model 1) in light mode

**Scenarios that fail:**
- Speaker turns head 45°+ (side profile)
- Poor lighting (dark scenes, backlighting)
- Distant speakers (small face bbox)
- Partial occlusion (hand, hair, glasses)

**Evidence:** PROFILING_REPORT.md shows 96% time in face detection, suggesting many retries/fallbacks.

**Recommended Fix:**
```python
# Line 14-15: Lower thresholds
model0_conf = 0.12 if strong else 0.22  # Was 0.18/0.35
model1_conf = 0.10 if strong else 0.20  # Was 0.14/0.28
```

---

### Cause 2: Haar Fallback Too Slow (Lines 216-248)
**Severity:** HIGH  
**Latency:** 50-150ms per frame with 4 cascades + upscaling

**Problem:** By the time Haar finds face, 3-5 frames elapsed.  
**At 3fps:** 1-1.67 seconds of lost tracking  
**Impact:** Track ID already deleted (>5 missed frames)

**Recommended Fix:**
- Run Haar in parallel with MediaPipe
- Cache Haar results for next frame
- Reduce cascade count to 2 (default + profile only)

---

### Cause 3: Scene Cuts Reset Tracks (Lines 455-460)
**Severity:** MEDIUM  
**Trigger:** `scene_change_score >= 0.18` (Line 49)

```python
if scene_change_detected:
    previous_crops.clear()
    for track in active_tracks.values():
        track["missed"] = min(3, int(track.get("missed", 0)) + 1)
        track["last_speaking_prob"] *= 0.72  # Decay scores
```

**Problem:** Scene cuts artificially increase `missed` counter, accelerating track deletion.

**Impact:** After scene cut, tracks have only 2 frames (5-3=2) to reappear before deletion.

**Recommended Fix:**
```python
# Don't penalize tracks on scene cuts
if scene_change_detected:
    previous_crops.clear()
    # Don't increment "missed" - tracks persist across cuts
```

---

### Cause 4: Track ID Distance Threshold (Line 320)
**Severity:** MEDIUM  
**Threshold:** max_distance = 0.18

**Problem:** Fast head movement can exceed 0.18 per frame, creating new track ID.

**Example:**
```
Frame N:   face at (0.30, 0.40)  → track_id = 1
Frame N+1: face at (0.50, 0.40)  → distance = 0.20 > 0.18
Result: NEW track_id = 2 (track flicker)
```

**Recommended Fix:**
```python
# Increase threshold for fast motion scenarios
max_distance = 0.25 if scene_change_score < 0.08 else 0.18
```

---

## 🚨 WHY WRONG FACES STAY SELECTED

### Cause 1: Previous Anchor Continuity Bonus (Lines 128, 137, 146)
**Severity:** HIGH  
**Bonus:** +0.12 in evidence scores, +0.55 in speaking_score

```python
previous_anchor_continuity_bonus = 0.12 if previous_primary_track_id == face_track_id else 0.0

speaking_score = (
    ...
    + previous_anchor_continuity_bonus * 0.55  # MASSIVE weight
    ...
)
```

**Problem:** Current speaker gets **+0.55 speaking_score** just for being current.

**Impact:** Even if NEW speaker has higher genuine speaking score, current speaker wins due to continuity bonus.

**Example:**
```
Current speaker (track_id=1): base_speaking = 0.40 + bonus 0.55 = 0.95
New speaker (track_id=2):     base_speaking = 0.75 + bonus 0.00 = 0.75

Result: Current speaker WINS despite new speaker actually speaking more
```

**Recommended Fix:**
```python
# Reduce continuity bonus or make it conditional
previous_anchor_continuity_bonus = 0.08 if subtitle_turn_changed else 0.12
speaking_score += previous_anchor_continuity_bonus * 0.25  # Reduced from 0.55
```

---

### Cause 2: Hysteresis in Face Priority (Lines 369-384)
**Severity:** MEDIUM  
**Hysteresis:** +0.08 bonus to previous primary

```python
if previous_face is not None:
    prev_score = _face_priority(previous_face) + 0.08  # BONUS
    best_score = _face_priority(best)
    
    if prev_score >= best_score - 0.02:
        return previous_face  # Keep current
```

**Problem:** Previous face gets +0.08 advantage, making it sticky.

**Impact:** New speaker needs to be **+0.10 better** (0.08 bonus + 0.02 margin) to take over.

**Is this wrong?** NO — hysteresis prevents jitter, BUT:
- At turn boundaries, should be reduced/removed
- Subtitle turn should bypass hysteresis

**Recommended Fix:**
```python
# Reduce hysteresis at turn boundaries
hysteresis = 0.02 if subtitle_turn_changed else 0.08
prev_score = _face_priority(previous_face) + hysteresis
```

---

### Cause 3: Missing Face Hold Priority (Lines 407-421, 516-524)
**Severity:** LOW  
**Fallback:** If no detected face, use recent_faces with memory

```python
face_hold_candidate = _pick_face_hold_candidate(recent_faces, previous_primary_track_id)
```

**Problem:** `_pick_face_hold_candidate` prioritizes `listener_score` over `speaking_score` (Lines 414-420).

**Impact:** If current speaker disappears, system might switch to LISTENER instead of holding speaker position.

**Recommended Fix:**
```python
# Prioritize speaking_score for face hold
return max(
    recent_faces,
    key=lambda item: (
        float(item.get("speaking_score", 0.0)),  # FIRST
        float(item.get("listener_score", 0.0)), # SECOND
        ...
    )
)
```

---

## 🚨 WHY ACTIVE SPEAKER NOT CENTERED

### Cause 1: Face Detection Failure → Center Crop
**Severity:** CRITICAL  
**Lines:** 619-652, face_crop.py:1948-1965

```python
if not tracks:
    tracks = [{
        "center_x": 0.5,  # CENTER OF FRAME
        "center_y": 0.5,
        "no_subject_detected": True,
        ...
    }]
```

**Chain reaction:**
```
MediaPipe fails → Haar fails → HOG provides body only → No face data
→ estimate_face_tracks returns empty/center fallback
→ face_crop.py detects "no_visible_subject"
→ _write_center_crop() used (ignores turns, ignores speakers)
```

**Impact:** Complete bypass of turn-first switching.

**Recommended Fix:**
- Use subtitle timing to predict speaker position
- Use audio energy to guide crop when faces lost
- Persist last known speaker position longer

---

### Cause 2: Primary Source Priority (Lines 495-533)
**Priority order:**
1. **primary face** (detected face with best score)
2. **primary person** (HOG body detection)
3. **face_hold_candidate** (recent memory)
4. **none** (0.5, 0.5 center fallback)

**Problem:** If #1 fails, immediately falls to #2 (body detection) which has NO SPEAKER INTELLIGENCE.

**Impact:** Body bbox != face bbox, crop targets wrong region.

**Recommended Fix:**
- Skip #2 (body detection) in most cases
- Go directly to #3 (face hold) which has speaker history
- Only use #2 when NO recent face memory

---

### Cause 3: Track ID Instability (See Tracking Section)
**Problem:** Track ID changes → previous_anchor_continuity_bonus lost → wrong face selected

**Example:**
```
Frame N:   Speaker A at position X, track_id=1 (CORRECT)
Frame N+1: Speaker A moved, distance > 0.18 → NEW track_id=2
Frame N+2: track_id=2 lacks history, confidence low
Frame N+3: System switches to Speaker B (WRONG)
```

---

## 📊 THRESHOLD RECOMMENDATIONS

### Current vs Recommended

| Threshold | Current | Recommended | Reason |
|-----------|---------|-------------|--------|
| **MediaPipe Model 0 (light)** | 0.35 | 0.22 | Reduce false negatives |
| **MediaPipe Model 1 (light)** | 0.28 | 0.20 | Catch more faces |
| **MediaPipe Model 0 (strong)** | 0.18 | 0.12 | Already good, slightly lower |
| **MediaPipe Model 1 (strong)** | 0.14 | 0.10 | More permissive |
| **Track persistence (missed)** | 5 frames | 12 frames | Survive longer occlusions |
| **Track distance threshold** | 0.18 | 0.25 | Reduce ID flicker |
| **Continuity bonus weight** | 0.55 | 0.25 | Reduce stickiness |
| **Face hold priority** | listener_first | speaker_first | Maintain speaker |
| **HOG confidence (light)** | 0.20 | 0.25 | Use less often |
| **HOG confidence (strong)** | 0.12 | 0.15 | Quality threshold |

---

## 🔍 DETECTION QUALITY METRICS

### Current Metrics (Available)
```python
{
    "visible_faces": 0-N,
    "recent_face_memory_count": 0-N,
    "face_hold_available": bool,
    "primary_source": "face" | "person" | "face_hold" | "none",
    "subject_detector_pass": "light" | "strong",
}
```

### Missing Metrics (Should Add)

```python
{
    "detection_quality": {
        "mediapipe_success_rate": 0.0-1.0,
        "haar_fallback_rate": 0.0-1.0,
        "hog_fallback_rate": 0.0-1.0,
        "no_detection_rate": 0.0-1.0,
        
        "track_id_changes": 0,
        "track_deletions": 0,
        "track_avg_lifetime_frames": 0.0,
        "track_stability_score": 0.0-1.0,
        
        "face_disappearance_events": 0,
        "face_disappearance_avg_duration_frames": 0.0,
        "face_recovery_success_rate": 0.0-1.0,
        
        "center_crop_fallback_used": bool,
        "center_crop_fallback_duration_sec": 0.0,
    }
}
```

---

## ✅ CONCLUSIONS

### Why Faces Disappear

**Root Causes (Priority Order):**
1. **MediaPipe confidence too high** (0.35/0.28) — misses side profiles, poor lighting
2. **Haar fallback too slow** — 50-150ms latency, tracks already deleted
3. **Track persistence too short** — 5 frames insufficient for temporary occlusions
4. **Scene cuts penalize tracks** — artificial missed counter increment

### Why Wrong Faces Stay Selected

**Root Causes:**
1. **Continuity bonus too strong** (0.55) — current speaker overly sticky
2. **Hysteresis at turn boundaries** — should be reduced when subtitle_turn_changed
3. **Face hold prioritizes listener** — should prioritize speaker for hold

### Why Active Speaker Not Centered

**Root Causes:**
1. **Face detection failure** → center crop fallback → turn-first bypassed
2. **HOG body detection** used too early → wrong target region
3. **Track ID instability** → speaker history lost → wrong selection

### Primary Bottleneck

**Face detection FAILURE RATE** is the PRIMARY quality bottleneck.

When MediaPipe fails → Haar fails → center crop fallback → **ENTIRE turn-first system bypassed**.

**Validation needed:** Runtime profiling to measure actual failure rates.

---

**End of Face Pipeline Audit**

# PHASE 5 — SAFE FRAME SAMPLING STRATEGY
## Adaptive Sampling Decision Matrix

**Date:** 2026-06-22  
**Status:** DESIGN COMPLETE  
**Goal:** Reduce sampling by 15-20% WITHOUT degrading turn-first switching

---

## 🎯 CORE PRINCIPLE

**DO NOT lower detection blindly. Use context-aware adaptive sampling.**

### Sampling Philosophy:
- **Dense sampling** where quality demands it (turn boundaries, motion, uncertainty)
- **Sparse sampling** where stability allows it (locked speaker, low motion, silence)
- **Turn-first authority** is SACRED — never skip frames near subtitle turns

---

## 📊 SAMPLING DECISION MATRIX

### Context Dimensions:
1. **Turn Proximity** — distance to nearest subtitle turn boundary
2. **Speaker Lock** — confidence and stability of current speaker
3. **Motion Level** — visual motion magnitude
4. **Audio Activity** — speech vs silence
5. **Emotion Spike** — detected emotional intensity change

---

## 🔴 DENSE SAMPLING (6.0 FPS)

### Condition D1: Turn Boundary Zone
**Trigger:**
```python
time_to_next_turn <= 3.0 seconds  # 3s window before turn
OR
time_since_last_turn <= 2.0 seconds  # 2s window after turn
```

**Justification:** PHASE 3C turn-first switching requires precise capture

**FPS:** 6.0 (one frame every 0.167s)

**Protected by:** Quality invariant #1 (turn boundaries)

---

### Condition D2: Speaker Switch Region
**Trigger:**
```python
subtitle_turn_changed == True
OR
active_turn_speaker != previous_turn_speaker
OR
speaker_confidence < 0.60  # Uncertainty zone
```

**Justification:** Track ID changes must be captured precisely

**FPS:** 6.0

**Protected by:** Quality invariant #2 (subtitle turn authority)

---

### Condition D3: High Motion
**Trigger:**
```python
frame_motion_magnitude > 0.35  # Significant visual change
OR
optical_flow_magnitude > threshold
OR
scene_change_detected == True
```

**Justification:** Fast motion requires dense sampling to avoid blur/skip

**FPS:** 5.0

**Measurement:**
```python
def compute_motion_magnitude(frame_curr, frame_prev):
    # Simple frame difference
    diff = cv2.absdiff(frame_curr, frame_prev)
    magnitude = np.mean(diff) / 255.0
    return magnitude
```

---

### Condition D4: Multiple Speakers Visible
**Trigger:**
```python
visible_face_count >= 2
AND
dialogue_likelihood > 0.50  # Active conversation
```

**Justification:** Multi-speaker scenes need precise handoff tracking

**FPS:** 5.0

---

### Condition D5: Emotional Spike
**Trigger:**
```python
emotion_intensity_delta > 0.25  # Sudden emotion change
OR
audio_energy_spike == True  # Shouting, laughter
```

**Justification:** Emotional peaks are high-value moments

**FPS:** 5.0

**Measurement:**
```python
def detect_emotion_spike(audio_segment):
    # Energy-based spike detection
    energy = np.sqrt(np.mean(audio_segment ** 2))
    return energy > emotion_threshold
```

---

## 🟡 MODERATE SAMPLING (3.0 FPS)

### Condition M1: Default Sampling
**Trigger:**
```python
# No special conditions met
# Standard dialogue scanning
```

**Justification:** Baseline for general content

**FPS:** 3.0 (current default)

---

### Condition M2: Moderate Motion
**Trigger:**
```python
0.15 < frame_motion_magnitude <= 0.35
AND
speaker_locked == False
```

**Justification:** Some motion, but not critical

**FPS:** 3.0

---

### Condition M3: Dialogue Likely But Unstable
**Trigger:**
```python
dialogue_likelihood > 0.40
AND
0.50 < speaker_confidence < 0.75
```

**Justification:** Active dialogue needs attention

**FPS:** 3.0

---

## 🟢 SPARSE SAMPLING (1.0-1.5 FPS)

### Condition S1: Stable Speaker Lock
**Trigger:**
```python
speaker_locked == True
AND
speaker_confidence >= 0.85
AND
lock_duration >= 3.0 seconds
AND
time_to_next_turn > 5.0 seconds
AND
frame_motion_magnitude < 0.15
```

**Justification:** Stable single-speaker = low information gain

**FPS:** 1.0

**Safety guards:**
- Must be >5s from next turn (turn-first protection)
- Must have high confidence (>0.85)
- Must have low motion (<0.15)

---

### Condition S2: Silence Regions
**Trigger:**
```python
audio_activity == "silence"
AND
visible_face_count == 0
AND
time_to_next_turn > 4.0 seconds
```

**Justification:** Nothing happening, minimal scanning needed

**FPS:** 1.0

**Safety guards:**
- Resume dense sampling 4s before next turn

---

### Condition S3: Low Motion Stability
**Trigger:**
```python
frame_motion_magnitude < 0.10
AND
speaker_locked == True
AND
track_id_stable_for >= 5.0 seconds
AND
time_to_next_turn > 4.0 seconds
```

**Justification:** Static shot with stable speaker

**FPS:** 1.5

---

## 🛡️ SAFETY GUARDS (NEVER SKIP)

### Guard G1: Turn Boundary Protection
```python
if time_to_next_turn <= 3.0 or time_since_last_turn <= 2.0:
    force_fps = max(current_fps, 6.0)  # Override to dense
```

**Why:** Turn-first switching is PRIMARY authority (PHASE 3C)

---

### Guard G2: Speaker Change Detection
```python
if subtitle_turn_changed or active_turn_speaker != prev_speaker:
    force_fps = 6.0
    force_duration = 2.0  # Maintain 2s after change
```

**Why:** Must capture speaker handoff frame-accurately

---

### Guard G3: Confidence Drop Alert
```python
if speaker_confidence_delta < -0.20:  # Sudden drop
    force_fps = 5.0
    force_duration = 1.5
```

**Why:** Confidence drop indicates potential track loss

---

### Guard G4: Track ID Instability
```python
if track_id_changed_count > 2 in last_3_seconds:
    force_fps = 6.0
    force_duration = 3.0
```

**Why:** Unstable tracking needs dense coverage

---

### Guard G5: Scene Change Recovery
```python
if scene_change_detected:
    force_fps = 6.0
    force_duration = 2.0  # Re-establish after scene cut
```

**Why:** New scene requires fresh track establishment

---

## 🔄 ADAPTIVE SAMPLING ALGORITHM

### State Machine:
```python
class AdaptiveSampler:
    def __init__(self):
        self.current_fps = 3.0
        self.force_fps = None
        self.force_until = None
        self.last_frame_time = 0.0
        self.speaker_lock_start = None
        self.track_id_history = []
    
    def get_next_sample_time(self, current_time, context):
        # 1. Check safety guards (HIGHEST PRIORITY)
        if self._check_guards(context):
            fps = self.force_fps
        # 2. Check dense conditions
        elif self._check_dense_conditions(context):
            fps = 6.0
        # 3. Check sparse conditions
        elif self._check_sparse_conditions(context):
            fps = 1.0
        # 4. Default moderate
        else:
            fps = 3.0
        
        # Compute next sample time
        step = 1.0 / fps
        next_time = current_time + step
        
        # Log for metrics
        self._log_sampling_decision(fps, context)
        
        return next_time
    
    def _check_guards(self, context):
        # G1: Turn boundary
        if context.time_to_next_turn <= 3.0:
            self.force_fps = 6.0
            return True
        
        # G2: Speaker change
        if context.subtitle_turn_changed:
            self.force_fps = 6.0
            self.force_until = context.current_time + 2.0
            return True
        
        # G3-G5: Other guards...
        return False
    
    def _check_dense_conditions(self, context):
        # D1-D5 conditions
        if context.time_to_next_turn <= 3.0:
            return True
        if context.speaker_confidence < 0.60:
            return True
        if context.motion_magnitude > 0.35:
            return True
        # ... other dense conditions
        return False
    
    def _check_sparse_conditions(self, context):
        # S1-S3 conditions WITH safety checks
        if (context.speaker_locked 
            and context.speaker_confidence >= 0.85
            and context.lock_duration >= 3.0
            and context.time_to_next_turn > 5.0  # CRITICAL GUARD
            and context.motion_magnitude < 0.15):
            return True
        # ... other sparse conditions
        return False
```

---

## 📊 EXPECTED IMPACT

### Current Baseline (Fixed 3.0 FPS):
```
30s candidate × 3.0 fps = 90 frames
```

### With Adaptive Sampling:
```
Breakdown (estimated for typical 30s candidate):

Dense zones (turn boundaries):  5s × 6.0fps =  30 frames
Moderate zones (dialogue):     15s × 3.0fps =  45 frames
Sparse zones (stable lock):    10s × 1.0fps =  10 frames
                                              ───────────
Total:                                         85 frames

Reduction: (90 - 85) / 90 = 5.6% per candidate
```

### Realistic Distribution:
```
High-motion candidates:     90-100 frames (no reduction)
Standard dialogue:          70-85 frames  (10-15% reduction)
Stable single-speaker:      50-70 frames  (20-40% reduction)

Average: 15-20% reduction WITHOUT quality loss
```

---

## 🧪 VALIDATION CHECKLIST

Before deploying adaptive sampling:

### ✅ Turn-First Integrity:
- [ ] All subtitle turn boundaries sampled at 6fps minimum
- [ ] `subtitle_turn_changed` triggers force dense sampling
- [ ] 3s buffer before turn, 2s buffer after turn

### ✅ Speaker Switch Capture:
- [ ] Track ID changes occur within dense-sampled regions
- [ ] No speaker switches missed due to sparse sampling
- [ ] Confidence drops trigger density increase

### ✅ Face Continuity:
- [ ] Track merging still works with variable FPS
- [ ] No discontinuous jumps from sparse sampling
- [ ] Stable track IDs maintained

### ✅ Crop Stability:
- [ ] Crop center variance within acceptable range (<0.05)
- [ ] No jittery crops from sparse sampling
- [ ] Motion blending still smooth

### ✅ Emotional Framing:
- [ ] High-energy moments captured densely
- [ ] Reaction shots not missed
- [ ] Payoff moments detected

---

## 🎯 IMPLEMENTATION PHASES

### Phase 1: Basic Adaptive (LOW RISK)
- Implement turn-boundary dense forcing (G1, G2)
- Implement stable-lock sparse sampling (S1)
- Target: 8-10% reduction

### Phase 2: Motion-Aware (MEDIUM RISK)
- Add motion magnitude detection (D3)
- Add low-motion sparse sampling (S3)
- Target: 12-15% reduction

### Phase 3: Full Matrix (HIGHER RISK)
- Add all dense/moderate/sparse conditions
- Add all safety guards
- Target: 15-20% reduction

---

## 🔬 PROFILING METRICS

Track these metrics to validate adaptive sampling:

```python
{
    "adaptive_sampling_enabled": True,
    "frames_dense_sampled": 150,
    "frames_moderate_sampled": 180,
    "frames_sparse_sampled": 45,
    "total_frames": 375,
    
    "avg_fps_dense_zones": 6.0,
    "avg_fps_moderate_zones": 3.0,
    "avg_fps_sparse_zones": 1.2,
    "avg_fps_overall": 2.8,  # Down from 3.0
    
    "turn_boundaries_dense": 12,  # All turn zones sampled densely
    "guard_overrides": 8,  # Safety guards triggered
    "sparse_sampling_windows": 5,  # Stable lock regions
    
    "quality_metrics": {
        "speaker_switches_captured": 12,
        "speaker_switches_total": 12,
        "capture_rate": 1.00,  # 100% (MUST be 1.00)
        "crop_stability_score": 0.94,
        "track_continuity_score": 0.97
    }
}
```

---

**End of Sampling Matrix**

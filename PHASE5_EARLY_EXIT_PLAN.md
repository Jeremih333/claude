# PHASE 5 — EARLY EXIT DESIGN
## Safe Early-Stop Conditions

**Date:** 2026-06-22  
**Status:** DESIGN COMPLETE  
**Expected Savings:** 8-12% of face_detection_sec

---

## 🎯 CORE PRINCIPLE

**Early exit = stop expensive detection expansion when speaker is stable AND no upcoming changes.**

**CRITICAL:** Must NEVER trigger if:
- Active speaker switch incoming
- Subtitle turn changed
- Second speaker enters
- Emotion intensity rises

---

## ✅ SAFE EARLY EXIT CONDITIONS

### Condition E1: Single Dominant Speaker Stable
**Trigger:**
```python
speaker_locked == True
AND
speaker_confidence >= 0.88
AND
lock_duration >= 4.0 seconds
AND
visible_face_count == 1
AND
track_id_stable == True  # No ID changes in last 3s
```

**Action:** Stop detection expansion after lock established

**Justification:** Single stable speaker = no new information to gain

---

### Condition E2: High Confidence Lock with Turn Buffer
**Trigger:**
```python
speaker_confidence >= 0.92
AND
lock_confidence >= 0.85
AND
time_to_next_turn > 6.0 seconds
AND
frame_motion_magnitude < 0.12
```

**Action:** Resume detection 4s before next turn

**Justification:** High confidence + distant turn = safe to pause

---

### Condition E3: Crop Center Variance Low
**Trigger:**
```python
crop_center_variance < 0.02  # Very stable framing
AND
speaker_locked == True
AND
lock_duration >= 3.0 seconds
```

**Action:** Reduce sampling to 1fps until variance increases

**Justification:** Static framing = minimal change expected

---

### Condition E4: Subtitle Turn Continuity Stable
**Trigger:**
```python
subtitle_turn_stable_for >= 5.0 seconds
AND
active_turn_speaker == locked_speaker
AND
no_speaker_changes_detected == True
```

**Action:** Sparse sampling until turn boundary approaches

**Justification:** Turn continuity = speaker won't change mid-turn

---

## 🚫 EARLY EXIT BLOCKERS (NEVER EXIT IF TRUE)

### Blocker B1: Subtitle Turn Approaching
```python
if time_to_next_turn <= 4.0 seconds:
    early_exit_allowed = False
    reason = "turn_boundary_approaching"
```

**Why:** Turn-first switching requires dense sampling (PHASE 3C)

---

### Blocker B2: Speaker Confidence Dropping
```python
if speaker_confidence_delta < -0.15:
    early_exit_allowed = False
    reason = "confidence_drop_detected"
```

**Why:** Confidence drop indicates potential track loss

---

### Blocker B3: Second Speaker Entering
```python
if visible_face_count > 1:
    early_exit_allowed = False
    reason = "multi_speaker_detected"
```

**Why:** Multi-speaker scenes need precise handoff tracking

---

### Blocker B4: Emotion Intensity Rising
```python
if emotion_intensity_delta > 0.20:
    early_exit_allowed = False
    reason = "emotion_spike_detected"
```

**Why:** Emotional peaks are high-value moments

---

### Blocker B5: Motion Increase
```python
if frame_motion_magnitude > 0.25:
    early_exit_allowed = False
    reason = "high_motion_detected"
```

**Why:** Motion suggests action/change worth capturing

---

### Blocker B6: Track ID Instability
```python
if track_id_changes_in_last_3s > 1:
    early_exit_allowed = False
    reason = "track_instability"
```

**Why:** Unstable tracking requires continued monitoring

---

### Blocker B7: Scene Change Detected
```python
if scene_change_detected:
    early_exit_allowed = False
    force_dense_duration = 2.0 seconds
    reason = "scene_change_recovery"
```

**Why:** New scene requires fresh track establishment

---

## 🔄 EARLY EXIT ALGORITHM

### State Machine:
```python
class EarlyExitController:
    def __init__(self):
        self.exit_active = False
        self.exit_start_time = None
        self.exit_reason = None
        self.resume_time = None
        self.exit_count = 0
    
    def should_exit(self, current_time, context, subtitle_turns):
        # 1. Check blockers (HIGHEST PRIORITY)
        blocker = self._check_blockers(context, subtitle_turns, current_time)
        if blocker:
            if self.exit_active:
                self._resume_detection(blocker)
            return False
        
        # 2. Check exit conditions
        if self._check_exit_conditions(context):
            if not self.exit_active:
                self._enter_exit_mode(current_time, context)
            return True
        
        # 3. No exit
        if self.exit_active:
            self._resume_detection("conditions_not_met")
        return False
    
    def _check_blockers(self, context, subtitle_turns, current_time):
        # B1: Turn approaching
        next_turn = self._get_next_turn(subtitle_turns, current_time)
        if next_turn and (next_turn - current_time) <= 4.0:
            return "turn_boundary_approaching"
        
        # B2: Confidence drop
        if context.speaker_confidence_delta < -0.15:
            return "confidence_drop"
        
        # B3: Multi-speaker
        if context.visible_face_count > 1:
            return "multi_speaker"
        
        # B4: Emotion spike
        if context.emotion_intensity_delta > 0.20:
            return "emotion_spike"
        
        # B5: High motion
        if context.frame_motion_magnitude > 0.25:
            return "high_motion"
        
        # B6: Track instability
        if context.track_id_changes_recent > 1:
            return "track_instability"
        
        # B7: Scene change
        if context.scene_change_detected:
            return "scene_change"
        
        return None
    
    def _check_exit_conditions(self, context):
        # E1: Single dominant speaker
        if (context.speaker_locked
            and context.speaker_confidence >= 0.88
            and context.lock_duration >= 4.0
            and context.visible_face_count == 1
            and context.track_id_stable):
            return True
        
        # E2: High confidence lock
        if (context.speaker_confidence >= 0.92
            and context.lock_confidence >= 0.85
            and context.time_to_next_turn > 6.0
            and context.frame_motion_magnitude < 0.12):
            return True
        
        # E3: Low variance
        if (context.crop_center_variance < 0.02
            and context.speaker_locked
            and context.lock_duration >= 3.0):
            return True
        
        # E4: Turn continuity
        if (context.subtitle_turn_stable_for >= 5.0
            and context.active_turn_speaker == context.locked_speaker
            and context.no_speaker_changes_detected):
            return True
        
        return False
    
    def _enter_exit_mode(self, current_time, context):
        self.exit_active = True
        self.exit_start_time = current_time
        self.exit_count += 1
        
        # Determine exit reason
        if context.speaker_confidence >= 0.92:
            self.exit_reason = "high_confidence_lock"
        elif context.crop_center_variance < 0.02:
            self.exit_reason = "stable_framing"
        elif context.subtitle_turn_stable_for >= 5.0:
            self.exit_reason = "turn_continuity"
        else:
            self.exit_reason = "speaker_lock"
    
    def _resume_detection(self, reason):
        if self.exit_active:
            duration = time.time() - self.exit_start_time
            # Log exit session
            self._log_exit_session(duration, reason)
        
        self.exit_active = False
        self.exit_start_time = None
        self.exit_reason = None
    
    def _get_next_turn(self, subtitle_turns, current_time):
        """Find next subtitle turn boundary after current_time."""
        for turn in subtitle_turns:
            if turn['start'] > current_time:
                return turn['start']
        return None
    
    def _log_exit_session(self, duration, resume_reason):
        """Log early exit metrics."""
        pass  # Implement profiling
```

---

## 🎯 INTEGRATION POINTS

### Point 1: `active_speaker.py` Loop Modification
```python
# Line 446-604: Main detection loop
def estimate_face_tracks(clip, start_t, end_t, subtitle_turns=None, **kwargs):
    # ... initialization ...
    
    early_exit = EarlyExitController()
    t = start_t
    
    while t < end_t:
        # Build context for early exit decision
        context = _build_detection_context(tracks, t, clip)
        
        # Check early exit
        if early_exit.should_exit(t, context, subtitle_turns):
            # Sparse sampling mode
            t += 1.0  # Jump 1 second
            continue
        
        # Normal detection
        frame = clip.get_frame(t)
        faces = _detect_faces(frame, detector)
        # ... track building ...
        
        t += step
    
    return tracks
```

### Point 2: `face_crop.py` Subtitle Turn Passing
```python
# Line 1878: Pass subtitle turns to estimate_face_tracks
local_tracks = estimate_face_tracks(
    clip, start, end,
    subtitle_turns=candidate_subtitle_turns,  # NEW
    **kwargs
)
```

---

## 📊 EXPECTED IMPACT

### Scenario: 30s Candidate with 20s Stable Lock

**Current (No Early Exit):**
```
30s × 3fps = 90 frames
```

**With Early Exit:**
```
Initial lock phase:  5s × 3fps = 15 frames
Stable lock phase:  20s × 1fps = 20 frames  (EARLY EXIT ACTIVE)
Turn approach:       5s × 6fps = 30 frames
Total: 65 frames

Reduction: (90 - 65) / 90 = 28% for this candidate
```

### Realistic Distribution:
```
Candidates with stable lock (40%):  25-30% reduction
Candidates without lock (60%):       0% reduction

Average: 0.4 × 28% = 11.2% overall
```

---

## 🛡️ QUALITY PROTECTION

### Protection 1: Turn-First Authority Preserved
**Guarantee:** Early exit ALWAYS resumes 4s before next turn

**Validation:**
```python
assert time_to_next_turn > 4.0, "Early exit blocked too close to turn"
```

---

### Protection 2: Speaker Switch Capture
**Guarantee:** Multi-speaker detection blocks early exit

**Validation:**
```python
assert visible_face_count == 1, "Early exit requires single speaker"
```

---

### Protection 3: Track Continuity
**Guarantee:** Track ID instability blocks early exit

**Validation:**
```python
assert track_id_changes_recent == 0, "Early exit requires stable track ID"
```

---

### Protection 4: Confidence Threshold
**Guarantee:** Only high-confidence locks allow early exit

**Validation:**
```python
assert speaker_confidence >= 0.88, "Early exit requires high confidence"
```

---

## 🔬 EARLY EXIT METRICS

Track these for validation:

```python
{
    "early_exit_enabled": True,
    "early_exit_hits": 6,
    "early_exit_blocked_by_turn": 3,
    "early_exit_blocked_by_confidence": 2,
    "early_exit_blocked_by_motion": 1,
    
    "avg_exit_duration_sec": 8.5,
    "total_exit_time_sec": 51.0,
    "total_candidate_time_sec": 450.0,
    "exit_ratio": 0.11,  # 11% of time in early exit
    
    "frames_skipped_by_exit": 128,
    "frames_saved_percent": 9.5,
    
    "exit_reasons": {
        "high_confidence_lock": 3,
        "stable_framing": 2,
        "turn_continuity": 1
    },
    
    "resume_reasons": {
        "turn_boundary_approaching": 3,
        "confidence_drop": 1,
        "motion_increase": 1,
        "multi_speaker": 1
    },
    
    "quality_validation": {
        "speaker_switches_missed": 0,  # MUST be 0
        "turn_boundaries_missed": 0,   # MUST be 0
        "track_discontinuities": 0     # MUST be 0
    }
}
```

---

## 🎯 IMPLEMENTATION PHASES

### Phase 1: Basic Exit (Single Condition)
- Implement E1 only (single speaker stable)
- All blockers active
- Target: 5-7% reduction

### Phase 2: Multi-Condition Exit
- Add E2-E4 conditions
- Refine blocker thresholds
- Target: 8-10% reduction

### Phase 3: Adaptive Resume
- Smart resume timing based on context
- Predictive turn detection
- Target: 10-12% reduction

---

**End of Early Exit Plan**

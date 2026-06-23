# PHASE 5 — QUALITY PROTECTION RULES
## Mandatory Quality Invariants

**Date:** 2026-06-22  
**Status:** INVARIANTS DEFINED  
**Purpose:** Ensure optimization NEVER degrades quality

---

## 🛡️ CORE PRINCIPLE

**Performance optimization must be INVISIBLE to end-user quality.**

All optimizations (caching, adaptive sampling, early exit) MUST preserve:
1. Speaker turn authority
2. Face detection completeness
3. Crop framing stability
4. Track continuity
5. Dialogue reaction switching
6. Dual-speaker detection
7. Speaker handoff precision

---

## ✅ MANDATORY INVARIANTS

### Invariant I1: Turn-First Authority (CRITICAL)
**Source:** PHASE 3C

**Rule:** `subtitle_turn_changed` MUST trigger dense face detection

**Validation:**
```python
def validate_turn_first_authority(detection_log, subtitle_turns):
    for turn in subtitle_turns:
        # Find detection samples around turn boundary
        samples_near_turn = [
            s for s in detection_log
            if abs(s['timestamp'] - turn['start']) <= 3.0
        ]
        
        # Assert dense sampling (≥5fps)
        for sample in samples_near_turn:
            assert sample['fps'] >= 5.0, \
                f"Turn boundary at {turn['start']}s not densely sampled"
        
        # Assert no gaps >0.25s near turn
        sample_gaps = compute_gaps(samples_near_turn)
        assert max(sample_gaps) <= 0.25, \
            f"Gap >0.25s detected near turn at {turn['start']}s"
```

**Protected Operations:**
- Dense sampling 3s before turn
- Dense sampling 2s after turn
- No early exit within 4s of turn
- No sparse sampling at turn boundaries

---

### Invariant I2: Subtitle Turn Switch Capture (CRITICAL)
**Source:** PHASE 3C

**Rule:** ALL speaker switches triggered by subtitle turns MUST be captured

**Validation:**
```python
def validate_switch_capture(switches_detected, switches_expected):
    # switches_expected from subtitle turn analysis
    # switches_detected from face track analysis
    
    missed_switches = []
    for expected in switches_expected:
        found = any(
            abs(detected['timestamp'] - expected['timestamp']) <= 0.5
            for detected in switches_detected
        )
        if not found:
            missed_switches.append(expected)
    
    assert len(missed_switches) == 0, \
        f"Missed {len(missed_switches)} speaker switches: {missed_switches}"
```

**Protected Operations:**
- Speaker confidence scoring at turn boundaries
- Track ID transitions aligned with turns
- `active_turn_speaker` tracking accurate

---

### Invariant I3: Face Continuity (HIGH PRIORITY)
**Source:** Track merging logic

**Rule:** Track IDs MUST remain stable across frames (no discontinuous jumps)

**Validation:**
```python
def validate_face_continuity(track_history):
    for i in range(1, len(track_history)):
        prev_track = track_history[i-1]
        curr_track = track_history[i]
        
        time_gap = curr_track['start'] - prev_track['end']
        
        # If same track ID, gap must be small
        if prev_track['track_id'] == curr_track['track_id']:
            assert time_gap <= 1.0, \
                f"Track {curr_track['track_id']} discontinuous: {time_gap}s gap"
        
        # If different track ID, must have spatial/confidence reason
        else:
            spatial_overlap = compute_bbox_overlap(
                prev_track['bbox'],
                curr_track['bbox']
            )
            confidence_delta = abs(
                curr_track['confidence'] - prev_track['confidence']
            )
            
            # Track change justified?
            justified = (
                spatial_overlap < 0.3  # Different face location
                or confidence_delta > 0.25  # Significant confidence change
                or time_gap > 2.0  # Large temporal gap
            )
            
            assert justified, \
                f"Unjustified track change from {prev_track['track_id']} to {curr_track['track_id']}"
```

**Protected Operations:**
- Track merging `_merge_overlapping_tracks()`
- Track ID stability across cached intervals
- No ID flickering from optimization

---

### Invariant I4: Crop Framing Stability (HIGH PRIORITY)
**Source:** Face crop targeting logic

**Rule:** Crop center variance MUST remain below acceptable threshold

**Validation:**
```python
def validate_crop_stability(crop_targets):
    # Compute frame-to-frame variance
    variances = []
    for i in range(1, len(crop_targets)):
        prev_center = crop_targets[i-1]['center']
        curr_center = crop_targets[i]['center']
        
        variance = (
            abs(curr_center[0] - prev_center[0]) +
            abs(curr_center[1] - prev_center[1])
        )
        variances.append(variance)
    
    # Assert smooth motion (variance < 0.08 per frame)
    for i, var in enumerate(variances):
        assert var < 0.08, \
            f"Crop jitter at frame {i}: variance={var:.3f}"
    
    # Assert average stability
    avg_variance = sum(variances) / len(variances)
    assert avg_variance < 0.03, \
        f"Average crop instability: {avg_variance:.3f}"
```

**Protected Operations:**
- `_turn_based_targets()` smooth transitions
- Motion blend parameters preserved
- No jitter from adaptive sampling

---

### Invariant I5: Dialogue Reaction Switching (MEDIUM PRIORITY)
**Source:** Multi-speaker interaction logic

**Rule:** Listener reactions MUST trigger crop switches when appropriate

**Validation:**
```python
def validate_reaction_switching(crop_switches, audio_energy_spikes):
    # Find listener reaction opportunities
    reaction_candidates = [
        spike for spike in audio_energy_spikes
        if spike['speaker'] == 'listener'
        and spike['energy'] > threshold
    ]
    
    # Check if reactions triggered switches
    captured_reactions = 0
    for candidate in reaction_candidates:
        switch_near = any(
            abs(switch['timestamp'] - candidate['timestamp']) <= 1.0
            for switch in crop_switches
        )
        if switch_near:
            captured_reactions += 1
    
    # At least 50% of strong reactions should trigger switches
    capture_rate = captured_reactions / len(reaction_candidates)
    assert capture_rate >= 0.50, \
        f"Reaction capture rate too low: {capture_rate:.1%}"
```

**Protected Operations:**
- Listener confidence scoring
- Dialogue likelihood detection
- Reaction frame detection

---

### Invariant I6: Dual-Speaker Detection (MEDIUM PRIORITY)
**Source:** Multi-face tracking

**Rule:** When 2+ speakers visible, BOTH must be tracked

**Validation:**
```python
def validate_dual_speaker_detection(tracks, ground_truth_multi_speaker_segments):
    for segment in ground_truth_multi_speaker_segments:
        # Find tracks in this segment
        segment_tracks = [
            t for t in tracks
            if t['start'] <= segment['end'] and t['end'] >= segment['start']
        ]
        
        # Count unique speakers
        unique_speakers = len(set(t['track_id'] for t in segment_tracks))
        
        # Assert at least 2 speakers detected
        assert unique_speakers >= 2, \
            f"Dual-speaker segment at {segment['start']}s only detected {unique_speakers} speaker(s)"
```

**Protected Operations:**
- Multi-face detection in `_detect_faces()`
- Track separation logic
- Dialogue center detection

---

### Invariant I7: Speaker Handoff Precision (HIGH PRIORITY)
**Source:** Turn-first switching + track transitions

**Rule:** Speaker handoffs MUST occur within ±0.5s of subtitle turn

**Validation:**
```python
def validate_handoff_precision(handoffs, subtitle_turns):
    for turn in subtitle_turns:
        # Find handoff near this turn
        nearby_handoffs = [
            h for h in handoffs
            if abs(h['timestamp'] - turn['start']) <= 2.0
        ]
        
        if not nearby_handoffs:
            continue  # No handoff expected (same speaker continues)
        
        # Check precision
        closest_handoff = min(
            nearby_handoffs,
            key=lambda h: abs(h['timestamp'] - turn['start'])
        )
        
        precision = abs(closest_handoff['timestamp'] - turn['start'])
        assert precision <= 0.5, \
            f"Handoff at turn {turn['start']}s imprecise: {precision:.2f}s offset"
```

**Protected Operations:**
- Turn boundary detection
- Track ID transitions
- Speaker confidence handoff logic

---

## 🚫 OPTIMIZATION CONSTRAINTS

### Constraint C1: Adaptive Sampling Must Not Skip Turns
**Rule:** FPS at turn boundaries ≥ 6.0

**Implementation check:**
```python
# In adaptive_sample_rate()
if context.time_to_next_turn <= 3.0:
    assert sample_rate >= 6.0, "Turn boundary sampling too sparse"
```

---

### Constraint C2: Early Exit Must Resume Before Turns
**Rule:** Early exit MUST resume ≥4s before next turn

**Implementation check:**
```python
# In EarlyExitController.should_exit()
if time_to_next_turn <= 4.0:
    assert early_exit_active == False, "Early exit too close to turn"
```

---

### Constraint C3: Cache Must Preserve Track IDs
**Rule:** Cached tracks merged with new tracks MUST maintain ID stability

**Implementation check:**
```python
# In IntervalTrackCache._merge_tracks()
for merged_track in result:
    overlapping = [t for t in tracks if overlaps(t, merged_track)]
    if len(overlapping) > 1:
        # Verify track IDs consistent
        ids = set(t['track_id'] for t in overlapping)
        assert len(ids) == 1, f"Track ID conflict: {ids}"
```

---

### Constraint C4: Detector Pooling Must Use Correct Profile
**Rule:** Cached detector MUST match requested profile

**Implementation check:**
```python
# In get_or_create_detector()
cached_detector = _DETECTOR_POOL.get(profile)
if cached_detector:
    assert cached_detector.profile == profile, \
        f"Detector profile mismatch: cached={cached_detector.profile}, requested={profile}"
```

---

## 🔬 QUALITY VALIDATION SUITE

### Automated Tests:

```python
class Phase5QualityTests:
    def test_turn_first_authority(self):
        """Verify all turn boundaries densely sampled."""
        validate_turn_first_authority(detection_log, subtitle_turns)
    
    def test_switch_capture_completeness(self):
        """Verify all speaker switches captured."""
        validate_switch_capture(switches_detected, switches_expected)
    
    def test_face_track_continuity(self):
        """Verify no discontinuous track jumps."""
        validate_face_continuity(track_history)
    
    def test_crop_framing_stability(self):
        """Verify crop variance within limits."""
        validate_crop_stability(crop_targets)
    
    def test_reaction_capture_rate(self):
        """Verify listener reactions trigger switches."""
        validate_reaction_switching(crop_switches, audio_energy_spikes)
    
    def test_dual_speaker_detection(self):
        """Verify multi-speaker scenes detect all speakers."""
        validate_dual_speaker_detection(tracks, multi_speaker_segments)
    
    def test_handoff_precision(self):
        """Verify speaker handoffs aligned with turns."""
        validate_handoff_precision(handoffs, subtitle_turns)
    
    def test_optimization_constraints(self):
        """Verify all optimization constraints met."""
        # C1-C4 checks
        pass
```

---

## 📊 QUALITY METRICS DASHBOARD

Track these metrics to monitor quality:

```python
{
    "quality_metrics": {
        "turn_first_compliance": 1.00,  # MUST be 1.00
        "switch_capture_rate": 1.00,    # MUST be 1.00
        "track_continuity_score": 0.97, # ≥0.95 required
        "crop_stability_score": 0.94,   # ≥0.92 required
        "reaction_capture_rate": 0.62,  # ≥0.50 required
        "dual_speaker_recall": 0.88,    # ≥0.85 required
        "handoff_precision_avg_ms": 280, # ≤500ms required
    },
    
    "invariant_violations": 0,  # MUST be 0
    "constraint_violations": 0,  # MUST be 0
    
    "quality_degradation_detected": False,
    "rollback_recommended": False
}
```

---

## 🚨 ROLLBACK TRIGGERS

If any of these conditions occur, DISABLE optimization immediately:

### Trigger R1: Turn Compliance Failure
```
turn_first_compliance < 1.00
```

### Trigger R2: Switch Capture Loss
```
switch_capture_rate < 0.95
```

### Trigger R3: Track Discontinuities
```
track_continuity_score < 0.90
```

### Trigger R4: Crop Instability
```
crop_stability_score < 0.85
```

### Trigger R5: Invariant Violations
```
invariant_violations > 0
```

**Action:** Log error, disable optimization flags, alert monitoring

---

**End of Quality Invariants**

# ACTIVE SPEAKER FORENSIC AUDIT
## Face Crop Decision-Making Architecture Analysis

**Date:** 2026-06-22  
**Purpose:** Determine what ACTUALLY decides camera target in create_vertical_crop()  
**Status:** ANALYSIS COMPLETE — NO CODE MODIFICATIONS

---

## 🎯 EXECUTIVE SUMMARY

### Primary Finding
**Turn-first switching IS implemented (PHASE 3C), but has CRITICAL weaknesses:**

1. ✅ **subtitle_turn_changed** is PRIMARY switch trigger (Line 1232, 1254-1258)
2. ⚠️ **Cooldown CAN block legitimate switches** (Line 1264-1267)
3. ⚠️ **Hold counters can delay turn switches** (Line 1095-1098, 1236-1240)
4. ❌ **Face confidence CAN override turns** through score_margin_ok (Line 1250-1251)
5. ❌ **Multiple fallback paths bypass turn logic** (Lines 1191-1213)

**Bottom Line:** Turn-first authority EXISTS but is COMPROMISED by competing logic paths.

---

## 📊 DECISION FLOW ARCHITECTURE

### Entry Point: `create_vertical_crop()` (Line 1470)
```
create_vertical_crop() 
├─ estimate_face_tracks() [Line 1878] → Face detection
├─ _build_turn_timeline() [Line 1890] → PHASE 3C: Turn timeline
├─ _build_window_targets() [Line 1997] → Window-level targets
└─ _turn_based_targets() [Line 1000] → Turn-first switching logic
   └─ Resolves to final crop coordinates
```

---

## 🔍 DETAILED AUTHORITY ANALYSIS

### Question 1: What Actually Decides Camera Target?

**Answer:** 5-tier decision hierarchy (in order):

#### Tier 1: FORCE MODES (Lines 1818-1831)
```python
if bool(force_center_crop):
    return _write_center_crop(out_path, "forced_center_crop")

if bool(force_face_preserving_crop):
    return _write_face_preserving_crop(...)
```
**Authority:** 100% override, bypasses ALL logic  
**Impact:** Unknown (depends on when these flags are set)

---

#### Tier 2: SUBJECT ACQUISITION FAILURE (Lines 1948-1965)
```python
if acquisition["state"] == "no_visible_subject":
    if face_preserving_anchor_center is not None:
        return _write_face_preserving_crop(...)
    return _write_center_crop(out_path, "no_visible_subject")
```
**Authority:** 100% override if no faces detected  
**Impact:** Falls back to center crop, ignores turns completely  
**Critical:** If face detection fails, turn-first is DEAD

---

#### Tier 3: TURN-FIRST SWITCHING (Lines 1232-1267)
```python
# Line 1232: Turn change detection
subtitle_turn_changed = bool(target.get("subtitle_turn_changed", False))
active_turn_speaker = target.get("active_turn_speaker")

# Line 1236-1240: Turn boundary resets hold/cooldown
if subtitle_turn_changed and active_turn_speaker:
    forced_turn_switches += 1
    speaker_hold_counter = 0
    speaker_switch_cooldown = 0  # BYPASSES cooldown
    last_turn_speaker = active_turn_speaker

# Line 1254-1258: Turn defines strong_turn_switch
strong_turn_switch = (
    candidate_role == "speaker"
    and subtitle_turn_changed
    and float(target.get("speaker_turn_strength", 0.0)) >= 0.20
)

# Line 1261-1263: Turn boundary FORCES switch
if subtitle_turn_changed and candidate_role == "speaker":
    should_switch = True  # FORCE evaluation
    required_hold = 1  # Minimal hold
```

**Authority:** HIGH — bypasses cooldown, forces switch evaluation  
**BUT:** Still subject to Tier 4 conditions (score_margin_ok, visible_enough)

---

#### Tier 4: CONFIDENCE & VISIBILITY GATES (Lines 1223-1251)
```python
# Line 1223-1227: Visibility gate
visible_enough = bool(
    target["strength"] >= switch_min_visibility * 0.12
    or visible_subject_count >= 1
    or float(target.get("speaker_confidence", 0.0)) >= 0.48
)

# Line 1250-1251: Score margin gate
score_margin_ok = candidate_switch_score >= (current_switch_score + strict_switch_margin)

# Line 1251: Combined requirement
should_switch = visible_enough and (track_changed and score_margin_ok or ...)
```

**Authority:** MEDIUM — can BLOCK turn switches if:
- Face confidence too low
- Speaker not visible
- Score margin not met

**Critical Flaw:** Even with `subtitle_turn_changed=True`, if `score_margin_ok=False`, switch is blocked!

---

#### Tier 5: COOLDOWN BLOCKER (Lines 1264-1267)
```python
# Line 1264-1267: Cooldown blocks non-turn switches
elif speaker_switch_cooldown > 0 and track_changed and not strong_turn_switch:
    cooldown_blocked_switches += 1
    should_switch = False
```

**Authority:** LOW — only blocks non-turn switches  
**Note:** Turn switches bypass cooldown (Line 1239), BUT edge cases possible

---

### Question 2: Is speaker_turn_strength Truly Primary?

**Answer:** NO — it's ONE INPUT among many.

**Evidence:**

1. **speaker_turn_strength** (Line 1257):
   ```python
   float(target.get("speaker_turn_strength", 0.0)) >= 0.20
   ```
   Only used to define `strong_turn_switch`, not the ONLY decision factor

2. **Competing Signals** (Lines 96-100, 1129):
   ```python
   # Line 96-100: Speaker priority formula
   def _speaker_priority(face):
       return (
           float(face.get("speaking_score", 0.0)) * 1.15
           + float(face.get("listener_score", 0.0)) * 0.35
           + float(face["box_w"] * face["box_h"]) * 0.55
       )
   
   # Line 1129: Candidate switch score
   candidate_switch_score = float(target.get("speaker_confidence_score", ...))
   ```

3. **Decision Matrix:**
   ```
   Final Switch Decision = 
       subtitle_turn_changed (PRIMARY)
       AND visible_enough (GATE)
       AND score_margin_ok (GATE)
       AND NOT cooldown_blocked (GATE unless turn)
       AND hold_counter satisfied (GATE)
   ```

**Conclusion:** `speaker_turn_strength` is PRIMARY TRIGGER but NOT SOLE AUTHORITY.

---

### Question 3: Can face_confidence Override Turns?

**Answer:** YES — through `score_margin_ok` gate.

**Evidence:**

**Line 1250:** Score margin check
```python
score_margin_ok = candidate_switch_score >= (current_switch_score + strict_switch_margin)
```

**Line 1129:** candidate_switch_score derived from face confidence
```python
candidate_switch_score = float(target.get("speaker_confidence_score", target.get("switch_score", 0.0)) or 0.0)
```

**Scenario:**
```
Turn boundary detected: subtitle_turn_changed = True
New speaker face confidence: 0.45
Current speaker confidence: 0.85
strict_switch_margin: 0.13

score_margin_ok = 0.45 >= (0.85 + 0.13) = False

Result: Turn switch BLOCKED despite subtitle_turn_changed=True
```

**Impact:** LOW confidence new speaker cannot take over from HIGH confidence current speaker, even at turn boundary.

**Is this correct behavior?** DEBATABLE:
- ✅ Prevents switching to poorly detected faces
- ❌ Violates turn-first authority principle
- ❌ Can cause speaker lag at turn boundaries

---

### Question 4: Can Cooldown Block Legitimate Switches?

**Answer:** NO for turn switches, YES for mid-turn switches.

**Evidence:**

**Line 1236-1240:** Turn boundary bypasses cooldown
```python
if subtitle_turn_changed and active_turn_speaker:
    forced_turn_switches += 1
    speaker_hold_counter = 0
    speaker_switch_cooldown = 0  # RESET
```

**Line 1264-1267:** Cooldown only blocks non-turn switches
```python
elif speaker_switch_cooldown > 0 and track_changed and not strong_turn_switch:
    cooldown_blocked_switches += 1
    should_switch = False
```

**Turn switches:** Cooldown = 0 (BYPASSED)  
**Mid-turn switches:** Blocked if cooldown > 0

**Concern:** What if `subtitle_turn_changed` is False but speaker ACTUALLY changed?
- Mid-sentence switches blocked
- Reaction shots delayed
- Dialogue handoffs missed

**Metric to track:** `cooldown_blocked_switches` counter (Line 1122, 1266)

---

### Question 5: Can hold_frames Keep Camera on Wrong Speaker?

**Answer:** YES — hold counters delay switches.

**Evidence:**

**Line 1095-1098:** Hold window calculation
```python
speaker_hold_windows = 0 if strict_center else max(hold_windows, int(round(max(0.6, float(speaker_min_hold_seconds)) / 0.6)))
listener_hold_windows = 1 if strict_center else max(1, int(round(max(0.5, listener_hold_seconds) / 0.6)))
```

**Line 1236:** Turn boundary resets hold counter
```python
speaker_hold_counter = 0  # Reset on turn
```

**BUT:** Line 1263 sets `required_hold = 1` even on turn boundary, meaning switch still needs 1 window delay.

**Scenario:**
```
Window 0: Turn boundary detected, subtitle_turn_changed = True
Window 0: required_hold = 1 set
Window 0: Switch NOT applied yet (hold requirement)
Window 1: Switch applied (1 window delay)
```

**Impact:** Minimum 1 frame delay (~0.2-0.3s at window_sec=0.6) even for turn switches.

**Is this acceptable?** MAYBE:
- ✅ Prevents jitter
- ❌ Introduces turn-switch latency
- ❌ Violates "instant turn switch" expectation

---

## 📊 DECISION WEIGHT BREAKDOWN

### Estimated % of Final Framing Decisions Driven By:

Based on code analysis (NOT runtime profiling):

#### 1. Subtitle Turns
**Weight:** 30-40%  
**Evidence:**
- Line 1232: `subtitle_turn_changed` is PRIMARY trigger
- Line 1261-1263: Forces switch evaluation
- Line 1236-1240: Bypasses cooldown, resets hold
- Line 1122: `forced_turn_switches` counter tracks this

**BUT:** Subject to visibility and confidence gates (Tier 4)

---

#### 2. Speaking Score
**Weight:** 20-25%  
**Evidence:**
- Line 96-100: `speaking_score * 1.15` in priority formula
- Line 47: Faces sorted by `speaking_score` first
- Line 1129: Contributes to `candidate_switch_score`

**Role:** Influences WHO is selected as speaker candidate

---

#### 3. Face Confidence
**Weight:** 25-30%  
**Evidence:**
- Line 1250: `score_margin_ok` gate uses confidence
- Line 1145-1149: `confident_lock` state based on lock_confidence
- Line 1223-1227: `visible_enough` gate includes confidence >= 0.48
- Line 98-100: Contributes to speaker_priority

**Role:** GATE that can block turn switches

---

#### 4. Bbox Size
**Weight:** 10-15%  
**Evidence:**
- Line 29: `box_w * box_h` used in speaker_focus mode
- Line 49: Bbox size is 3rd sort key in _visible_faces
- Line 100: `box_w * box_h * 0.55` in speaker_priority

**Role:** Tiebreaker when multiple faces have similar scores

---

#### 5. Cooldown
**Weight:** 5-10%  
**Evidence:**
- Line 1264-1267: Blocks mid-turn switches
- Line 1122: `cooldown_blocked_switches` counter
- Line 1239: BYPASSED at turn boundaries

**Role:** Prevents mid-turn flicker, but can delay legitimate switches

---

### Decision Authority Matrix

```
Decision Path                    | Frequency | Authority | Can Override Turns?
--------------------------------|-----------|-----------|-------------------
Turn Boundary Switch            | 30-40%    | HIGH      | N/A (IS turn)
Confident Lock Hold             | 20-25%    | MEDIUM    | YES (delays switch)
Score Margin Block              | 15-20%    | MEDIUM    | YES (blocks switch)
Visibility Gate Block           | 10-15%    | MEDIUM    | YES (blocks switch)
Cooldown Block (mid-turn only)  | 5-10%     | LOW       | NO (turn bypass)
Hold Counter Delay              | 5-10%     | LOW       | PARTIAL (1 frame delay)
Lost Face Recovery              | 5-8%      | HIGH      | YES (fallback)
Center Crop Fallback            | 2-5%      | CRITICAL  | YES (total override)
```

---

## 🔍 CODE PATH ANALYSIS

### Path 1: IDEAL Turn-First Switch
```
subtitle_turn_changed = True [Line 1232]
→ strong_turn_switch = True [Line 1254-1258]
→ speaker_hold_counter = 0 [Line 1238]
→ speaker_switch_cooldown = 0 [Line 1239]
→ should_switch = True [Line 1262]
→ visible_enough = True [Line 1223-1227]
→ score_margin_ok = True [Line 1250]
→ hard_switch_candidate = True [Line 1268-1283]
→ current = candidate [Line 1285]
→ lock_state = "speaker_locked" [Line 1291]
✅ Turn switch SUCCESS
```

---

### Path 2: Turn Switch BLOCKED by Low Confidence
```
subtitle_turn_changed = True [Line 1232]
→ strong_turn_switch = True [Line 1254-1258]
→ should_switch = True [Line 1262]
→ visible_enough = True [Line 1223-1227]
→ score_margin_ok = FALSE [Line 1250] ❌
   (new_speaker_confidence < current_confidence + margin)
→ hard_switch_candidate = False [Line 1268]
→ pending hold logic [Line 1301+]
❌ Turn switch BLOCKED despite subtitle_turn_changed
```

---

### Path 3: Turn Switch DELAYED by Hold
```
subtitle_turn_changed = True [Line 1232]
→ required_hold = 1 [Line 1263]
→ role_hold_counter incremented [implied]
→ Window 0: Switch NOT applied (hold requirement)
→ Window 1: Switch applied
⚠️ Turn switch DELAYED by 1 window (~0.2-0.3s)
```

---

### Path 4: No Faces Detected (Turn Logic BYPASSED)
```
estimate_face_tracks() returns empty [Line 1878]
→ acquisition["state"] = "no_visible_subject" [Line 1948]
→ _write_center_crop(out_path, "no_visible_subject") [Line 1965]
❌ Turn-first COMPLETELY BYPASSED
❌ Falls back to center crop regardless of subtitle state
```

---

### Path 5: Lost Face Recovery (Mid-Turn Fallback)
```
visible_subject_count == 0 [Line 1177]
→ invisible_streak += 1 [Line 1178]
→ recoverable_subject = True [Line 1176]
→ candidate = current [Line 1180]
→ lock_state = "lost_face_recover" [Line 1183]
→ Camera STAYS on last known position
⚠️ May keep camera on wrong speaker if face lost during turn
```

---

## 🚨 CRITICAL ISSUES IDENTIFIED

### Issue 1: Confidence Gate Can Override Turn Authority
**Severity:** HIGH  
**Lines:** 1250-1251, 1261-1263  
**Problem:**
```python
# Turn says switch
if subtitle_turn_changed and candidate_role == "speaker":
    should_switch = True

# BUT confidence gate can still block
score_margin_ok = candidate_switch_score >= (current_switch_score + strict_switch_margin)
# If False, switch blocked despite subtitle_turn_changed
```

**Impact:** Turn-first authority COMPROMISED when new speaker has low confidence.

**Recommended Fix:**
```python
# Turn switches should BYPASS score_margin_ok
if subtitle_turn_changed and candidate_role == "speaker":
    should_switch = True
    score_margin_ok = True  # FORCE bypass
```

---

### Issue 2: Hold Counter Introduces Turn Latency
**Severity:** MEDIUM  
**Lines:** 1095-1098, 1236, 1263  
**Problem:** Even turn switches have `required_hold = 1`, causing 1 window delay.

**Impact:** 0.2-0.3s latency between subtitle turn and camera switch.

**Recommended Fix:**
```python
# Line 1263: Remove hold for turn switches
if subtitle_turn_changed and candidate_role == "speaker":
    should_switch = True
    required_hold = 0  # INSTANT switch on turn
```

---

### Issue 3: No Face Detection = No Turn-First
**Severity:** CRITICAL  
**Lines:** 1878-1886, 1948-1965  
**Problem:** If `estimate_face_tracks()` fails, entire turn-first logic is bypassed.

**Impact:**
- Dialogue scenes with poor lighting = center crop fallback
- Turn timeline completely ignored
- Story quality degraded

**Recommended Fix:**
- Add subtitle-driven fallback positioning
- Use audio energy peaks to guide crop when faces unavailable
- Track last known speaker positions longer

---

### Issue 4: Multiple Competing Decision Paths
**Severity:** MEDIUM  
**Lines:** Throughout _turn_based_targets()  
**Problem:** Too many decision branches:
- speaker_locked
- listener_hold
- dialogue_center
- lost_face_recover
- scene_interest_fallback
- subject_person_hold
- confident_lock
- handoff_glide
- hard_switch

**Impact:** Difficult to predict behavior, hard to debug, authority conflicts.

**Recommended Fix:** Consolidate to 3 primary states:
1. **Turn-Lock:** Following subtitle turns (primary)
2. **Face-Lock:** Holding stable speaker (secondary)
3. **Safe-Fallback:** No faces detected (tertiary)

---

### Issue 5: Cooldown Can Delay Reaction Shots
**Severity:** LOW  
**Lines:** 1264-1267, 1242-1244  
**Problem:** Mid-turn switches blocked during cooldown.

**Impact:** Listener reactions, dialogue handoffs, emotional responses delayed.

**Recommended Fix:**
- Reduce cooldown window count
- Add exception for high-energy audio spikes
- Allow listener switches during speaker pauses

---

## 📈 METRICS TO TRACK

### Runtime Validation Needed

To validate this analysis, track these metrics:

```python
{
    "turn_first_metrics": {
        "forced_turn_switches": 0,           # Line 1122, 1237
        "cooldown_blocked_switches": 0,      # Line 1122, 1266
        "confident_lock_windows": 0,         # Line 1156
        "handoff_glide_windows": 0,          # Line 1168
        "hard_switch_windows": 0,            # Line 1292
        
        # NEW metrics to add:
        "turn_switches_confidence_blocked": 0,  # Track Issue 1
        "turn_switches_hold_delayed": 0,        # Track Issue 2
        "turn_switches_no_face_failed": 0,      # Track Issue 3
        "turn_switch_avg_latency_ms": 0.0,      # Measure delay
        
        "state_distribution": {
            "speaker_locked": 0,
            "listener_hold": 0,
            "dialogue_center": 0,
            "lost_face_recover": 0,
            "scene_interest_fallback": 0,
            "subject_person_hold": 0
        },
        
        "decision_authority_breakdown": {
            "subtitle_turn_driven": 0,
            "confidence_driven": 0,
            "speaking_score_driven": 0,
            "bbox_size_driven": 0,
            "cooldown_blocked": 0,
            "hold_delayed": 0,
            "fallback_used": 0
        }
    }
}
```

---

## ✅ CONCLUSIONS

### What ACTUALLY Decides Camera Target?

**5-Tier Hierarchy:**
1. **Force modes** (100% override)
2. **Face detection failure** (100% override to center crop)
3. **Subtitle turn boundary** (70-80% authority, subject to gates)
4. **Confidence + visibility gates** (30-50% veto power)
5. **Cooldown + hold** (10-20% delay power)

### Is Turn-First Authority Real?

**YES, but COMPROMISED:**
- ✅ `subtitle_turn_changed` is PRIMARY trigger
- ✅ Bypasses cooldown
- ✅ Resets hold counters
- ❌ Still gated by confidence thresholds
- ❌ Still delayed by hold windows
- ❌ Completely bypassed if no faces detected

### Primary Bottleneck

**CONFIDENCE GATES blocking turn switches** (Issue 1) is the PRIMARY quality issue.

If new speaker has low face confidence at turn boundary, turn-first authority is OVERRIDDEN.

**Evidence needed:** Runtime profiling to measure how often this occurs.

---

**End of Active Speaker Forensic Audit**

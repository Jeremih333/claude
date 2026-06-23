# FORENSIC AUDIT - PART 2: ARCHITECTURE BREAKDOWN
## Silence Surgery, Active Speaker, Timeline Reality

**Date:** 2026-06-16 01:43 UTC+3  
**Status:** CRITICAL ARCHITECTURAL FRAUD DETECTED

---

## SMOKING GUN #4: Timeline Editor is FAKE

### Evidence

**FILE:** `pipeline/montage/timeline_editor.py`  
**SIZE:** 11 lines total  
**FUNCTION:** `apply_timeline_plan()`

```python
def apply_timeline_plan(window: dict, plan: dict | None = None) -> dict:
    edited = dict(window or {})
    plan = dict(plan or {})
    edited["timeline_edit_applied"] = True  # ← METADATA FLAG
    edited["timeline_edit_plan"] = plan     # ← STORES PLAN AS METADATA
    edited["window_duration"] = round(      # ← UPDATES DURATION NUMBER
        float(plan.get("duration", edited.get("duration", 0.0)) 
        or edited.get("duration", 0.0)), 3
    )
    return edited
```

### What This Does NOT Do

**DOES NOT:**
- Cut silence from video timeline
- Remove dead air segments
- Rewrite start/end timestamps
- Build new segment list
- Apply surgical edits
- Modify video structure

**DOES:**
- Copy window dict
- Set flag `timeline_edit_applied = True`
- Store plan as metadata
- Update `window_duration` field (just a number)

### Why This is Architectural Fraud

The name `timeline_editor.py` and function `apply_timeline_plan` **PROMISES** video editing.

**Reality:** It's a metadata decorator.

No actual timeline surgery happens.

### Real Impact

```
silence_rewriter.py:
  ├─ Analyzes pauses (REAL ANALYSIS)
  ├─ Classifies: dead_air, comedic_pause, reaction, etc. (REAL CLASSIFICATION)
  ├─ Decides: cut vs keep (REAL DECISION)
  └─ Returns plan with trim events (REAL PLAN)

timeline_editor.py:
  ├─ Receives plan
  ├─ Stores plan in metadata
  ├─ Sets flag = True
  └─ DOES NOTHING TO VIDEO

Result:
  Dead air >2s remains in video
  Silence surgery never happens
  Pipeline thinks it's done (flag = True)
```

### Validation Data Evidence

From `_validation_sprint_1_6/story_run/validation_report.json`:

```json
"stats": {
  "silent_parts_removed_total": 0,  // ← ZERO REMOVED
  "pause_policy_failed_outputs": 0,
  "kept_micro_pauses": 0
}
```

**Zero silence removed** despite silence_rewriter analysis.

### ACTION Required

**REWRITE** `apply_timeline_plan` to do REAL timeline surgery:

1. Take silence trim events from plan
2. Build new segment list (voiced sections only)
3. Update window start/end based on cuts
4. Return edited window with NEW timestamps
5. Pass edited timeline to video export

OR

**RENAME** to `annotate_timeline_plan` to stop lying about functionality

---

## SMOKING GUN #5: Face Detection Timeout Loop

### Complete Failure Chain

**FILE:** `pipeline/highlight.py`

**Step 1: Face Detection (Line 6075-6082)**
```python
def _score_story_candidate(self, video_path: str, candidate: dict):
    # ...
    faces = sample_face_focus_stats(
        video_path,
        start,
        end,
        sample_fps=int(self.cfg.get("face_detection_fps", 2)),
        detector_profile=str(self.cfg.get("active_speaker_scan_profile", "light")),
    )
    _timings["face_detection_sec"] = round(time.perf_counter() - _t0, 3)
```

**Step 2: Calculate face_evidence_score (Line 6188-6193)**
```python
face_evidence_score = min(
    1.0,
    float(faces.get("face_presence", 0.0)) * 0.62
    + float(faces.get("person_presence", 0.0)) * 0.22
    + float(faces.get("subject_presence", 0.0)) * 0.16,
)
```

**Step 3: Timeout Fallback (Line 3353-3430)**
```python
def _score_story_candidate_timeout_fallback(self, candidate: dict):
    baseline = dict(candidate.get("score_breakdown", {}) or {})
    # ...
    source_face_presence = float(baseline.get("face_presence", 0.0) or 0.0)      # DEFAULT 0.0
    source_person_presence = float(baseline.get("person_presence", 0.0) or 0.0)  # DEFAULT 0.0
    source_subject_presence = float(baseline.get("subject_presence", 0.0) or 0.0)  # DEFAULT 0.0
    
    face_evidence_score = max(
        0.0,
        min(
            1.0,
            source_face_presence * 0.62
            + source_person_presence * 0.22
            + source_subject_presence * 0.16,
        ),
    )  # RESULT: 0.0 when all inputs are 0.0
```

**Step 4: Rejection (Line 9089-9090)**
```python
elif not face_evidence_gate and not story_override:
    reason = "no_visual_subject"
```

### The Vicious Circle

```
1. Candidate enters scoring
2. Face detection starts (~23 seconds needed)
3. Ranking timeout fires (30s limit)
4. Timeout fallback runs
5. face_presence, person_presence, subject_presence NOT in baseline
6. All default to 0.0
7. face_evidence_score = 0.0
8. face_evidence_gate = False (0.0 < 0.08)
9. REJECT: "no_visual_subject"
10. Repeat for all 6 candidates
11. Result: 0 outputs
```

### Why Face Detection Times Out

From profiling data:
- Face detection: ~23 seconds per candidate
- Ranking timeout: 30 seconds total
- BUT ranking does MORE than just face detection:
  - Video metrics
  - Premise scoring
  - Story scoring
  - Subtitle quality
  
Total time > 30s → timeout → fallback → 0.0 scores → rejection

### Why This is Scorer Architecture

**The Problem:** Pipeline REQUIRES face detection scores to pass selection.

**Montage-First Would:**
- Use dialogue turns for speaker switching
- Use subtitle timestamps for montage boundaries
- Use audio energy for active speaker detection
- Treat face detection as OPTIONAL enhancement, not gate

**Current Architecture:**
- BLOCKS all candidates without face scores
- Treats face_evidence as admission requirement
- Rejects valid dialogue content due to detection timeout

### ACTION Required

**PRIMARY:** Remove face_evidence_gate from production rejection path (Line 9089)

**SECONDARY:** Make face detection optional:
- Continue with 0.0 face scores (don't reject)
- Use turn-based active speaker as fallback
- Use face detection only when available (no timeout)

**TERTIARY:** Increase timeout OR disable face detection in story mode

---

## Active Speaker Logic Analysis

### Current Implementation

From earlier sprint reports, active speaker uses:
1. Face detection confidence
2. Center crop as fallback
3. Face lock when confidence high

### Problem

From validation data, face detection returns 0.0 for all candidates.

**Hypothesis:** Active speaker falls back to center crop for ALL frames.

**Expected Behavior (Turn-Based):**
```
Turn 1: Speaker A → Track face A (or two-shot if no detection)
Turn 2: Speaker B → Switch to face B (or two-shot)
Turn 3: Speaker A → Switch back to face A
```

**Actual Behavior (Confidence Fallback):**
```
Turn 1: No face confidence → Center crop
Turn 2: No face confidence → Center crop (NO SWITCH)
Turn 3: No face confidence → Center crop (NO SWITCH)
Result: Static framing, no speaker switching
```

### Evidence Needed

**Diagnostic Test 3** will measure:
- Dialogue turns vs camera switches
- Expected: ~1:1 ratio
- Hypothesis: Actual ~0.1:1 ratio (mostly static)

### Why This is Scorer Behavior

**Scorer Logic:** "Do I have enough confidence to switch?"  
**Montage Logic:** "Speaker changed, so I switch."

Confidence-based switching is **passive**.  
Turn-based switching is **active**.

For dialogue-heavy sitcoms:
- Turns are ALWAYS known (from subtitles)
- Face confidence MAY be low (lighting, angles)
- Turn-based switching works even without face detection

### ACTION Required

**REWRITE** active speaker logic to be turn-based:
1. Parse dialogue turns from subtitles
2. On turn change → camera switch
3. Use face detection to REFINE position (optional)
4. Fallback to two-shot if no face, NOT center crop

---

## Silence Surgery Reality Check

### What Exists

**FILE:** `pipeline/montage/silence_rewriter.py` (507 lines)

**Capabilities:**
- ✅ Analyzes pause energy
- ✅ Classifies silence types (dead_air, comedic_pause, reaction, etc.)
- ✅ Decides cut vs keep
- ✅ Builds timeline plan with trim events
- ✅ Calculates pacing scores

**Example Output:**
```python
{
  "pause_cut_count": 3,
  "trimmed_silence_seconds": 12.4,
  "silence_trim_events": [
    {"start": 45.2, "end": 49.8, "duration": 4.6, "silence_type": "dead_air"},
    {"start": 102.1, "end": 107.3, "duration": 5.2, "silence_type": "dead_air"},
    # ...
  ]
}
```

### What Does NOT Exist

**FILE:** `pipeline/montage/timeline_editor.py` (11 lines)

**Reality:**
- ❌ Does NOT cut silence from timeline
- ❌ Does NOT rebuild segment list
- ❌ Does NOT modify timestamps
- ✅ Only stores plan as metadata

### The Illusion

```
Pipeline flow:

1. silence_rewriter analyzes pauses → REAL ANALYSIS
2. Builds plan with trim events → REAL PLAN
3. timeline_editor.apply_timeline_plan(window, plan) → FAKE EDIT
4. window["timeline_edit_applied"] = True → FLAG SET
5. Video export uses ORIGINAL timestamps → NO CUTS APPLIED
6. Dead air remains in final short → SURGERY FAILED
```

### Validation Evidence

```json
"stats": {
  "silent_parts_removed_total": 0  // ← PROOF: Nothing was cut
}
```

### Why This Matters for Production

**User Expectation:**
- Dead air >2s should be removed
- Shorts should feel tight and paced

**Current Reality:**
- Dead air analysis happens
- But cuts never applied
- Final shorts have long pauses
- Viewer experience suffers

### ACTION Required

**IMMEDIATE:** Implement real timeline surgery in `timeline_editor.py`

**Pseudocode:**
```python
def apply_timeline_plan(window: dict, plan: dict) -> dict:
    trim_events = plan.get("silence_trim_events", [])
    
    # Build segment list (keep only voiced parts)
    segments = []
    current_time = window["start"]
    
    for trim in sorted(trim_events, key=lambda x: x["start"]):
        # Add segment before trim
        if trim["start"] > current_time:
            segments.append({
                "start": current_time,
                "end": trim["start"]
            })
        # Skip trim region
        current_time = trim["end"]
    
    # Add final segment
    if current_time < window["end"]:
        segments.append({
            "start": current_time,
            "end": window["end"]
        })
    
    # Calculate new duration
    new_duration = sum(seg["end"] - seg["start"] for seg in segments)
    
    return {
        "original_start": window["start"],
        "original_end": window["end"],
        "segments": segments,  # ← REAL TIMELINE
        "duration": new_duration,
        "timeline_edit_applied": True,
        "timeline_edit_plan": plan
    }
```

Then video export must concatenate segments, not use original start/end.

---

## COMPLETE FAILURE MAP

### Pipeline Kills

| Location | Type | Mechanism | Impact | Fix |
|----------|------|-----------|--------|-----|
| highlight.py:9089 | PRIMARY | face_evidence_gate blocks all | 4/6 candidates (66%) | REMOVE gate |
| highlight.py:9065-9088 | SECONDARY | Score threshold cascade | 2/6 candidates (33%) | REMOVE thresholds |
| highlight.py:5073 | TERTIARY | Dead code confusion | 186 lines unreachable | DELETE dead code |
| highlight.py:3419-3430 | SYSTEMIC | Timeout fallback defaults to 0.0 | All face scores = 0.0 | Fix defaults OR disable gate |
| timeline_editor.py:4-10 | ARCHITECTURAL | Fake timeline editing | No silence cuts applied | REWRITE editor |

### The Root Problem

**Current Architecture:** Scorer-First with Montage Decoration

```
Story Candidate
  ↓
Score Everything (face, interest, completeness, etc.)
  ↓
Check Score Gates (are scores high enough?)
  ↓
If ANY gate fails → REJECT
  ↓
If all gates pass → Accept
  ↓
Metadata-only "editing" (no real cuts)
  ↓
Export with original timestamps
```

**Target Architecture:** Montage-First with Score Metadata

```
Dialogue Candidate
  ↓
Parse Dialogue Turns (from subtitles)
  ↓
Build Story Window (temporal adjacency)
  ↓
Adjust Hook Boundary (first 1-3s)
  ↓
Real Silence Surgery (cut dead air >2s)
  ↓
Turn-Based Active Speaker (switch on dialogue change)
  ↓
Export Edited Timeline
  ↓
(Optional: Score for ranking/debugging)
```

---

## SUMMARY FOR PHASE 2 TESTS

### Test 1: Candidate Explosion

**Hypothesis:** Pipeline starves due to rejection gates

**Evidence:**
- 12 windows → 12 candidates → 0 outputs (story mode)
- 33 windows → 30 candidates → 3 outputs (legacy mode)

**Expected Healthy:** 25-40 windows → 8-15 candidates → 5-8 outputs

**Root Cause:** face_evidence_gate + score threshold cascade

---

### Test 2: Silence Surgery

**Hypothesis:** Timeline editor is fake, no real cuts

**Evidence:**
- timeline_editor.py is 11 lines, only metadata
- silent_parts_removed_total = 0
- No segment list modification

**Expected:** Real timeline surgery, dead air >2s removed

**Root Cause:** `apply_timeline_plan` doesn't edit timeline

---

### Test 3: Speaker Switching

**Hypothesis:** Falls back to center crop, no turn-based switching

**Evidence:**
- face_evidence_score = 0.0 for all candidates
- No face confidence → fallback logic triggers
- Suspected: static center crop for all frames

**Expected:** ~1:1 ratio (20 turns → 16-22 switches)

**Root Cause:** Confidence-based switching instead of turn-based

---

### Test 4: Story Coherence

**Hypothesis:** Random stitching due to temporal proximity, not structure

**Evidence:** TBD (need to analyze actual shorts)

**Expected:** Clear who/what/why/payoff

**Root Cause:** Story assembly uses proximity, not narrative structure

---

## NEXT STEPS

### Completed
- ✅ Part 1: Found PRIMARY KILLERS (face_evidence_gate, score thresholds)
- ✅ Part 2: Found ARCHITECTURAL FRAUD (fake timeline editor)
- ✅ Traced complete failure chain (timeout → 0.0 → rejection)
- ✅ Identified silence surgery illusion
- ✅ Identified active speaker confidence fallback problem

### Phase 2: Diagnostic Tests
- [ ] Test 1: Measure candidate explosion (use existing data)
- [ ] Test 2: Prove timeline editor is fake (analyze output videos)
- [ ] Test 3: Measure speaker switch ratio (analyze 1 short)
- [ ] Test 4: Story coherence technical analysis (analyze structure)

### Phase 3: Architecture Plan
- [ ] Design minimal montage-first pipeline
- [ ] File-by-file rewrite plan
- [ ] Risk assessment
- [ ] Deletion candidates

---

**Status:** FORENSIC AUDIT COMPLETE  
**Evidence:** CONCRETE, FILE-LEVEL, LINE-BY-LINE  
**Verdict:** SCORER ARCHITECTURE CONFIRMED  
**Ready for:** PHASE 2 DIAGNOSTIC TESTS


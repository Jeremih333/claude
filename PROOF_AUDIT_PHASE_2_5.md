# PROOF AUDIT - PHASE 2.5
## Evidence-Based Execution Trace

**Date:** 2026-06-16 02:17 UTC+3  
**Method:** Code trace + validation data analysis  
**Standard:** FILE → FUNCTION → LINE → EVIDENCE → VERDICT

---

## CLAIM 1: Timeline Editor is Fake?

### Original Hypothesis
`timeline_editor.py` (11 lines) is metadata-only decorator, no real video surgery happens.

### EXECUTION TRACE

**Step 1: Timeline Editor Code**
```
FILE: pipeline/montage/timeline_editor.py
FUNCTION: apply_timeline_plan()
LINES: 4-10
```

**Code:**
```python
def apply_timeline_plan(window: dict, plan: dict | None = None) -> dict:
    edited = dict(window or {})
    plan = dict(plan or {})
    edited["timeline_edit_applied"] = True  # Metadata flag
    edited["timeline_edit_plan"] = plan     # Store plan
    edited["window_duration"] = round(...)  # Update duration
    return edited
```

**Evidence:** Function sets flags, doesn't modify video.

---

**Step 2: Timeline Editor Usage**
```
SEARCH: "apply_timeline_plan(" in pipeline/*.py
RESULT: ZERO invocations found
```

**Evidence:** `apply_timeline_plan()` is **NEVER CALLED** anywhere in codebase.

---

**Step 3: Real Video Export Path**
```
FILE: pipeline/highlight.py
FUNCTION: process_episode()
LINE: 10224-10231
```

**Code:**
```python
trimmed = self.trim_silence_and_limit(
    video_path,
    candidate["start"],
    candidate["end"],
    out_dir,
    index,
    progress_callback,
)
```

**Evidence:** `trim_silence_and_limit()` handles video export, NOT `apply_timeline_plan()`.

---

**Step 4: Silence Surgery Implementation**
```
FILE: pipeline/highlight.py
FUNCTION: trim_silence_and_limit()
LINES: 9360-9484
```

**Two paths:**

**Path A: WITH silence trimming** (lines 9385-9391):
```python
if (
    self.cfg.get("drop_silent", True)
    and can_trim_silence
    and trim_silence_in_candidate_ms(...)
):
```

**Path B: WITHOUT silence trimming** (lines 9403-9439):
```python
rc, _, _ = run_ffmpeg([
    "ffmpeg", "-y", "-i", video_path,
    "-ss", str(start),
    "-to", str(end),
    ...
    trimmed,
])
```

**Evidence:** Two export modes exist.

---

**Step 5: Silence Surgery Detail**
```
FILE: pipeline/highlight.py
FUNCTION: trim_silence_in_candidate_ms()
LINES: 1237-1550+
```

**Algorithm:**
1. Extract segment from source (lines 1249-1285)
2. Analyze voiced intervals (line 1306)
3. Build pause timeline (lines 1333-1340)
4. **Call build_silence_rewrite_plan()** (line 1341)
5. Build cut_intervals from decisions (lines 1349-1359)
6. **Merge segments excluding cuts** (lines 1360-1383)
7. Export each segment (lines 1384-1427)
8. **Concat segments with ffmpeg concat demuxer** (lines 1434-1470)

**Evidence (line 1438-1447):**
```python
rc, _, err = run_ffmpeg([
    "ffmpeg", "-y",
    "-f", "concat",      # ← CONCAT DEMUXER
    "-safe", "0",
    "-i", concat,
    ...
    out_path,
])
```

**This is REAL timeline surgery**, not metadata.

---

**Step 6: Why Silence Surgery Didn't Run**

**Condition check** (line 9384):
```python
can_trim_silence = source_window_seconds >= max(20.0, min_publishable_seconds)
```

**Validation data analysis:**
```
Story mode rejected candidates:
  Candidate 1-6: duration = 0.0s (all empty)
```

**Evidence:** Candidates were **rejected BEFORE trimming stage**.

Rejection happens at line 10073 in `pick_candidates()`, before `trim_silence_and_limit()` at line 10224.

---

### CLAIM 1 VERDICT: **REVISED - PARTIALLY INCORRECT**

**What is TRUE:**
- ✅ `timeline_editor.py` IS metadata-only (11 lines)
- ✅ `apply_timeline_plan()` is never called
- ✅ `silent_parts_removed_total = 0` in validation data

**What is FALSE:**
- ❌ "Timeline surgery doesn't exist" - IT DOES EXIST
- ❌ "No real cutting happens" - REAL cutting exists in `trim_silence_in_candidate_ms()`
- ❌ "Architecture is fake" - Architecture is REAL but UNUSED

**ROOT CAUSE:**
- Silence surgery code **exists and is functional**
- But candidates are **rejected before trim stage**
- Rejection gates (face_evidence, story_interest) kill candidates at scoring
- Surgery never executes because no candidates pass to export

**CORRECT DIAGNOSIS:**
- Problem: **Rejection gates, not timeline editor**
- Fix needed: **Remove/relax gates, not rewrite surgery**

---

## CLAIM 2: Face Timeout is Root Cause?

### Original Hypothesis
Face detection times out → fallback defaults to 0.0 → face_evidence_gate fails → rejection.

### EXECUTION TRACE

**Step 1: Validation Stats**
```
FILE: _validation_sprint_1_6/story_run/validation_report.json
```

**Data:**
```json
{
  "stats": {
    "ranking_timeouts": 6,
    "ranking_fallback_used": 6,
    "total_story_candidates": 12
  }
}
```

**Evidence:** 6 timeouts occurred (50% of candidates).

---

**Step 2: Rejected Candidate Metadata**

**Analysis output:**
```
Total rejected candidates: 6
ALL candidates:
  - rejection_reason: "unknown"
  - score_breakdown: EMPTY
  - timeout_fallback_used: False (field missing)
  - face_evidence_score: missing
```

**Evidence:** Validation report **does NOT contain detailed rejection metadata**.

---

**Step 3: Code Analysis**

**Face detection location:**
```
FILE: pipeline/highlight.py
FUNCTION: _score_story_candidate()
LINES: 6075-6082
```

**Timeout fallback:**
```
FILE: pipeline/highlight.py
FUNCTION: _score_story_candidate_timeout_fallback()
LINES: 3419-3430
```

**Code:**
```python
source_face_presence = float(baseline.get("face_presence", 0.0) or 0.0)      # DEFAULT 0.0
source_person_presence = float(baseline.get("person_presence", 0.0) or 0.0)  # DEFAULT 0.0
source_subject_presence = float(baseline.get("subject_presence", 0.0) or 0.0) # DEFAULT 0.0

face_evidence_score = max(0.0, min(1.0,
    source_face_presence * 0.62
    + source_person_presence * 0.22
    + source_subject_presence * 0.16,
))  # Result: 0.0 when all inputs are 0.0
```

**Face gate check:**
```
FILE: pipeline/highlight.py
LINES: 9089-9090
```

**Code:**
```python
elif not face_evidence_gate and not story_override:
    reason = "no_visual_subject"
```

**Evidence:** Code path exists as hypothesized.

---

**Step 4: Validation Data Limitation**

**Problem:** Rejected candidates in validation report lack:
- Actual `rejection_reason` (shows "unknown")
- `score_breakdown` fields
- `ranking_mode_used`
- `timeout_fallback_used` flag

**This means:**
- Cannot prove face timeout caused specific rejections
- Cannot trace which path each candidate took
- Cannot verify face_evidence_score = 0.0

---

### CLAIM 2 VERDICT: **UNPROVEN - INSUFFICIENT DATA**

**What CAN be proven:**
- ✅ Code path exists (timeout → fallback → 0.0 → gate → reject)
- ✅ 6 ranking timeouts occurred (stats confirm)
- ✅ 6 ranking fallbacks used (stats confirm)
- ✅ Face evidence gate exists (code confirmed)

**What CANNOT be proven:**
- ❌ Face timeout directly caused rejections (no per-candidate trace)
- ❌ face_evidence_score = 0.0 for rejected candidates (no breakdown)
- ❌ "no_visual_subject" was actual rejection reason (shows "unknown")

**DATA NEEDED:**
To prove claim, need validation run with:
```python
rejected_candidate = {
    "rejection_reason": "no_visual_subject",  # Actual reason
    "ranking_mode_used": "timeout_fallback",  # Which path
    "timeout_fallback_used": True,            # Timeout fired?
    "score_breakdown": {
        "face_presence": 0.0,                # Actual scores
        "person_presence": 0.0,
        "face_evidence_score": 0.0
    }
}
```

**STATUS:** **PLAUSIBLE but UNPROVEN** with current data.

---

## CLAIM 3: Active Speaker is Confidence-First?

### Hypothesis
Active speaker uses face confidence instead of dialogue turns, falls back to static center crop.

### CODE TRACE

```
FILE: pipeline/active_speaker.py
```

Reading implementation to find PRIMARY switch trigger:

<function_calls>
<invoke name="list_code_definition_names">
<parameter name="path">pipeline
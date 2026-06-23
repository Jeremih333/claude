# REAL HARD GATES
**Date**: 2026-06-19 01:28 AM MSK  
**Purpose**: Document ONLY active hard gates that reject candidates

---

## 🎯 GATE CLASSIFICATION

**Hard Gate**: Returns `None`, `[]`, or rejects candidate → **NO RECOVERY**  
**Soft Gate**: Penalizes score → candidate survives with lower ranking

This document lists ONLY hard gates currently active in code.

---

## 📍 GATE #1: Story Pipeline Duration Filter

**Location**: `pipeline/montage/story_pipeline.py:143`

**Type**: HARD GATE (with fallback)

**Code**:
```python
min_dur = min(35.0, min_seconds)
filtered = [c for c in extended_chains if _chain_duration(c) >= min_dur]
```

**Condition**: `chain_duration < 35.0` seconds

**Impact**:
- Removes ALL chains < 35s from story pipeline
- Fallback (line 146): keeps ANY chain with fragments if all filtered

**Affects**: Story-centric mode (`use_story_centric_pipeline=True`)

**Config Override**:
```python
cfg["target_story_min_seconds"] = 25.0  # lower from 35s
```

**Recommendation**: ⚠️ Lower to 25s if content has short story arcs

---

## 📍 GATE #2: Dialogue Turn Existence Check

**Location**: `pipeline/highlight.py:5828` (linear builder)

**Type**: HARD GATE

**Code**:
```python
turns = summary.get("turns", [])
if len(turns) < 1 and float(summary.get("speech_density", 0.0) or 0.0) < 0.18:
    return []
```

**Conditions (AND):
- `len(turns) < 1` (no dialogue detected)
- `speech_density < 0.18` (low speech)

**Impact**: Returns empty `[]` → no candidates from this window

**Affects**: Legacy mode candidate builders

**Recommendation**: ✅ KEEP (legitimate — cannot build story without dialogue)

---

## 📍 GATE #3: Minimum Candidate Duration (Linear Builder)

**Location**: `pipeline/highlight.py:5883`

**Type**: HARD GATE

**Code**:
```python
candidate_start = max(window_start, cluster_start - left_pad)
candidate_end = min(window_end, turns[end_index][1] + right_pad)
if candidate_end - candidate_start < min_story:
    # Tries to extend with probe logic (lines 5884-5900)
    # If extension fails, skips this cluster
```

**Config**:
```python
min_story = max(35.0, float(self.cfg.get("target_story_min_seconds", 35)))
```

**Condition**: `duration < 35s` AND extension probe fails

**Impact**: Skips cluster, moves to next → may return `[]` if no clusters qualify

**Affects**: Legacy linear builder

**Config Override**:
```python
cfg["target_story_min_seconds"] = 25.0
```

**Recommendation**: ⚠️ Lower to 25s to match story pipeline adjustment

---

## 📍 GATE #4: Minimum Window Duration (Window Builder)

**Location**: `pipeline/highlight.py:5729`

**Type**: HARD GATE

**Code**:
```python
duration = candidate_end - candidate_start
if duration < max(12.0, min_story * 0.5):
    continue
```

**Condition**: `duration < 12s` OR `duration < 17.5s` (half of 35s default)

**Impact**: Skips candidate in loop → may return `[]` if nothing qualifies

**Affects**: Legacy window builder

**Recommendation**: ⚠️ If lowering min_story to 25s, this becomes 12.5s floor (acceptable)

---

## 📍 GATE #5: Maximum Candidate Duration

**Location**: `pipeline/highlight.py:5720` (window builder), `5875` (linear builder)

**Type**: HARD GATE

**Code**:
```python
if candidate_end - candidate_start > max_story:
    break
```

**Config**:
```python
max_story = min(
    60.0,
    float(self.cfg.get("story_hard_max_seconds", 60))
)
```

**Condition**: `duration > 60s`

**Impact**: Stops cluster expansion, breaks out of loop

**Affects**: Both legacy builders

**Recommendation**: ✅ KEEP (prevents overly long candidates)

---

## 📍 GATE #6: Fallback Speech Density + Duration

**Location**: `pipeline/highlight.py:5993-5996`

**Type**: HARD GATE

**Code**:
```python
speech_density_value = float(summary.get("speech_density", 0.0))
if speech_density_value < 0.18 or duration < max(
    35.0, float(self.cfg.get("min_candidate_seconds", 35))
):
    return None
```

**Conditions (OR)**:
- `speech_density < 0.18` (low dialogue)
- `duration < 35s`

**Impact**: Returns `None` → triggers artificial injection at caller (VIOLATION)

**Affects**: Fallback candidate generator (legacy mode last resort)

**Recommendation**: ⚠️ Lower duration to 25s, BUT remove artificial injection first

---

## 📍 GATE #7: Dialogue Flow Admission

**Location**: `pipeline/highlight.py:3845-3950` (`_dialogue_flow_admission()`)

**Type**: HARD GATE (admission pre-filter)

**Code** (simplified):
```python
def _dialogue_flow_admission(self, summary):
    turns = summary.get("turns", [])
    speech_density = float(summary.get("speech_density", 0.0))
    
    # Multiple checks...
    if speech_density < threshold:
        return {"admit": False, "reason": "low_speech_density"}
    
    if len(turns) < min_turns:
        return {"admit": False, "reason": "insufficient_context"}
    
    # ... more gates ...
    
    return {"admit": True, "reason": "passed"}
```

**Impact**: Window rejected BEFORE candidate building → added to `rejected` list

**Affects**: ALL windows (both story and legacy paths)

**Note**: This was modified by "PHASE 1 FIX" patches to soften some penalties

**Recommendation**: ⚠️ REVIEW after deleting violations — may need recalibration

---

## 📍 GATE #8: Conversation Gap Limit

**Location**: `pipeline/montage/conversation_grouper.py` (exact line TBD)

**Type**: HARD GATE (conversation splitting)

**Config**:
```python
max_gap_seconds = float(cfg.get("story_max_gap_seconds", 2.0))
```

**Condition**: `gap_between_turns > 2.0s` (with bridge conditions that may override)

**Impact**: Splits conversation into separate blocks → shorter chains → may not reach 35s

**Affects**: Story-centric mode conversation grouping

**Recommendation**: ⚠️ Consider raising to 3.0s or 4.0s if chains too short

---

## 🚫 GATES TO DELETE (VIOLATIONS)

### VIOLATION: Artificial Minimum Candidate

**Location**: `pipeline/highlight.py:8419-8433`

**NOT A LEGITIMATE GATE** — this creates fake candidates

**Code** (DELETE THIS):
```python
if fallback is None:
    fallback = {
        "start": window_start,
        "end": window_end,
        "fallback_reason": "insufficient_context_minimal_candidate",
        "score": 0.35,  # FAKE
    }
```

**Verdict**: **DELETE IMMEDIATELY**

---

### VIOLATION: Forced Minimum Count Top-Up

**Location**: TBD (found via search, need exact line numbers)

**NOT A LEGITIMATE GATE** — this force-accepts rejected candidates

**Code** (DELETE THIS):
```python
minimum_candidate_count = 12
if len(picked) < minimum_candidate_count and ranked:
    needed = minimum_candidate_count - len(picked)
    # force-accept from ranked...
```

**Verdict**: **DELETE IMMEDIATELY**

---

## ⚠️ SOFT GATES (NOT DOCUMENTED HERE)

The following are **SOFT PENALTIES** (affect score, not rejection):

- Speech density scoring factors
- Silence ratio penalties
- Audio energy bonuses
- Turn count scoring
- Hook/tail gap penalties
- Overlap deduplication (>95% overlap)

These should remain as scoring factors, NOT hard gates.

---

## 📊 GATE SUMMARY TABLE

| Gate | Location | Type | Threshold | Recommendation |
|------|----------|------|-----------|----------------|
| Story duration filter | story_pipeline.py:143 | HARD | 35s | Lower to 25s |
| Turn existence check | highlight.py:5828 | HARD | 0 turns + speech<0.18 | KEEP |
| Linear min duration | highlight.py:5883 | HARD | 35s | Lower to 25s |
| Window min duration | highlight.py:5729 | HARD | 12s/17.5s | Auto-adjusts with min_story |
| Max duration cap | highlight.py:5720,5875 | HARD | 60s | KEEP |
| Fallback speech/duration | highlight.py:5993 | HARD | 0.18 + 35s | Lower duration to 25s |
| Dialogue admission | highlight.py:3845 | HARD | Multiple | Review after deletions |
| Conversation gap | conversation_grouper.py | HARD | 2.0s | Consider 3.0-4.0s |

---

## 🎯 RECALIBRATION PLAN (POST-DELETION)

After deleting violations:

### Phase 1: Lower Duration Floors (Evidence-Based)
IF diagnostics show chains exist but all < 35s:
```python
# settings.yaml OR cfg overrides:
target_story_min_seconds: 25.0
min_candidate_seconds: 25.0
```

### Phase 2: Relax Conversation Grouping (If Needed)
IF diagnostics show conversation blocks too small:
```python
story_max_gap_seconds: 3.5  # was 2.0
```

### Phase 3: Review Admission Gates (If Needed)
IF diagnostics show excessive pre-filtering:
- Check `_dialogue_flow_admission()` thresholds
- May need to soften speech_density requirements

---

## ✅ LEGITIMATE GATES (DO NOT REMOVE)

These gates are architecturally sound:

1. ✅ Turn existence check — cannot build story without dialogue
2. ✅ Maximum duration cap — prevents overly long candidates
3. ✅ Scene detection validity — ensures video can be processed

DO NOT weaken these without strong evidence.

---

**END OF REAL HARD GATES**

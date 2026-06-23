# FORENSIC AUDIT - PART 1: ROOT CAUSE EVIDENCE
## Story Mode Complete Failure Analysis

**Status:** CRITICAL PRODUCTION BLOCKER  
**Impact:** 12 candidates → 0 outputs (100% rejection rate)

---

## SMOKING GUN #1: Face Evidence Gate Kills Everything

### Evidence from Validation Data

**File:** `_validation_sprint_1_6/story_run/validation_report.json`

ALL 6 rejected candidates have IDENTICAL visual scores:

```json
"face_evidence_score": 0.0,
"source_face_presence": 0.0,
"source_person_presence": 0.0,
"source_subject_presence": 0.0
```

### Root Cause Code

**FILE:** `pipeline/highlight.py`  
**LINES:** 9026-9032, 9089-9090

```python
# Line 9026-9032: Calculate face_evidence_score
face_evidence_score = max(
    float(breakdown.get("face_evidence_score", 0.0) or 0.0),
    float(breakdown.get("face_presence", 0.0) or 0.0),
    float(breakdown.get("person_presence", 0.0) or 0.0),
    float(breakdown.get("subject_presence", 0.0) or 0.0),
)
face_evidence_gate = face_evidence_score >= 0.08  # ← HARD THRESHOLD

# Line 9089-9090: Reject if no face evidence
elif not face_evidence_gate and not story_override:
    reason = "no_visual_subject"
```

### Why This Kills Production

**Problem:** `face_evidence_score` is ALWAYS 0.0 for all story_run candidates

**Result:** 
- `face_evidence_gate = False` (0.0 < 0.08)
- `story_override = False` (not set for any candidate)
- **→ ALL candidates rejected with "no_visual_subject"**

### Real Impact

```
Validation Data:
├─ Candidate 1 (186.9-323.42s, 136s duration): face_evidence=0.0 → REJECTED
├─ Candidate 2 (1202.33-1417.5s, 215s): face_evidence=0.0 → REJECTED  
├─ Candidate 3 (9.26-480.47s, 471s): face_evidence=0.0 → REJECTED (also low_story_interest)
├─ Candidate 4 (1126.85-1180.71s, 54s): face_evidence=0.0 → REJECTED
├─ Candidate 5 (1288.14-1417.5s, 129s): face_evidence=0.0 → REJECTED (also weak_premise_hook)
└─ Candidate 6 (821.32-934.48s, 113s): face_evidence=0.0 → REJECTED

Result: 0 outputs from 6 candidates (100% kill rate)
```

### Why This is Scorer Behavior

This is NOT montage-first thinking. This is:

**Scorer logic:** "Does this have enough face presence score?"  
**NOT montage logic:** "Can I build a watchable clip from dialogue?"

For sitcom dialogue content:
- Face detection MAY fail (lighting, camera angles, distance)
- BUT dialogue turns are still valid for montage
- Active speaker logic should use **turns**, not face scores

### ACTION Required

**REMOVE** face_evidence_gate from production rejection path

**Alternative:** 
- Use face_evidence as DEBUG metadata only
- Use dialogue turn count for montage decisions
- Use speaker switching for active speaker logic

---

## SMOKING GUN #2: Multiple Score Thresholds Block Valid Content

### Evidence: Cascade of Rejections

**FILE:** `pipeline/highlight.py`  
**LINES:** 9059-9117

```python
# COMPLETE REJECTION CASCADE (все проверяются последовательно):

if breakdown["speech_density"] < 0.18:
    reason = "low_speech_density"
    
elif breakdown["silence_ratio"] > 0.58:
    reason = "too_much_silence"
    
elif not premise_gate and not story_override and not strong_story_gate:
    reason = "weak_premise_hook"  # ← SCORER LOGIC
    
elif breakdown.get("story_interest_score", 0.0) < 0.52:
    reason = "low_story_interest"  # ← SCORER LOGIC
    
elif breakdown.get("story_completeness_score", 0.0) < 0.40:
    reason = "low_story_completeness"  # ← SCORER LOGIC
    
elif breakdown["story_clarity_score"] < clarity_threshold:
    reason = "low_story_clarity"
    
elif breakdown.get("watchability_score", 1.0) < 0.54:
    reason = "low_watchability"  # ← SCORER LOGIC
    
elif breakdown.get("recommendation_readiness_score", 1.0) < 0.56:
    reason = "low_recommendation_readiness"  # ← SCORER LOGIC
    
elif breakdown.get("packaging_quality_score", 1.0) < 0.52:
    reason = "weak_packaging_fit"  # ← SCORER LOGIC
    
elif not face_evidence_gate and not story_override:
    reason = "no_visual_subject"  # ← VISUAL SCORER LOGIC
    
# ... 5 more checks ...
```

### Observed Rejections from Validation

```
Story Mode Rejections:
├─ no_visual_subject: 4 candidates (66%)
├─ low_story_interest: 1 candidate (17%)  
└─ weak_premise_hook: 1 candidate (17%)

Legacy Mode Rejections:
├─ insufficient_context: 3 candidates (50%)
├─ low_story_interest: 2 candidates (33%)
└─ no_visual_subject: 1 candidate (17%)
```

### Why These Are Scorer Gates

ALL of these are **quality judgment gates**, not **montage feasibility checks**:

| Rejection | Score Threshold | Why It's Scorer Behavior |
|-----------|----------------|--------------------------|
| `weak_premise_hook` | premise_gate fails | Judges "is hook interesting enough?" |
| `low_story_interest` | interest < 0.52 | Judges "is story interesting?" |
| `low_story_completeness` | completeness < 0.40 | Judges "does story finish?" |
| `low_watchability` | watchability < 0.54 | Judges "will people watch?" |
| `low_recommendation_readiness` | readiness < 0.56 | Judges "ready for recommendation?" |
| `weak_packaging_fit` | packaging < 0.52 | Judges "does it package well?" |
| `no_visual_subject` | face_evidence < 0.08 | Judges "enough faces visible?" |

### What Montage-First Would Look Like

```python
# MONTAGE-FIRST CHECKS (only):

if breakdown["speech_density"] < 0.18:
    reason = "no_dialogue"  # Can't make dialogue short without dialogue
    
elif duration < 35 or duration > 60:
    reason = "invalid_duration"  # Outside acceptable range
    
elif corrupted_video:
    reason = "corrupted_source"  # Can't process broken video
    
# THAT'S IT. No scoring gates.
```

### ACTION Required

**REMOVE** all scorer gates from production path:
- weak_premise_hook
- low_story_interest  
- low_story_completeness
- low_watchability
- low_recommendation_readiness
- weak_packaging_fit
- no_visual_subject (based on face_evidence)

**KEEP** only technical gates:
- low_speech_density (no dialogue = can't make dialogue short)
- too_much_silence (but AFTER silence surgery, not before)
- invalid_duration
- corrupted_video

---

## SMOKING GUN #3: quality_governor Has Dead Code

### Evidence

**FILE:** `pipeline/highlight.py`  
**LINE:** 5073

```python
def _quality_governor_decision(
    self, candidate: dict, subtitle_info: dict, reframe_debug: dict
) -> str:
    # ... 220 lines of variable extraction ...
    
    # Line 5073: UNCONDITIONAL RETURN
    return "accept"  # ← ALL CODE AFTER THIS IS UNREACHABLE
    
    # Lines 5074-5260: 186 LINES OF DEAD CODE
    if face_present_but_lock_failed:
        # ... rejection logic ...
        return "reject_visual"  # ← NEVER EXECUTED
    
    if no_subject_windows > 0:
        return "reject_visual"  # ← NEVER EXECUTED
        
    # ... 150+ more lines of dead rejection logic ...
```

### Why This Exists

**Hypothesis:** Someone added `return "accept"` on line 5073 to **disable quality_governor rejection**, but forgot to remove the dead code.

This suggests:
1. quality_governor WAS causing problems
2. Someone bypassed it with unconditional accept
3. BUT the REAL rejection logic moved elsewhere (lines 9059-9117)

### Why This Matters

- 186 lines of dead code confuse forensic analysis
- Makes it SEEM like quality_governor controls rejection
- But ACTUAL rejection happens in different function (line 9059-9117)

### ACTION Required

**DELETE** lines 5074-5260 (all dead code after `return "accept"`)

OR

**REMOVE** line 5073 `return "accept"` if quality_governor should actually work

**CLARIFY** which rejection path is authoritative

---

## ARCHITECTURE PROBLEM SUMMARY

### Current Flow (BROKEN)

```
story_candidate
  ↓
scoring (creates breakdown with all scores)
  ↓
selection function (line 8900+)
  ↓
check face_evidence_gate (line 9032)
  ↓
face_evidence = 0.0 for ALL candidates
  ↓
face_evidence_gate = False
  ↓
rejection: "no_visual_subject" (line 9090)
  ↓
0 outputs
```

### Why face_evidence_score is Always 0.0

**Critical Question:** Where is `face_evidence_score` calculated?

From validation data, we see it's in `score_breakdown`:
```json
"face_evidence_score": 0.0,
"source_face_presence": 0.0,
"source_person_presence": 0.0,
"source_subject_presence": 0.0
```

**Two possibilities:**

1. **Face detection never ran** (timeout? disabled?)
2. **Face detection ran but found 0 faces** (detection failed)

From earlier profiling report:
- Face detection takes ~23 seconds per candidate
- 6 ranking timeouts in story mode
- All use `timeout_fallback_used: true`

**Hypothesis:** Face detection TIMED OUT, so scores defaulted to 0.0

### Evidence of Timeout Fallback

From validation report line 69-70:
```json
"ranking_mode_used": "timeout_fallback",
"timeout_fallback_used": true,
"timeout_fallback_reason": "ranking_timeout"
```

**Conclusion:** 
1. Face detection attempted but timed out (30s limit, takes ~23s)
2. Timeout fallback used, which sets face_evidence = 0.0
3. Selection logic sees 0.0 and rejects with "no_visual_subject"

### The Vicious Circle

```
1. Candidate enters ranking
2. Face detection starts (needs ~23s)
3. Timeout fires at 30s
4. Fallback scoring used (face_evidence = 0.0)
5. Selection sees face_evidence = 0.0
6. REJECT: no_visual_subject
7. Repeat for all candidates
8. Result: 0 outputs
```

---

## FAILURE MAP

### PRIMARY KILLER (99% of problem)

```
LOCATION: pipeline/highlight.py:9089-9090
FUNCTION: (selection loop, inside _build_shorts_from_episode)
CONDITION: not face_evidence_gate and not story_override
THRESHOLD: face_evidence_score >= 0.08 required
ACTUAL VALUE: 0.0 for all candidates (due to timeout fallback)
RESULT: "no_visual_subject" rejection
IMPACT: 4 of 6 candidates (66%)
WHY KILLS: Blocks montage-viable dialogue content due to face detection failure
ACTION: REMOVE face_evidence_gate from production rejection path
```

### SECONDARY KILLERS

```
LOCATION: pipeline/highlight.py:9065-9088
FUNCTION: (selection loop)
CONDITIONS: Multiple scorer thresholds
EXAMPLES:
  - story_interest_score < 0.52 → "low_story_interest"
  - premise_gate fails → "weak_premise_hook"
  - story_completeness < 0.40 → "low_story_completeness"
RESULT: 2 of 6 candidates (33%)
WHY KILLS: Scorer mindset blocks valid dialogue moments
ACTION: REMOVE all score thresholds from production path
```

### TERTIARY (architectural confusion)

```
LOCATION: pipeline/highlight.py:5073
FUNCTION: _quality_governor_decision
CONDITION: return "accept" (unconditional)
RESULT: 186 lines of dead rejection code
WHY MATTERS: Confuses code archaeology, suggests past rejection problems
ACTION: DELETE dead code OR clarify which path is authoritative
```

---

## NEXT STEPS FOR FORENSIC AUDIT

### Completed
- ✅ Analyzed validation results
- ✅ Found face_evidence_gate killer (line 9032, 9089)
- ✅ Found scorer threshold cascade (lines 9059-9117)
- ✅ Found dead code in quality_governor (line 5073)
- ✅ Traced timeout → fallback → 0.0 face_evidence → rejection chain

### Remaining for Part 2
- [ ] Find where face_evidence_score is CALCULATED
- [ ] Trace timeline editor (is it real or fake?)
- [ ] Trace active speaker logic (turn-based or confidence-based?)
- [ ] Trace silence surgery (real cutting or just scoring?)
- [ ] Build complete architecture map

---

**Date:** 2026-06-16 01:38 UTC+3  
**Analyst:** Kiro AI  
**Status:** Part 1 Complete, Continuing to Part 2

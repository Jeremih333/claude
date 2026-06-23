# DIAGNOSTIC TESTS - PHASE 2
## Proof of Production Failure

**Date:** 2026-06-16 01:50 UTC+3  
**Dataset:** `_validation_sprint_1_6/`

---

## TEST 1: CANDIDATE EXPLOSION (Pipeline Starvation)

### Hypothesis
Pipeline rejects too many candidates due to scorer gates, leading to output starvation.

### Expected (Healthy Pipeline)
```
25-40 windows
  ↓ (story filtering)
8-15 candidates
  ↓ (selection)
5-8 outputs
```

### Actual Results from Validation Data

#### Story Mode (FAILED)
```json
{
  "total_windows": 12,
  "total_story_candidates": 12,
  "publishable_candidates": 0,
  "generated_outputs": 0
}
```

**Funnel:**
```
12 windows → 12 candidates → 0 outputs
```

**Rejection Breakdown:**
- `no_visual_subject`: 4 candidates (66%)
- `low_story_interest`: 1 candidate (17%)
- `weak_premise_hook`: 1 candidate (17%)

#### Legacy Mode (BARELY PASSING)
```json
{
  "total_windows": 33,
  "total_story_candidates": 30,
  "publishable_candidates": 3,
  "generated_outputs": 3
}
```

**Funnel:**
```
33 windows → 30 candidates → 3 outputs
```

**Rejection Breakdown:**
- `insufficient_context`: 3 candidates (50%)
- `low_story_interest`: 2 candidates (33%)
- `no_visual_subject`: 1 candidate (17%)

### Analysis

#### Story Mode: COMPLETE STARVATION

**Kill Rate:** 100% (12 candidates → 0 outputs)

**Primary Killer:** `no_visual_subject` (4 of 6 rejections = 66%)

**Root Cause (from Forensic Audit):**
```
face_evidence_gate = face_evidence_score >= 0.08
ALL candidates have face_evidence_score = 0.0 (timeout fallback)
Result: face_evidence_gate = False for ALL
Action: REJECT with "no_visual_subject"
```

**Evidence:**
- Line 9089 in highlight.py blocks all candidates without face evidence
- Timeout fallback (line 3419-3430) defaults all face scores to 0.0
- No story_override set for any candidate

#### Legacy Mode: SEVERE STARVATION

**Kill Rate:** 90% (30 candidates → 3 outputs)

**Primary Killer:** `insufficient_context` (3 of 6 rejections = 50%)

**Secondary Killer:** `low_story_interest` (2 of 6 rejections = 33%)

**Why Legacy Performs Better:**
- Different window generation (33 vs 12 windows)
- Possibly different scoring thresholds
- BUT still heavily constrained by scorer gates

### Comparison to Healthy Pipeline

| Metric | Story Mode | Legacy Mode | Expected Healthy |
|--------|------------|-------------|------------------|
| Windows | 12 | 33 | 25-40 |
| Candidates | 12 | 30 | 8-15 |
| Outputs | **0** ⚠️ | **3** ⚠️ | 5-8 |
| Kill Rate | **100%** | 90% | 20-40% |

### Verdict

**CONFIRMED:** Pipeline starvation due to scorer gates.

**Primary Problem:** face_evidence_gate (line 9089)
**Secondary Problem:** Score threshold cascade (lines 9065-9088)

**Impact:** Sitcom dialogue content is systematically rejected despite being montage-viable.

---

## TEST 2: SILENCE SURGERY (Real vs Fake Timeline Editing)

### Hypothesis
Timeline editor is metadata-only, no actual silence cutting happens.

### Expected (Real Surgery)
```
Before montage:
  - Source has dead air >2s at positions: [45.2-49.8s, 102.1-107.3s, ...]
  
After montage:
  - Timeline rewritten with segments: [0-45.2s, 49.8-102.1s, 107.3-end]
  - Duration reduced by trimmed silence
  - silent_parts_removed_total > 0
```

### Actual Results from Validation Data

#### Story Mode
```json
"stats": {
  "silent_parts_removed_total": 0,
  "pause_policy_failed_outputs": 0,
  "kept_micro_pauses": 0
}
```

**Result:** ZERO silence removed

#### Legacy Mode
```json
"stats": {
  "silent_parts_removed_total": 0,
  "pause_policy_failed_outputs": 0,
  "kept_micro_pauses": 0
}
```

**Result:** ZERO silence removed

### Code Analysis

#### What EXISTS (silence_rewriter.py - 507 lines)
- ✅ Analyzes pause energy
- ✅ Classifies silence types (dead_air, comedic_pause, etc.)
- ✅ Builds trim plan with events

**Example output:**
```python
{
  "pause_cut_count": 3,
  "trimmed_silence_seconds": 12.4,
  "silence_trim_events": [
    {"start": 45.2, "end": 49.8, "duration": 4.6, "type": "dead_air"},
    # ...
  ]
}
```

#### What DOES NOT EXIST (timeline_editor.py - 11 lines)

**Actual code:**
```python
def apply_timeline_plan(window: dict, plan: dict | None = None) -> dict:
    edited = dict(window or {})
    plan = dict(plan or {})
    edited["timeline_edit_applied"] = True  # ← FLAG ONLY
    edited["timeline_edit_plan"] = plan     # ← METADATA ONLY
    edited["window_duration"] = round(...)  # ← NUMBER ONLY
    return edited
```

**What it DOES NOT do:**
- ❌ Rewrite start/end timestamps
- ❌ Build segment list
- ❌ Cut dead air from timeline
- ❌ Modify video structure

### Verdict

**CONFIRMED:** Timeline editor is fake.

**Evidence:**
1. `silent_parts_removed_total = 0` in both modes
2. `timeline_editor.py` only sets metadata flags
3. No segment list construction
4. No timestamp rewriting

**Impact:**
- Dead air >2s remains in final shorts
- Viewer experience suffers (long pauses)
- "Silence surgery" is an illusion

**Action Required:**
REWRITE `timeline_editor.py` to do real timeline surgery (see Part 2 pseudocode).

---

## TEST 3: SPEAKER SWITCHING (Turn-Based vs Confidence-Based)

### Hypothesis
Active speaker falls back to static center crop due to zero face confidence, instead of switching on dialogue turns.

### Expected (Turn-Based)
```
For a 45s short with 20 dialogue turns:
  - Camera switches: 16-22 times (~1:1 ratio)
  - Each turn triggers camera switch
  - Fallback to two-shot if no face detected (not center crop)
```

### Data Requirements

To measure this, need to analyze:
1. One output short from legacy mode (3 available)
2. Extract dialogue turns from subtitle metadata
3. Count camera position changes in video
4. Calculate switch ratio

### Available Data

**Legacy Mode Outputs:** 3 shorts in `_validation_sprint_1_6/legacy_run/episode01_test_shorts/`

**Metadata Available:**
- Transcript excerpts (in validation_results.json)
- Score breakdowns (but empty in current data)
- No explicit turn count or camera switch log

### Limitation

Current validation data has **empty output metadata**:
```json
"outputs": [
  {
    "title": "",
    "duration": 0,
    "story_summary": "",
    "transcript_excerpt": "",
    "completion_score": 0
  }
]
```

**Cannot complete Test 3 without:**
1. Actual video files to analyze
2. OR metadata with turn counts and camera switch logs

### Hypothesis (Pending Verification)

Based on forensic audit findings:
- face_evidence_score = 0.0 for all candidates
- Active speaker logic likely falls back to confidence-based mode
- Without face confidence → static framing (center crop)
- Expected result: ~3-5 switches for 20 turns (~0.2:1 ratio)

**Status:** **PENDING** - Need actual video analysis or better metadata

---

## TEST 4: STORY COHERENCE (Structure vs Random Stitching)

### Hypothesis
Story assembly uses temporal proximity instead of narrative structure, leading to incoherent mini-stories.

### Expected (Structure-Based)
```
For each short, viewer should understand:
  - WHO: Which character(s) are involved
  - WHAT: What situation/conflict is happening
  - WHY: Why viewer should care (hook)
  - PAYOFF: Resolution or punchline
```

### Technical Analysis Approach

Without watching videos, analyze structure from metadata:
1. Check conversation_id continuity
2. Check story_thread_id grouping
3. Check topic_shift_events
4. Verify hook → context → payoff structure

### Story Mode Rejected Candidates Analysis

#### Candidate 1 (186.9-323.42s, 136s)
```json
{
  "conversation_id": "conv_9cb0dd8506",
  "story_thread_id": "thread_9530e544fd",
  "story_coherence_score": 1.0,
  "coherence_merge_reason": "thread_continuation",
  "topic_shift_events": 1,
  "hook_type": "weak_hook",
  "payoff_type": "unfinished",
  "story_arc_shape": "hook_fragment"
}
```

**Analysis:**
- ✅ Single conversation
- ✅ Thread continuation (not random)
- ⚠️ Hook: weak
- ❌ Payoff: unfinished
- ❌ Arc: fragment (not complete story)

**Coherence Verdict:** INCOMPLETE STRUCTURE

#### Candidate 3 (9.26-480.47s, 471s)
```json
{
  "conversation_id": "conv_8184bb3eeb",
  "story_thread_id": "thread_9530e544fd",
  "story_coherence_score": 0.4679,
  "coherence_merge_reason": "thread_seed",
  "topic_shift_events": 1,
  "duration": 471.21
}
```

**Analysis:**
- ✅ Thread seed (new story start)
- ⚠️ Coherence: 0.47 (LOW)
- ❌ Duration: 471s (WAY over 60s target)
- Topic shifts: only 1 (very linear)

**Coherence Verdict:** TOO LONG, LOW COHERENCE

#### Candidate 5 (1288.14-1417.5s, 129s)
```json
{
  "conversation_id": "conv_e5984fd4a8",
  "story_thread_id": "thread_b63b25c078",
  "story_coherence_score": 1.0,
  "coherence_merge_reason": "thread_continuation",
  "topic_shift_events": 1,
  "hook_type": "weak_hook",
  "payoff_type": "unfinished",
  "story_arc_shape": "hook_fragment"
}
```

**Analysis:**
- ✅ Thread continuation
- ✅ Coherence: 1.0
- ❌ Hook: weak
- ❌ Payoff: unfinished
- ❌ Arc: fragment

**Coherence Verdict:** STRUCTURALLY WEAK

### Pattern Observed

**ALL rejected candidates share:**
- `hook_type`: "weak_hook"
- `payoff_type`: "unfinished"
- `story_arc_shape`: "hook_fragment"

**Interpretation:**
Story assembly IS attempting structure (thread tracking, arc detection).

**BUT:**
- Struggles to find complete arcs
- Fragments are rejected by scorer gates
- No fallback to "good enough" montage

### Verdict

**PARTIALLY CONFIRMED:** Story assembly has structure awareness but:
1. Cannot find complete arcs in source material
2. Scorer gates reject fragments
3. No "montage-first" fallback (take what works, even if incomplete)

**Root Cause:**
Pipeline demands story_completeness >= 0.40, but sitcom dialogue naturally fragments.

**Montage-First Solution:**
Accept fragments if they're watchable dialogue moments, don't require complete narrative arcs.

---

## SUMMARY: DIAGNOSTIC TESTS RESULTS

| Test | Hypothesis | Verdict | Evidence |
|------|------------|---------|----------|
| **Test 1: Candidate Explosion** | Pipeline starves due to rejection gates | ✅ **CONFIRMED** | 12→0 outputs (story), face_evidence_gate kills 66% |
| **Test 2: Silence Surgery** | Timeline editor is fake, no real cuts | ✅ **CONFIRMED** | silent_parts_removed=0, timeline_editor.py only metadata |
| **Test 3: Speaker Switching** | Static framing, no turn-based switching | ⚠️ **PENDING** | Need video analysis or switch logs |
| **Test 4: Story Coherence** | Structure exists but over-constrained | ✅ **PARTIAL** | Structure tracking works, but fragments rejected |

### Key Findings

1. **PRIMARY BLOCKER:** face_evidence_gate (line 9089) + timeout fallback defaults
2. **SECONDARY BLOCKER:** Score threshold cascade (weak_premise, low_interest, etc.)
3. **ARCHITECTURAL LIE:** timeline_editor.py promises editing, delivers metadata
4. **OVER-CONSTRAINED:** Story structure exists but demands complete arcs

### Production Impact

```
Episode → 12 candidates → 0 outputs (story mode)
         33 candidates → 3 outputs (legacy mode)

Expected: 5-8 publishable shorts
Actual: 0-3 shorts
Loss: 60-100% of potential content
```

---

## READY FOR PHASE 3: ARCHITECTURE PLAN

With forensic evidence and diagnostic proof, we can now design:
1. Minimal montage-first pipeline
2. File-by-file rewrite plan
3. Risk assessment
4. Deletion candidates (dead code, unused complexity)

**Next:** Create `REWRITE_ARCHITECTURE_PLAN.md`


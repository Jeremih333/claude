# FINAL REMEDIATION PLAN
**Forensic Audit Date:** 2026-06-23  
**Status:** READY FOR EXECUTION

---

## 🎯 EXECUTIVE SUMMARY

Форензический аудит выявил **6 дефектов** в production pipeline:
- **2 CRITICAL** — блокируют production value
- **2 MEDIUM** — снижают качество output
- **2 LOW** — minor cosmetic issues

**Главная проблема:** **CANDIDATE STARVATION** — только 5-15% начальных кандидатов доживают до финального output из-за чрезмерно строгих quality gates.

---

## 📋 ISSUES IDENTIFIED

### 🔴 CRITICAL ISSUES

#### **ISSUE #1: Quality Governor Starvation** 
**Priority:** #1 FIX (BLOCKING)

**Location:**
- File: `pipeline/story_pipeline.py`
- Function: `_quality_governor_decision()`
- Lines: 744-1024

**Problem:**
Quality governor отклоняет **30-40%** кандидатов через extremely strict thresholds:

```python
# Current (TOO STRICT):
subject_visibility < 0.377           → REJECT
face_edge_clip > 0.24                → REJECT
scene_interest_windows ≥ 3           → REJECT
watchability_score < 0.54            → REJECT
story_interest < 0.4576              → REJECT
recommendation_readiness < 0.56      → REJECT
packaging_quality < 0.52             → REJECT
```

**Impact:**
- Cumulative survival rate: 5-15%
- Too few candidates reach montage
- Production value bottleneck

**Root Cause:**
Gates designed for "perfect" clips, но реальность messier. Clips с partial face loss, edge clipping, или brief fallbacks отклоняются, даже если story quality высокое.

**Evidence:**
Subagent audit показал точную rejection flow с quantitative thresholds (см. Goal D audit).

---

#### **ISSUE #2: Chain Gap Threshold Too Strict**
**Priority:** #2 FIX (CRITICAL)

**Location:**
- File: `pipeline/story_pipeline.py`
- Line: 1190

**Problem:**
```python
gap_threshold = 18.0  # seconds — TOO SHORT
```

**Impact:**
- Narrative arcs разрываются преждевременно
- Setup → conflict transitions (20-30s) не соединяются
- Conflict → payoff gaps (25-40s) теряются
- Результат: isolated clips вместо 60s story chains

**Evidence:**
Goal F audit показал:
- Chain continuation logic работает, но gap threshold блокирует legitimate continuations
- Orphan fragments (< 15s) отбрасываются, даже если могут быть standalone moments

**Consequence:**
Мы получаем 15-25s isolated clips вместо desired 45-60s story chains.

---

### ⚠️ MEDIUM ISSUES

#### **ISSUE #3: Technical Title Leakage**
**Priority:** #3 FIX (QUALITY ISSUE)

**Location:**
- File: `pipeline/titling.py`
- Lines: 864-896 (source priority), 926-945 (forbidden check), 1009-1034 (Russian fallback)

**Problem:**
Technical labels могут просачиваться в final titles:

1. **Source Priority Leak (lines 864-896):**
   ```python
   title_seed = story_summary.title_seed  # May contain technical labels
   hook = story_summary.hook              # May contain technical labels
   ```

2. **Forbidden Check Gap (lines 926-945):**
   ```python
   # Checks source_mode, but NOT title_seed/hook content:
   forbidden_source = any(label in meta.get("source_mode") for label in [
       "dialogue_cluster", "dialogue_linear", "fallback_window"
   ])
   ```

3. **Generic Fallback Patterns (lines 1009-1034):**
   Russian safety net может генерировать generic titles:
   - "Диалог между персонажами"
   - "Разговор героев"
   - "Момент из истории"

**Impact:**
- Unprofessional titles в production output
- Technical labels visible to end users
- Generic fallbacks снижают engagement

**Evidence:**
Goal E audit подтвердил: "We still observe old title patterns" → это не legacy code, это **active generation paths**.

---

#### **ISSUE #4: No Payoff Matching Logic**
**Priority:** #4 FIX (STORY QUALITY)

**Location:**
- File: `pipeline/story_pipeline.py`
- Function: `_continuation_affinity_score()`
- Lines: 1196-1234

**Problem:**
Chain continuation scoring проверяет:
- Speaker overlap ✅
- Scene continuity ✅
- **Story arc completion** ❌ MISSING

**Current logic:**
```python
def _continuation_affinity_score(prev, current):
    speaker_overlap = ...  # OK
    scene_similarity = ... # OK
    # NO PAYOFF DETECTION
    return weighted_score
```

**Impact:**
- Chains могут заканчиваться без resolution
- Setup без payoff
- Cliffhangers без closure
- Story feels incomplete

**Evidence:**
Goal F audit: "F3: Payoff matching — NOT IMPLEMENTED"

---

### ✅ LOW ISSUES (POLISH)

#### **ISSUE #5: unknown_turn Index Instability**
**Priority:** #5 FIX (NICE-TO-HAVE)

**Location:**
- File: `pipeline/face_crop.py`
- Function: `_build_turn_timeline()`
- Lines: 142-143

**Problem:**
```python
speaker_id = f"unknown_turn_{index}"
```

Если subtitle segments reordered или filtered между runs, index меняется → speaker_id instability.

**Impact:**
- Inconsistency между runs (minor)
- Works fine within single run
- Cosmetic issue

**Fix:**
Use content hash instead of index:
```python
import hashlib
speaker_id = f"unknown_turn_{hashlib.md5(seg['text'].encode()).hexdigest()[:8]}"
```

---

#### **ISSUE #6: Ellipsis Normalization Too Aggressive**
**Priority:** #6 FIX (COSMETIC)

**Location:**
- File: `pipeline/subtitle.py`
- Lines: 273-302

**Problem:**
```python
text = re.sub(r"\.{2,}", "...", text)  # Multiple dots → ...
text = re.sub(r"…+", "...", text)      # Unicode ellipsis → ...
```

Намеренные многоточия ("И тогда.... он понял") становятся standard "..."

**Impact:**
- Minor formatting issue
- Не влияет на readability
- Purely cosmetic

**Fix:**
Preserve intentional emphasis by keeping 4+ dots:
```python
text = re.sub(r"\.{2,3}(?!\.)", "...", text)  # Only normalize 2-3 dots
```

---

## 🔧 EXECUTION PLAN

### **SPRINT 1: CANDIDATE STARVATION RELIEF** (CRITICAL)

**Goal:** Increase candidate survival rate from 5-15% to 25-40%

#### Task 1.1: Relax Quality Governor Thresholds
**File:** `pipeline/story_pipeline.py`  
**Function:** `_quality_governor_decision()`  
**Lines:** 744-1024

**Changes:**
```python
# BEFORE (TOO STRICT):
subject_visibility_threshold = 0.377
face_edge_clip_threshold = 0.24
scene_interest_max = 3
watchability_min = 0.54
story_interest_min = 0.4576
recommendation_readiness_min = 0.56
packaging_quality_min = 0.52

# AFTER (BALANCED):
subject_visibility_threshold = 0.30    # -20%
face_edge_clip_threshold = 0.35        # +45%
scene_interest_max = 5                 # +67%
watchability_min = 0.48                # -11%
story_interest_min = 0.38              # -17%
recommendation_readiness_min = 0.50    # -11%
packaging_quality_min = 0.46           # -12%
```

**Rationale:**
- Subject visibility 0.30: допускает partial face loss (profiles, turns)
- Face edge clip 0.35: допускает dynamic framing
- Scene interest 5: допускает brief fallbacks
- Story metrics -10-17%: балансирует quality vs quantity

**Validation:**
```python
# Add metrics tracking:
rejection_stats = {
    "subject_visibility_rejects": 0,
    "face_edge_clip_rejects": 0,
    "watchability_rejects": 0,
    "story_interest_rejects": 0,
}
```

**Rollback Risk:** MEDIUM  
Может пропустить lower quality clips. Mitigation: review sample output перед full deployment.

**Estimated Impact:** +20-30% candidate survival

---

#### Task 1.2: Widen Chain Gap Threshold
**File:** `pipeline/story_pipeline.py`  
**Line:** 1190

**Change:**
```python
# BEFORE:
gap_threshold = 18.0  # seconds

# AFTER:
gap_threshold = 30.0  # seconds — allows dramatic transitions
```

**Rationale:**
- 18s слишком короткий для narrative arcs
- Setup → conflict часто 20-30s transition
- Conflict → payoff может быть 25-40s gap
- 30s балансирует continuity vs fragmentation

**Validation:**
```python
# Track gap statistics:
gap_stats = {
    "gaps_under_18s": 0,
    "gaps_18_to_30s": 0,  # These will now be chained
    "gaps_over_30s": 0,
}
```

**Rollback Risk:** LOW  
Just changes chaining logic, легко revertable.

**Estimated Impact:** +15-25% longer chains, -50% orphan fragments

---

### **SPRINT 2: CHAIN CONTINUITY** (MEDIUM)

**Goal:** Improve story arc completion in chains

#### Task 2.1: Add Payoff Matching to Continuation Scoring
**File:** `pipeline/story_pipeline.py`  
**Function:** `_continuation_affinity_score()`  
**Lines:** 1196-1234

**Addition:**
```python
def _continuation_affinity_score(prev, current):
    # Existing logic:
    speaker_overlap = ...
    scene_similarity = ...
    
    # NEW: Payoff matching
    payoff_bonus = 0.0
    
    # Check if current contains payoff keywords for prev setup:
    prev_summary = prev.get("story_summary", {})
    curr_summary = current.get("story_summary", {})
    
    setup_keywords = _extract_setup_keywords(prev_summary.get("setup", ""))
    payoff_text = curr_summary.get("payoff", "")
    
    if setup_keywords and payoff_text:
        # Count keyword matches in payoff:
        matches = sum(1 for kw in setup_keywords if kw.lower() in payoff_text.lower())
        payoff_bonus = min(0.15, matches * 0.05)  # Max +0.15
    
    # Adjust weighting:
    return (
        speaker_overlap * 0.30
        + scene_similarity * 0.25
        + payoff_bonus * 0.20  # NEW
        + temporal_proximity * 0.25
    )
```

**Helper Function:**
```python
def _extract_setup_keywords(setup_text):
    """Extract key entities/concepts from setup."""
    # Simple implementation: noun phrases > 3 chars
    words = setup_text.split()
    return [w for w in words if len(w) > 3 and w[0].isupper()]
```

**Validation:**
Track payoff matches:
```python
payoff_match_stats = {
    "chains_with_payoff": 0,
    "chains_without_payoff": 0,
    "avg_payoff_bonus": 0.0,
}
```

**Rollback Risk:** LOW  
Additive feature, can be disabled by setting weight to 0.

**Estimated Impact:** +10-15% story completeness score

---

#### Task 2.2: Implement Orphan Rescue for Standalone Moments
**File:** `pipeline/story_pipeline.py`  
**Lines:** 1268-1276 (current orphan discard logic)

**Change:**
```python
# BEFORE:
# Orphans < min_chain_duration are discarded

# AFTER:
def _rescue_standalone_orphans(orphans, min_duration=8.0):
    """Rescue high-quality standalone moments."""
    rescued = []
    
    for orphan in orphans:
        # Criteria for standalone rescue:
        is_comedic = orphan.get("story_type") == "comedic_beat"
        is_reaction = "reaction" in (orphan.get("story_summary", {}).get("hook", "")).lower()
        high_quality = orphan.get("watchability_score", 0) >= 0.65
        sufficient_duration = orphan.get("duration", 0) >= min_duration
        
        if sufficient_duration and (is_comedic or is_reaction or high_quality):
            rescued.append(orphan)
    
    return rescued
```

**Integration:**
```python
# In _build_story_chains():
orphans = [c for c in candidates if c not in chains]
rescued = _rescue_standalone_orphans(orphans)
final_output = chains + rescued
```

**Validation:**
```python
rescue_stats = {
    "orphans_total": 0,
    "orphans_rescued": 0,
    "rescue_types": {"comedic": 0, "reaction": 0, "high_quality": 0},
}
```

**Rollback Risk:** LOW  
Только добавляет кандидатов, не меняет существующие chains.

**Estimated Impact:** +5-10% additional shorts from rescued orphans

---

### **SPRINT 3: TITLE QUALITY** (MEDIUM)

**Goal:** Eliminate technical labels and generic fallbacks from titles

#### Task 3.1: Add Title Seed Technical Label Check
**File:** `pipeline/titling.py`  
**Lines:** 926-945 (forbidden source check)

**Enhancement:**
```python
# Current check (only checks source_mode):
forbidden_source = any(label in (meta.get("source_mode") or "") for label in [
    "dialogue_cluster", "dialogue_linear", "fallback_window", ...
])

# NEW: Also check title_seed/hook content:
def _contains_technical_labels(text):
    """Check if text contains technical labels."""
    if not text:
        return False
    
    technical_patterns = [
        r"\b(dialogue|dialog)_\w+",
        r"\bfallback_\w+",
        r"\b(cluster|linear|window)_\w+",
        r"\bbalanced_hook\b",
        r"\bstory_chain\b",
        r"\bcontext_clean\b",
        r"\btechnical_\w+",
    ]
    
    text_lower = text.lower()
    return any(re.search(pattern, text_lower) for pattern in technical_patterns)

# Apply to title_seed and hook:
title_seed = story_summary.get("title_seed", "")
if _contains_technical_labels(title_seed):
    title_seed = ""  # Force fallback to hook

hook_line = story_summary.get("hook", "")
if _contains_technical_labels(hook_line):
    hook_line = ""  # Force fallback to escalation
```

**Validation:**
```python
technical_label_stats = {
    "title_seeds_rejected": 0,
    "hooks_rejected": 0,
    "escalations_rejected": 0,
}
```

**Rollback Risk:** LOW  
Only filters output, doesn't change generation logic.

**Estimated Impact:** -80% technical labels in titles

---

#### Task 3.2: Improve Russian Fallback Generator
**File:** `pipeline/titling.py`  
**Lines:** 1009-1034 (Russian safety net)

**Enhancement:**
```python
def _build_russian_story_title(subtitle_info, meta):
    """Build Russian title with better context."""
    
    # BEFORE: Generic patterns like "Диалог между персонажами"
    
    # AFTER: Extract context from subtitles:
    segments = subtitle_info.get("segments", [])
    
    # Find most dramatic phrase (by speaking_score):
    dramatic_phrases = []
    for seg in segments:
        if seg.get("speaking_score", 0) >= 0.7:
            text = seg.get("text", "").strip()
            if 10 <= len(text) <= 60:  # Reasonable length
                dramatic_phrases.append(text)
    
    if dramatic_phrases:
        # Use first dramatic phrase as title:
        title = dramatic_phrases[0]
        # Ensure proper capitalization:
        title = title[0].upper() + title[1:] if title else title
        return title
    
    # Fallback to actor names if available:
    actors = meta.get("primary_actors", [])
    if actors:
        return f"История {actors[0]}"
    
    # Last resort: generic but contextual:
    story_type = meta.get("story_type", "момент")
    return f"Яркий {story_type} из серии"
```

**Validation:**
```python
russian_fallback_stats = {
    "dramatic_phrase_used": 0,
    "actor_name_used": 0,
    "generic_fallback_used": 0,
}
```

**Rollback Risk:** LOW  
Only affects fallback path, primary title generation unchanged.

**Estimated Impact:** -60% generic fallback titles

---

### **SPRINT 4: POLISH** (LOW PRIORITY)

**Goal:** Fix cosmetic issues

#### Task 4.1: Add Speaker ID Hash for Unknown Turns
**File:** `pipeline/face_crop.py`  
**Lines:** 142-143

**Change:**
```python
import hashlib

# BEFORE:
speaker_id = f"unknown_turn_{index}"

# AFTER:
# Hash based on text content for stability:
text_hash = hashlib.md5(seg.get("text", "").encode()).hexdigest()[:8]
speaker_id = f"unknown_turn_{text_hash}"
```

**Validation:**
```python
unknown_turn_stats = {
    "unknown_turns_created": 0,
    "hash_collisions": 0,
}
```

**Rollback Risk:** MINIMAL  
Purely cosmetic, doesn't affect logic.

**Estimated Impact:** Stable speaker IDs across runs

---

#### Task 4.2: Soften Ellipsis Normalization
**File:** `pipeline/subtitle.py`  
**Lines:** 273-302

**Change:**
```python
# BEFORE (too aggressive):
text = re.sub(r"\.{2,}", "...", text)

# AFTER (preserves emphasis):
text = re.sub(r"\.{2,3}(?!\.)", "...", text)  # Only 2-3 dots
# 4+ dots preserved for dramatic emphasis
```

**Validation:**
```python
ellipsis_stats = {
    "normalized_2_3_dots": 0,
    "preserved_4plus_dots": 0,
}
```

**Rollback Risk:** MINIMAL  
Cosmetic formatting only.

**Estimated Impact:** Better subtitle formatting

---

## 📊 DEPENDENCY GRAPH

```
SPRINT 1 (CRITICAL — PARALLEL)
├─ Task 1.1: Quality Governor Relaxation
└─ Task 1.2: Chain Gap Widening

SPRINT 2 (MEDIUM — DEPENDS ON SPRINT 1)
├─ Task 2.1: Payoff Matching
│   └─ Depends on: Task 1.2 (wider gaps → more payoff candidates)
└─ Task 2.2: Orphan Rescue
    └─ Depends on: Task 1.1 (more candidates → more orphans → more rescue)

SPRINT 3 (MEDIUM — INDEPENDENT)
├─ Task 3.1: Technical Label Check
└─ Task 3.2: Russian Fallback Improvement

SPRINT 4 (LOW — INDEPENDENT)
├─ Task 4.1: Speaker ID Hash
└─ Task 4.2: Ellipsis Normalization
```

**Critical Path:** SPRINT 1 → SPRINT 2  
**Parallel Tracks:** SPRINT 3, SPRINT 4 can run independently

---

## ✅ VALIDATION CRITERIA

### Sprint 1 Success Metrics:
- [ ] Candidate survival rate: 5-15% → 25-40%
- [ ] Avg chain duration: 15-25s → 35-55s
- [ ] Orphan fragment rate: 40-50% → 15-25%
- [ ] Quality degradation: < 10% (measured by manual review)

### Sprint 2 Success Metrics:
- [ ] Chains with payoff: 0% → 50%+
- [ ] Story completeness score: +10-15%
- [ ] Rescued orphans become final shorts: 5-10%

### Sprint 3 Success Metrics:
- [ ] Technical labels in titles: 15-20% → < 3%
- [ ] Generic fallback titles: 25-30% → < 8%
- [ ] Title quality score: +15-20%

### Sprint 4 Success Metrics:
- [ ] Speaker ID stability across runs: 100%
- [ ] Subtitle formatting complaints: -50%

---

## 🔄 ROLLBACK STRATEGY

**Per-Sprint Rollback:**

**If Sprint 1 causes quality degradation > 10%:**
1. Revert Task 1.1 threshold changes
2. Keep Task 1.2 gap widening (low risk)
3. Re-tune thresholds incrementally (-5% instead of -10-20%)

**If Sprint 2 payoff matching fails:**
1. Disable payoff_bonus weight (set to 0)
2. Keep orphan rescue (independent)

**If Sprint 3 title checks too aggressive:**
1. Relax regex patterns in `_contains_technical_labels()`
2. Keep Russian fallback improvements

**Sprint 4 has minimal rollback risk** — purely cosmetic changes.

---

## 📈 EXPECTED IMPACT

### Before (Current State):
```
Candidate Survival: 5-15%
Avg Chain Duration: 15-25s
Final Shorts Output: LOW
Title Quality: 70/100
Story Completeness: 55/100
```

### After (All Sprints Complete):
```
Candidate Survival: 25-40% (+167% to +267%)
Avg Chain Duration: 35-55s (+133% to +220%)
Final Shorts Output: MEDIUM-HIGH
Title Quality: 88/100 (+26%)
Story Completeness: 70/100 (+27%)
```

**Total expected production value increase: +150-200%**

---

## 🚦 EXECUTION READINESS

**Status:** ✅ READY FOR IMPLEMENTATION

**Prerequisites:**
- [x] Forensic audit complete
- [x] Issues identified with line numbers
- [x] Severity ranked
- [x] Execution order planned
- [x] Rollback strategy defined
- [x] Validation criteria established

**Recommended Start:** SPRINT 1 (CRITICAL)

**User Approval Required Before:**
- [ ] Sprint 1 execution (quality gate changes)
- [ ] Sprint 2 execution (story logic changes)
- [ ] Sprint 3 execution (title generation changes)
- [ ] Sprint 4 execution (polish changes)

---

## 📝 NOTES

1. **Quality vs Quantity Balance:**  
   Sprint 1 shifts pipeline от "perfect clips only" к "good enough clips with story value". Manual review sample рекомендуется перед full deployment.

2. **Progressive Rollout:**  
   Consider deploying sprints sequentially, validating each before proceeding. Especially Sprint 1 (highest impact + highest risk).

3. **Monitoring:**  
   Add metrics tracking to each change для data-driven validation. См. validation code snippets в каждой task.

4. **Technical Debt:**  
   Issues #5 and #6 (Sprint 4) are cosmetic. Can be deferred если resources limited.

---

## 🎯 NEXT STEPS

**Immediate:**
1. User review этого remediation plan
2. Approve Sprint 1 execution
3. Toggle to Act Mode (если ещё не)
4. Begin implementation

**Implementation Order:**
1. Sprint 1, Task 1.1 (качество gates)
2. Sprint 1, Task 1.2 (chain gaps)
3. Validate Sprint 1
4. Proceed to Sprint 2 если validation successful

---

**Document Version:** 1.0  
**Last Updated:** 2026-06-23  
**Author:** Forensic Audit Team  
**Status:** AWAITING USER APPROVAL

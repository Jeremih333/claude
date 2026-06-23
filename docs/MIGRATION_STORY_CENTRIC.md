# Story-Centric Architecture Migration Plan

**Status:** 🚧 In Progress  
**Started:** 2026-06-14  
**Target Completion:** Sprint 3 (Week 4)

## Executive Summary

Migrate Shorts Factory from **temporal window-based** candidate generation to **semantic story-chain-based** architecture. This removes legacy `_build_story_candidates_from_window()` and unifies around `story_pipeline.py`.

---

## Current Architecture Issues

### 🔴 Critical Problems

1. **Dual candidate generation paths:**
   - Legacy: `_build_story_candidates_from_window()` (temporal windows: 14%, 32%, 78%)
   - New: `build_story_chains_for_episode()` (semantic story chains)
   - **Result:** Inconsistent candidate quality, technical titles leak through

2. **Technical metadata in candidate dict:**
   ```python
   source = "dialogue_cluster" | "fallback_window" | "dialogue_linear"
   ```
   - Shows up in titles: "fallback window", "dialogue cluster"
   - Should be: `source="story_chain"`, `story_unit_type="story_chain"`

3. **Rejection logic uses temporal criteria:**
   - `insufficient_context` — checks temporal duration, not story completion
   - Should check: `story_completion_score`, `is_complete`, `arc_shape`

4. **Dramatic pauses not preserved:**
   - `dramatic_kept_total = 0` (from legacy audit)
   - `_build_pause_timeline()` exists but not integrated with StoryFragment

### 📊 Evidence from Production

From `C:\Users\User\Desktop\toolkit\reports\legacy_pipeline_audit_2026-06-08.md`:

- `review_fast_mode_enabled=true` cap at 3 outputs (fixed)
- `insufficient_context` still active for selection rejection
- `starts_mid_phrase` and `dialogue_not_complete` still appear as warnings
- `face_preserving_fallback_usage_rate=1.0` — no dynamic conversation switching
- `subtitle_quality_score` around 0.45 (low)

---

## ⚠️ BLOCKING ISSUE DISCOVERED (2026-06-14, 21:50)

### Problem: Episode-Level Transcription Required

**Status:** 🔴 BLOCKER for story-centric activation

**Root Cause:**
- `_candidate_windows_story_centric()` requires `subtitle_info` with full episode transcription
- Current architecture: transcription happens PER CANDIDATE (after window selection)
- Story-centric needs: transcription BEFORE window selection (to build story chains)

**Current Flow (Legacy):**
```
1. _candidate_windows() → generates temporal windows (scene clusters)
2. For each window:
   3. Extract audio segment
   4. Transcribe segment → subtitle_info
   5. Build story candidates from segment
```

**Required Flow (Story-Centric):**
```
1. Extract full episode audio → WAV
2. Transcribe full episode → episode_subtitle_info
3. build_story_chains_for_episode(subtitle_info) → story chains
4. _candidate_windows_story_centric() → converts chains to windows
5. For each window: use pre-existing subtitle_info
```

**Why Fallback Activates:**
```python
# In _candidate_windows_story_centric():
subtitle_info = getattr(self, 'subtitle_info', None)
if not subtitle_info or not subtitle_info.get('segments'):
    # No subtitle data available - fallback to legacy
    return self._candidate_windows_legacy(video_path)  # ← This triggers
```

**Test Results:**
- ✅ Feature flag works: `use_story_centric_pipeline=True`
- ✅ Method routing works: calls `_candidate_windows_story_centric()`
- ❌ Subtitle data missing: `self.subtitle_info = None`
- → **Result:** Automatic fallback to legacy mode (33 scene_cluster candidates)

**Solution Options:**

1. **Quick Fix (MVP):** Add episode transcription step in `pick_candidates()`:
   ```python
   def pick_candidates(self, video_path, progress_callback=None):
       if self.cfg.get('use_story_centric_pipeline'):
           self.subtitle_info = self._transcribe_full_episode(video_path)
       windows = self._candidate_windows(video_path)
       # ... rest of logic
   ```

2. **Production Fix (Sprint 1.6):** Separate transcription as pre-processing:
   - Add `transcribe_episode()` public method
   - Cache episode transcription to disk
   - Load from cache in subsequent runs

**Impact:**
- Sprint 1.5 goal (activate story-centric) → BLOCKED
- Must implement episode transcription before story-centric can activate
- Legacy fallback working correctly as safety net

**Next Steps:**
1. Implement `_transcribe_full_episode()` helper method
2. Modify `pick_candidates()` to populate `self.subtitle_info`
3. Re-test story-centric activation

---

## Migration Strategy

### Phase 1: Core Migration (Sprint 1, Week 1-2)

#### 1.1 Isolate Story Pipeline as Single Source ✅

**File:** `pipeline/highlight.py`  
**Target:** `_candidate_windows()` method (lines ~2200-2600)

**Changes:**
```python
# BEFORE (temporal windows)
def _candidate_windows(self, video_path: str):
    candidates = []
    # ... motion/audio analysis
    candidates.append({
        "source": "dialogue_cluster",  # ❌ Technical label
        "story_window_segments": [{"start": 0.14*dur, ...}]  # ❌ Temporal %
    })

# AFTER (story chains)
def _candidate_windows(self, video_path: str):
    from .montage.story_pipeline import build_story_chains_for_episode
    
    chains = build_story_chains_for_episode(
        video_path=video_path,
        cfg=self.cfg,
        progress_callback=self._progress_callback
    )
    
    candidates = [
        story_chain_to_candidate(chain, source_id=Path(video_path).stem)
        for chain in chains
        if chain.is_complete and chain.completion_score >= 0.5
    ]
```

**Deletions:**
- `_build_story_candidates_from_window()` (lines ~2400-2500)
- `_fallback_window_candidate()` (lines ~2550-2650)
- `_build_story_candidates_from_turns_linear()` (lines ~2650-2750)

#### 1.2 Unified Candidate Dict Structure

**Standard fields (post-migration):**
```python
{
    "start": float,
    "end": float,
    "duration": float,
    "source": "story_chain",  # ✅ Unified
    "story_unit_type": str,   # hook_escalation | rescue_urgency | etc.
    "story_completion_score": float,  # replaces temporal checks
    "is_complete": bool,
    "arc_shape": str,  # classic | mini | payoff_first | etc.
    
    # Story metadata
    "story_summary": {
        "title_seed": str,
        "hook_type": str,
        "payoff_type": str,
        "conflict_type": str,
        "topic_terms": list[str],
        "emotions": list[str]
    },
    
    # Technical (internal use only)
    "score_breakdown": dict,
    "silence_timeline": list[dict]  # ✅ Integrated pause metadata
}
```

**Forbidden fields (to be removed):**
- `source="dialogue_cluster"` ❌
- `source="fallback_window"` ❌
- `source="dialogue_linear"` ❌
- `story_window_segments` with temporal percentages ❌

#### 1.3 Rejection Logic Update

**File:** `pipeline/selection.py` + `pipeline/highlight.py`

**Before:**
```python
if candidate_duration < min_duration:
    reject_reason = "insufficient_context"  # ❌ Temporal check
```

**After:**
```python
completion_score = candidate.get("story_completion_score", 0.0)
is_complete = candidate.get("is_complete", False)

if not is_complete or completion_score < 0.5:
    reject_reason = "incomplete_story_arc"  # ✅ Semantic check
    reject_details = {
        "completion_score": completion_score,
        "arc_shape": candidate.get("arc_shape", "unknown")
    }
```

**Update rejection reasons:**
- `insufficient_context` → `incomplete_story_arc`
- `low_story_quality` → check `completion_score` + `arc_shape`
- `starts_mid_phrase` → check `hook_type != "none"` from StorySummary

---

### Phase 2: Quality & Polish (Sprint 2, Week 3)

#### 2.1 Titling Pipeline Integration

**File:** `pipeline/titling.py`

**Current issues:**
- Titles contain "dialogue_cluster", "fallback_window"
- Hashtags too generic (#shorts, #сериал)

**Solution:**
```python
def generate_context_title(candidate: dict, cfg: dict) -> dict:
    story_summary = candidate.get("story_summary", {})
    
    # Priority 1: Use title_seed from StorySummary
    title_seed = story_summary.get("title_seed", "").strip()
    if title_seed and not _is_technical_label(title_seed):
        return _build_title_from_seed(title_seed, story_summary)
    
    # Priority 2: Generate from hook + conflict
    hook_type = story_summary.get("hook_type", "")
    conflict_type = story_summary.get("conflict_type", "")
    if hook_type and conflict_type:
        return _generate_contextual_title(hook_type, conflict_type, story_summary)
    
    # Fallback: Generic but avoid technical labels
    return _safe_fallback_title(candidate)

def _is_technical_label(text: str) -> bool:
    forbidden = {
        "dialogue_cluster", "fallback_window", "dialogue_linear",
        "balanced_hook", "story_window", "candidate", "turn", "segment"
    }
    return any(word in text.lower() for word in forbidden)
```

**Hashtag generation:**
```python
def generate_story_hashtags(story_summary: dict, cfg: dict) -> list[str]:
    tags = []
    
    # From topic_terms (specific keywords)
    for term in story_summary.get("topic_terms", [])[:3]:
        tags.append(f"#{term.lower().replace(' ', '')}")
    
    # From conflict_type (narrative genre)
    conflict_map = {
        "rescue_urgency": ["#спасение", "#срочно"],
        "reveal_discovery": ["#открытие", "#тайна"],
        "investigation_clue": ["#расследование", "#улика"]
    }
    conflict = story_summary.get("conflict_type", "")
    tags.extend(conflict_map.get(conflict, []))
    
    # Avoid generic fallbacks unless nothing else found
    if len(tags) < 2:
        tags.append("#shorts")
    
    return tags[:5]
```

#### 2.2 Silence Classification Integration

**File:** `pipeline/highlight.py` → `_build_pause_timeline()`

**Current issue:** Pause timeline built but not stored in candidate

**Solution:**
```python
def _extract_audio_summary_with_silence(self, video_path, start, end, cfg):
    # ... existing code ...
    
    pause_timeline = _build_pause_timeline(
        voiced=voiced_spans,
        pcm=pcm_data,
        sample_rate=sample_rate,
        cfg=cfg,
        detected_silences=silence_spans,
        total_duration=end - start
    )
    
    # NEW: Classify and extract meaningful pauses
    dramatic_pauses = [
        p for p in pause_timeline 
        if p["decision"] == "keep_for_story" 
        and p["silence_type"] in {"comedic_pause", "emotional_pause", "tension_pause"}
    ]
    
    return {
        "voiced_spans": voiced_spans,
        "silence_timeline": pause_timeline,
        "dramatic_pauses": dramatic_pauses,  # ✅ NEW
        "dramatic_kept_total": sum(p["duration"] for p in dramatic_pauses)
    }
```

**Integration with StoryFragment:**
```python
# In story_pipeline.py → build_story_chains_for_episode()
for fragment in story_fragments:
    audio_summary = self._extract_audio_summary_with_silence(
        video_path, fragment.start, fragment.end, cfg
    )
    
    fragment.silence_metadata = {
        "timeline": audio_summary["silence_timeline"],
        "dramatic_pauses": audio_summary["dramatic_pauses"],
        "kept_total": audio_summary["dramatic_kept_total"]
    }
```

---

### Phase 3: Testing & Validation (Sprint 2, Week 3)

#### 3.1 Toolkit Integration

**Use existing audit tools:**

1. **`story_audit.py`** — Story arc completeness
   ```powershell
   python C:\Users\User\Desktop\toolkit\story_audit.py "C:\path\to\output"
   ```
   **Acceptance criteria:**
   - `arc_complete_rate >= 0.75`
   - `avg_completion_score >= 0.65`
   - `technical_title_count == 0`

2. **`dialogue_gap_audit.py`** — Silence preservation
   ```powershell
   python C:\Users\User\Desktop\toolkit\dialogue_gap_audit.py "C:\path\to\output"
   ```
   **Acceptance criteria:**
   - `dramatic_kept_total > 0`
   - `comedic_pause_kept_count > 0`
   - `dead_air_cut_total > dead_air_kept_total`

3. **`story_interest_audit.py`** — Hook/retention quality
   ```powershell
   python C:\Users\User\Desktop\toolkit\story_interest_audit.py "C:\path\to\output"
   ```
   **Acceptance criteria:**
   - `avg_hook_score >= 0.6`
   - `sound_off_hook_avg >= 0.55`
   - `first_second_clarity_avg >= 0.6`

4. **`benchmark_corpus.py compare`** — Regression testing
   ```powershell
   python C:\Users\User\Desktop\toolkit\benchmark_corpus.py compare before.json after.json --output report.json
   ```
   **Monitor:**
   - `completion_score` change (should increase)
   - `title_quality` change (should increase)
   - `output_count` change (should stay same or increase)

#### 3.2 Manual Review Checklist

**First 20 outputs:**
- [ ] No "dialogue_cluster" or "fallback_window" in titles
- [ ] Hashtags are specific (not just #shorts)
- [ ] At least 1 dramatic pause preserved per candidate
- [ ] Hook clarity in first 3 seconds
- [ ] Ending payoff feels complete

---

### Phase 4: Cleanup (Sprint 3, Week 4)

#### 4.1 Code Deletion

**Files to modify:**
- `pipeline/highlight.py`:
  - Delete `_build_story_candidates_from_window()` (~100 lines)
  - Delete `_fallback_window_candidate()` (~50 lines)
  - Delete `_build_story_candidates_from_turns_linear()` (~80 lines)
  - Delete temporal percentage logic in `_build_story_window_plan()`

**Estimated LOC removed:** ~300 lines

#### 4.2 Documentation Update

- [ ] Update `README.md` with new story-centric flow
- [ ] Update `docs/PIPELINE_ARCHITECTURE.md`
- [ ] Add migration notes to `CHANGELOG.md`
- [ ] Update config schema docs (remove legacy fields)

---

## Risk Mitigation

### Risk 1: Breaking Existing Outputs

**Likelihood:** HIGH  
**Impact:** HIGH  

**Mitigation:**
- Use `benchmark_corpus.py compare` before/after
- Keep `git` branch for rollback
- Test on golden_set corpus first

### Risk 2: Title Quality Regression

**Likelihood:** MEDIUM  
**Impact:** MEDIUM  

**Mitigation:**
- `story_audit.py` monitors `technical_title_count`
- Manual review first 20 outputs
- Fallback to generic (but non-technical) titles

### Risk 3: Over-trimming Dramatic Pauses

**Likelihood:** MEDIUM  
**Impact:** HIGH (affects comedic timing)

**Mitigation:**
- `dialogue_gap_audit.py` monitors `dramatic_kept_total`
- Conservative thresholds initially
- A/B test with audience retention metrics

---

## Success Metrics

### Pre-Migration Baseline (2026-06-08)

From `legacy_pipeline_audit_2026-06-08.md`:
- `output_count` = 3 (capped by fast mode bug)
- `avg_final_duration` = 38s
- `subtitle_quality_score` = 0.45
- `face_preserving_fallback_usage_rate` = 1.0
- `technical_title_count` = unknown (likely > 0)

### Post-Migration Targets (2026-06-21)

- ✅ `output_count` >= 5 (fast mode cap removed)
- ✅ `arc_complete_rate` >= 0.75
- ✅ `avg_completion_score` >= 0.65
- ✅ `technical_title_count` == 0
- ✅ `dramatic_kept_total` > 0
- ✅ `avg_hook_score` >= 0.6

---

## Timeline

| Sprint | Week | Tasks | Deliverables |
|--------|------|-------|--------------|
| Sprint 1 | 1-2 | Core migration, candidate dict unification | Refactored `_candidate_windows()`, new rejection logic |
| Sprint 2 | 3 | Titling integration, silence preservation | Clean titles, preserved pauses |
| Sprint 3 | 4 | Testing, cleanup, documentation | Migration complete, legacy code removed |

---

## Decision Log

### 2026-06-14: Unified Story-Centric Architecture Approved

**Decision:** Migrate fully to `story_pipeline.py`, remove temporal windows  
**Rationale:** Technical labels leaking into titles, inconsistent quality  
**Alternatives considered:** Hybrid mode (rejected: too complex)  
**Stakeholders:** Pipeline maintainers, QA team  

---

## Questions for Review

1. **Fallback logic:** Keep fallback for incomplete story chains, or reject completely?
   - **Recommendation:** Reject incomplete chains, raise `min_candidate_duration` if needed

2. **Completion score threshold:** 0.5 or 0.65?
   - **Recommendation:** Start at 0.5, monitor with `story_audit.py`, raise if needed

3. **Hybrid mode:** Allow temporal fallback for edge cases?
   - **Recommendation:** No hybrid — full migration for consistency

---

## Contact

**Migration Lead:** Kiro (AI Dev Agent)  
**Review Required:** Human stakeholder approval before Phase 3 cleanup  
**Status Updates:** This document + `git` commit messages  

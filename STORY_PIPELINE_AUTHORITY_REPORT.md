# STORY PIPELINE AUTHORITY AUDIT — SECTION 1

**Date:** 2026-06-20  
**Status:** ❌ **FAILED — NOT AUTHORITATIVE**

---

## EXECUTIVE SUMMARY

Story pipeline is **NOT authoritative**. It is implemented as an **optional feature flag** with **default=False**, meaning the system runs in legacy mode by default.

**Critical Finding:** 4 legacy fallback entry points + 3-level cascade fallback chain inside story mode itself.

---

## FEATURE FLAG ANALYSIS

### Line 5394 — Default Behavior

```python
use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", False))
```

**Default:** `False` → Legacy mode is the **primary execution path**

**Impact:** Story pipeline is opt-in, not authoritative.

---

## 4 LEGACY FALLBACK ENTRY POINTS

| Line | Condition | Trigger | Impact |
|------|-----------|---------|--------|
| **5399** | `if not use_story_pipeline` | Feature flag disabled | **Returns to legacy immediately** |
| **5414** | `if not subtitle_info` | No subtitles available | Fallback to legacy scene detection |
| **5425** | `if not story_chains` | Story builder returned empty | Fallback to legacy scene detection |
| **5458** | `if not windows` | Story windows empty after building | Fallback to legacy scene detection |

### Code Evidence

**Entry Point 1 (Line 5399):**
```python
if not use_story_pipeline:
    return self._candidate_windows_legacy(video_path)
```

**Entry Point 2 (Line 5414):**
```python
if not subtitle_info:
    logger.warning("No subtitle data available - fallback to legacy")
    return self._candidate_windows_legacy(video_path)
```

**Entry Point 3 (Line 5425):**
```python
if not story_chains:
    logger.warning("Fallback to legacy if no story chains found")
    return self._candidate_windows_legacy(video_path)
```

**Entry Point 4 (Line 5458):**
```python
if not windows:
    return self._candidate_windows_legacy(video_path)
```

---

## 3-LEVEL CASCADE FALLBACK CHAIN

Even when story mode is active, each window goes through a fallback cascade:

### Lines 8407-8421 — Candidate Builder Cascade

```python
# LEVEL 1: Try turn-based linear builder
built = self._build_story_candidates_from_turns_linear(
    window_start, window_end, source, summary
)

# LEVEL 2: Fallback to window-based builder
if not built:
    built = self._build_story_candidates_from_window(
        window_start, window_end, source, summary
    )

# LEVEL 3: Emergency fallback candidate
if not built:
    fallback = self._fallback_window_candidate(
        window_start, window_end, source, summary
    )
    if fallback is not None:
        built = [fallback]
```

**Analysis:**
- This is a **legitimate builder hierarchy** (turn-first → window → emergency)
- NOT legacy dominance — this is expected story-mode behavior
- Each level tries progressively simpler candidate building

---

## LEGACY FUNCTION STATUS

| Function | Location | Status | Callers | Safe to Delete? |
|----------|----------|--------|---------|-----------------|
| `_candidate_windows_legacy()` | highlight.py:5462 | ✅ **ACTIVE** | 4 call sites | ❌ **NO** — Default mode |
| `_build_story_candidates_from_turns_linear()` | highlight.py | ✅ **ACTIVE** | Line 8407 | ❌ **NO** — Cascade L1 |
| `_build_story_candidates_from_window()` | highlight.py | ✅ **ACTIVE** | Line 8411 | ❌ **NO** — Cascade L2 |
| `_fallback_window_candidate()` | highlight.py | ✅ **ACTIVE** | Line 8415 | ❌ **NO** — Cascade L3 |

**Verdict:** ALL FOUR functions are **actively used** and **cannot be deleted**.

---

## AUTHORITY CHAIN ANALYSIS

### Current Flow

```
main.py
  → HighlightExtractor.candidate_windows()
    → Line 5394: Check feature flag
      → if False: _candidate_windows_legacy()  [DEFAULT PATH]
      → if True:
          → Check subtitle_info
            → if None: _candidate_windows_legacy()  [FALLBACK 1]
          → Build story chains
            → if empty: _candidate_windows_legacy()  [FALLBACK 2]
          → Build windows
            → if empty: _candidate_windows_legacy()  [FALLBACK 3]
          → FOR EACH WINDOW:
              → _build_story_candidates_from_turns_linear()
                → if fail: _build_story_candidates_from_window()
                  → if fail: _fallback_window_candidate()
```

**Conclusion:** Legacy mode is **primary**, story mode is **optional with multiple escape hatches**.

---

## IMPACT ASSESSMENT

### Production Behavior

**Current State:**
- Default config → legacy mode
- Story mode → still falls back to legacy on ANY failure
- Legacy functions are NOT dead code — they ARE the production code

**Risk:**
- Story pipeline improvements don't affect default behavior
- Users must explicitly enable story mode
- Any subtitle/chain failure → legacy fallback

---

## RECOMMENDATIONS

### Priority 1: Make Story Mode Default

**Change Line 5394:**
```python
# FROM:
use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", False))

# TO:
use_story_pipeline = bool(self.cfg.get("use_story_centric_pipeline", True))
```

**Impact:** Story mode becomes default, legacy becomes emergency fallback.

**Risk:** May break existing deployments if story mode has undetected bugs.

### Priority 2: Remove Feature Flag Entirely

**Delete Lines 5394-5399:**
```python
# Delete feature flag check, make story mode ONLY mode:
def candidate_windows(self, video_path: str):
    """Generate temporal candidate windows."""
    # Story mode is now the ONLY mode
    return self._candidate_windows_story_centric(video_path)
```

**Keep legacy as INTERNAL fallback** — rename functions:
- `_candidate_windows_legacy()` → `_emergency_fallback_scene_detection()`
- Document as "last resort when story pipeline fails"

### Priority 3: Document Cascade as Legitimate

The 3-level cascade (turns_linear → window → fallback) is **correct behavior**, not legacy dominance.

**Action:** Add comments:
```python
# Progressive fallback: try sophisticated builders first, simpler ones as fallback
# Level 1: Turn-based (requires good dialogue segmentation)
# Level 2: Window-based (requires basic scene structure)
# Level 3: Emergency (always produces something)
```

---

## CONCLUSION

**Story pipeline is NOT authoritative.**

It is an optional feature (default OFF) with multiple escape hatches to legacy mode.

ALL "legacy" functions are actively used in production.

**Next Action:** Execute Priority 1 recommendations to make story mode default.

---

**Report Status:** ✅ COMPLETE  
**Next Steps:** Proceed to Section 2 (Active Speaker Integration Report)

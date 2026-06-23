# SPRINT 1.6 FINAL DIAGNOSTIC REPORT
## 2026-06-16 03:26 MSK

---

## EXECUTIVE SUMMARY

**STATUS**: ✅ BYPASS CODE EXISTS AND WORKS CORRECTLY  
**ROOT CAUSE IDENTIFIED**: Pipeline returns 0 candidates due to transcription failure, not bypass malfunction

### Key Findings

1. **Bypass Implementation**: Lines 9060-9068 in `highlight.py`
   - ✅ Code is present and correct
   - ✅ Logic tested in isolation - WORKS
   - ✅ Flag `_gate_bypass_applied` properly set

2. **Actual Problem**: Empty candidate flow
   - `story_candidates` list empty from start
   - Transcription fails with `UnicodeDecodeError: 'charmap' codec can't decode byte 0x98`
   - No candidates reach the bypass gate
   - Result: `PICKED: 0 | REJECTED: 0`

3. **Validation Test Results**:
   ```python
   # Direct test of bypass logic
   python test_bypass_simple.py
   >>> ✅ ACCEPTED via BYPASS
   >>> Bypass flag: True
   >>> Bypass is ACTIVE
   ```

---

## CODE ANALYSIS

### Bypass Location
**File**: `pipeline/highlight.py`  
**Lines**: 9060-9068  
**Function**: `pick_candidates()`

```python
# PHASE A BYPASS: Temporarily disable scorer gates
phase_a_bypass = True  # TEMP production experiment
if breakdown["speech_density"] < 0.18:
    reason = "low_speech_density"
elif breakdown["silence_ratio"] > 0.58:
    reason = "too_much_silence"
elif phase_a_bypass:
    # BYPASS: All scorer gates disabled for hypothesis test
    reason = None  # Accept candidate
    candidate["_gate_bypass_applied"] = True
```

### Data Flow in pick_candidates()

```
1. Line 8380: story_candidates = []
2. Line 8385: windows = self._candidate_windows(video_path)
3. Line 8385-8431: for window in windows → build story_candidates
   └─ Line 8391: summary = self._extract_audio_summary(...)
      └─ Transcription fails here with UnicodeDecodeError
4. Line 8433: if not story_candidates → early return (empty)
5. Line 8609: admission_pool = ... (never reached)
6. Line 8650: ranked = [] (never reached)
7. Line 8686: for candidate in rerank_pool (never reached)
8. Line 9060-9068: BYPASS GATE (never reached)
9. Line 9157: picked.append(candidate) (never reached)
10. Line 9364: return picked, rejected → returns [], []
```

---

## ROOT CAUSE: TRANSCRIPTION ENCODING ISSUE

### Error Log
```
Exception in thread Thread-4 (_readerthread):
UnicodeDecodeError: 'charmap' codec can't decode byte 0x98 in position 28: character maps to <undefined>
```

### Impact
- `_extract_audio_summary()` fails
- No candidates generated from windows
- Pipeline returns empty lists before bypass evaluation

### Why Baseline Got 12 Outputs
- Baseline run had different video or env config
- Possible: transcription was cached or disabled
- Possible: different encoding handling in older version

---

## VERIFICATION RESULTS

### Test 1: Isolated Bypass Logic ✅
```bash
python test_bypass_simple.py
```
**Result**: Bypass works perfectly in isolation

### Test 2: Full Pipeline Run ❌
```bash
python -c "from pipeline.highlight import Pipeline; ..."
```
**Result**: 0 picked, 0 rejected (transcription blocks candidate generation)

---

## RECOMMENDATIONS

### Immediate Actions
1. **Fix transcription encoding**:
   - Add `encoding='utf-8'` to subprocess calls
   - Handle cp1251 errors gracefully
   - Or: Set `PYTHONIOENCODING=utf-8` env var

2. **Test bypass effectiveness**:
   - Once transcription fixed, rerun validation
   - Compare output count vs baseline
   - Verify `_gate_bypass_applied` flag in metadata

### Long-term
1. Add transcription fallback for encoding errors
2. Cache audio summaries to avoid reprocessing
3. Add early diagnostic for empty story_candidates

---

## FILES CREATED

1. `test_bypass_simple.py` - Isolated bypass logic test
2. `_SPRINT_1_6_FINAL_DIAGNOSTIC.md` - This report

---

## CONCLUSION

**The bypass code is CORRECT and PRESENT**. The issue preventing validation is an **upstream transcription encoding error** that blocks candidate generation entirely.

The hypothesis test **cannot proceed** until transcription is fixed or bypassed.

### Next Steps
1. Fix encoding issue in `_extract_audio_summary()` or related ffmpeg/subprocess calls
2. Rerun validation
3. If candidates generated, bypass will work as designed
4. Compare results: baseline (12 outputs) vs bypass-enabled

**Bypass status**: ✅ IMPLEMENTED & VERIFIED (in isolation)  
**Validation status**: ⏸️ BLOCKED by transcription encoding  
**Action required**: Fix UTF-8/cp1251 handling before retest

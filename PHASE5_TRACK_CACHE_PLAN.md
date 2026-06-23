# PHASE 5 — TRACK CACHE ARCHITECTURE
## Window-Aware Reusable Track Cache Design

**Date:** 2026-06-22  
**Status:** ARCHITECTURE COMPLETE  
**Expected Savings:** 35-45% of face_detection_sec (HIGHEST IMPACT)

---

## 🎯 CORE PROBLEM

**Current:** Each candidate calls `estimate_face_tracks()` independently, discarding tracks after use.

**Overlapping candidates** cause massive redundant detection:
```
Candidate A: [30s - 60s]  → 90 frames scanned
Candidate B: [45s - 75s]  → 90 frames scanned
Overlap: 45-60s = 45 DUPLICATE frames
```

**Solution:** Interval-aware cache with partial overlap reuse.

---

## 🏗️ CACHE ARCHITECTURE

### Cache Key Structure:
```python
CacheKey = namedtuple('CacheKey', [
    'video_path',      # Absolute path to video file
    'start_sec',       # Interval start (rounded to 0.1s precision)
    'end_sec',         # Interval end (rounded to 0.1s precision)
    'sample_fps',      # Sampling rate (affects frame selection)
    'detector_profile' # "light" or "strong" (affects detection params)
])
```

**Why these fields:**
- `video_path`: Different videos → different tracks
- `start_sec, end_sec`: Interval boundaries for overlap detection
- `sample_fps`: Different FPS → different frame timestamps
- `detector_profile`: Different profiles → different detection results

---

### Cache Value Structure:
```python
CacheEntry = {
    'tracks': [
        {
            'start': 30.2,
            'end': 31.8,
            'bbox': (x, y, w, h),
            'confidence': 0.87,
            'speaker_score': 0.92,
            'track_id': 'track_001',
            # ... other track fields
        },
        # ... more tracks
    ],
    'frame_timestamps': [30.0, 30.33, 30.67, ...],  # Actual sampled frames
    'metadata': {
        'creation_time': timestamp,
        'access_count': 5,
        'last_access': timestamp,
        'computation_time_sec': 2.4
    }
}
```

---

## 🔄 INTERVAL OVERLAP STRATEGY

### Scenario 1: Exact Match (CACHE HIT)
```python
Request: tracks(30, 60, fps=3, profile="light")
Cache:   tracks(30, 60, fps=3, profile="light")

Action: Return cached tracks directly
Computation: 0% (full reuse)
```

---

### Scenario 2: Subset Match (PARTIAL HIT)
```python
Request: tracks(40, 50, fps=3, profile="light")
Cache:   tracks(30, 60, fps=3, profile="light")

Action: Filter cached tracks to [40, 50] interval
Computation: 0% (subset extraction)
```

---

### Scenario 3: Superset Request (PARTIAL HIT + COMPUTE)
```python
Request: tracks(20, 70, fps=3, profile="light")
Cache:   tracks(30, 60, fps=3, profile="light")

Action:
  1. Compute tracks(20, 30) → NEW
  2. Reuse tracks(30, 60)   → CACHED
  3. Compute tracks(60, 70) → NEW
  4. Merge all three ranges

Computation: 40% (20s new / 50s total)
Savings: 60%
```

---

### Scenario 4: Partial Overlap (SPLIT COMPUTE)
```python
Request: tracks(45, 75, fps=3, profile="light")
Cache:   tracks(30, 60, fps=3, profile="light")

Action:
  1. Reuse tracks(45, 60)   → CACHED (15s)
  2. Compute tracks(60, 75) → NEW (15s)
  3. Merge ranges

Computation: 50% (15s new / 30s total)
Savings: 50%
```

---

### Scenario 5: Different FPS (MISS)
```python
Request: tracks(30, 60, fps=5, profile="light")
Cache:   tracks(30, 60, fps=3, profile="light")

Action: CACHE MISS (different frame timestamps)
Computation: 100%
```

**Why miss:** `fps=5` samples different frames than `fps=3`, cannot reuse.

**Mitigation:** Consider fps-agnostic track storage (store ALL detected tracks, filter by fps on retrieval).

---

## 🧩 INTERVAL MERGE ALGORITHM

### Core Logic:
```python
class IntervalTrackCache:
    def __init__(self, max_size_gb=2.0):
        self.cache = {}  # {CacheKey: CacheEntry}
        self.max_size = max_size_gb * 1024 * 1024 * 1024
        self.current_size = 0
    
    def get_or_compute(self, video_path, start, end, fps, profile, compute_fn):
        key = self._make_key(video_path, start, end, fps, profile)
        
        # 1. Exact match
        if key in self.cache:
            return self._hit(key)
        
        # 2. Find overlapping cached intervals
        overlaps = self._find_overlaps(video_path, start, end, fps, profile)
        
        if not overlaps:
            # MISS: Compute full range
            tracks = compute_fn(start, end)
            self._store(key, tracks)
            return tracks
        
        # 3. Compute coverage gaps
        covered_ranges = [ov['range'] for ov in overlaps]
        gaps = self._compute_gaps(start, end, covered_ranges)
        
        # 4. Compute only gaps
        gap_tracks = []
        for gap_start, gap_end in gaps:
            gap_tracks.extend(compute_fn(gap_start, gap_end))
        
        # 5. Merge cached + new tracks
        cached_tracks = []
        for ov in overlaps:
            cached_tracks.extend(self._filter_tracks(ov['tracks'], start, end))
        
        all_tracks = self._merge_tracks(cached_tracks + gap_tracks)
        
        # 6. Store result
        self._store(key, all_tracks)
        
        return all_tracks
    
    def _find_overlaps(self, video_path, start, end, fps, profile):
        """Find all cached intervals overlapping [start, end]."""
        overlaps = []
        for cache_key, entry in self.cache.items():
            if (cache_key.video_path == video_path
                and cache_key.sample_fps == fps
                and cache_key.detector_profile == profile
                and self._intervals_overlap(
                    start, end,
                    cache_key.start_sec, cache_key.end_sec
                )):
                overlaps.append({
                    'range': (cache_key.start_sec, cache_key.end_sec),
                    'tracks': entry['tracks']
                })
        return overlaps
    
    def _intervals_overlap(self, a_start, a_end, b_start, b_end):
        """Check if two intervals overlap."""
        return a_start < b_end and b_start < a_end
    
    def _compute_gaps(self, start, end, covered_ranges):
        """Compute uncovered gaps in [start, end]."""
        # Sort covered ranges
        covered = sorted(covered_ranges)
        
        gaps = []
        cursor = start
        
        for cov_start, cov_end in covered:
            if cursor < cov_start:
                gaps.append((cursor, cov_start))
            cursor = max(cursor, cov_end)
        
        if cursor < end:
            gaps.append((cursor, end))
        
        return gaps
    
    def _filter_tracks(self, tracks, start, end):
        """Filter tracks to [start, end] interval."""
        return [
            t for t in tracks
            if t['end'] >= start and t['start'] <= end
        ]
    
    def _merge_tracks(self, tracks):
        """Merge and deduplicate overlapping tracks."""
        if not tracks:
            return []
        
        # Sort by start time
        sorted_tracks = sorted(tracks, key=lambda t: t['start'])
        
        merged = [sorted_tracks[0]]
        
        for track in sorted_tracks[1:]:
            last = merged[-1]
            
            # Check for overlap
            if (track['start'] <= last['end'] + 0.5
                and track.get('track_id') == last.get('track_id')):
                # Merge overlapping tracks with same ID
                merged[-1] = {
                    **last,
                    'end': max(last['end'], track['end']),
                    'confidence': max(last['confidence'], track['confidence'])
                }
            else:
                merged.append(track)
        
        return merged
    
    def _make_key(self, video_path, start, end, fps, profile):
        return CacheKey(
            video_path=str(video_path),
            start_sec=round(start, 1),
            end_sec=round(end, 1),
            sample_fps=float(fps),
            detector_profile=str(profile)
        )
    
    def _hit(self, key):
        """Handle cache hit."""
        entry = self.cache[key]
        entry['metadata']['access_count'] += 1
        entry['metadata']['last_access'] = time.time()
        return entry['tracks']
    
    def _store(self, key, tracks):
        """Store tracks in cache with LRU eviction."""
        entry_size = self._estimate_size(tracks)
        
        # Evict if needed
        while self.current_size + entry_size > self.max_size:
            self._evict_lru()
        
        self.cache[key] = {
            'tracks': tracks,
            'metadata': {
                'creation_time': time.time(),
                'access_count': 1,
                'last_access': time.time(),
                'size_bytes': entry_size
            }
        }
        self.current_size += entry_size
    
    def _evict_lru(self):
        """Evict least recently used entry."""
        if not self.cache:
            return
        
        lru_key = min(
            self.cache.keys(),
            key=lambda k: self.cache[k]['metadata']['last_access']
        )
        
        entry_size = self.cache[lru_key]['metadata']['size_bytes']
        del self.cache[lru_key]
        self.current_size -= entry_size
    
    def _estimate_size(self, tracks):
        """Estimate memory size of tracks."""
        # Rough estimate: 200 bytes per track
        return len(tracks) * 200
```

---

## 🚀 INTEGRATION POINTS

### Point 1: `active_speaker.py` Wrapper
```python
# Global cache instance
_TRACK_CACHE = IntervalTrackCache(max_size_gb=2.0)

def estimate_face_tracks_cached(clip, start_t, end_t, **kwargs):
    """Cached wrapper around estimate_face_tracks."""
    video_path = getattr(clip, 'filename', None)
    
    if not video_path:
        # Can't cache without video identifier
        return estimate_face_tracks(clip, start_t, end_t, **kwargs)
    
    fps = kwargs.get('sample_fps', 3.0)
    profile = kwargs.get('detector_profile', 'light')
    
    def compute_fn(start, end):
        return estimate_face_tracks(clip, start, end, **kwargs)
    
    return _TRACK_CACHE.get_or_compute(
        video_path, start_t, end_t, fps, profile, compute_fn
    )
```

### Point 2: `face_crop.py` Usage
```python
# Line 1878: Replace direct call
# OLD:
local_tracks = estimate_face_tracks(clip, start, end, ...)

# NEW:
local_tracks = estimate_face_tracks_cached(clip, start, end, ...)
```

---

## 📊 EXPECTED IMPACT

### Scenario: 40min Episode, 15 Candidates

**Current (No Cache):**
```
Candidate 1:  [120s - 150s]  90 frames
Candidate 2:  [135s - 165s]  90 frames  (15s overlap with C1)
Candidate 3:  [150s - 180s]  90 frames  (15s overlap with C2)
...
Total: 15 × 90 = 1,350 frames scanned
```

**With Cache (Interval Reuse):**
```
Candidate 1:  [120s - 150s]  90 frames  (COMPUTE)
Candidate 2:  [135s - 165s]  45 frames  (15s cached, 15s new)
Candidate 3:  [150s - 180s]  45 frames  (15s cached, 15s new)
...
Total: ~900 frames scanned (vs 1,350)

Reduction: (1,350 - 900) / 1,350 = 33%
```

**Best Case (High Overlap):**
```
40% average overlap → 40% reduction
```

**Worst Case (No Overlap):**
```
No overlap → 0% reduction (but no harm)
```

---

## 🛡️ QUALITY PROTECTION

### Safety 1: Track ID Stability
**Issue:** Merging tracks from different cache entries could break track IDs

**Solution:** Track IDs include timestamp prefix
```python
track_id = f"track_{start_timestamp}_{sequence_num}"
```

### Safety 2: Confidence Preservation
**Issue:** Merging could dilute confidence scores

**Solution:** Use MAX confidence when merging overlaps

### Safety 3: FPS Mismatch
**Issue:** Different FPS → different frames → incompatible tracks

**Solution:** Cache key includes `sample_fps`, prevents cross-FPS reuse

---

## 🔬 CACHE METRICS

Track these for validation:

```python
{
    "cache_enabled": True,
    "cache_size_mb": 145.2,
    "cache_entries": 12,
    
    "cache_hits": 8,
    "cache_partial_hits": 4,
    "cache_misses": 3,
    "hit_rate": 0.53,  # (8 / 15)
    
    "overlap_reuse_hits": 6,
    "overlap_reuse_percent": 42.0,
    
    "frames_computed": 810,
    "frames_cached": 540,
    "frames_total_requested": 1350,
    "redundancy_eliminated": 0.40,  # 40% reduction
    
    "avg_gap_computation_sec": 0.8,  # Time to compute gaps
    "avg_merge_overhead_sec": 0.05   # Time to merge tracks
}
```

---

## 🎯 IMPLEMENTATION PHASES

### Phase 1: Basic Cache (Exact Match Only)
- Implement exact key matching
- No interval overlap logic
- Target: 10-15% reduction

### Phase 2: Overlap Detection
- Add `_find_overlaps()` logic
- Implement gap computation
- Target: 25-30% reduction

### Phase 3: Intelligent Merging
- Full interval merge algorithm
- Track deduplication
- Target: 35-40% reduction

---

**End of Track Cache Plan**

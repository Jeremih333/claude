from __future__ import annotations


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def rank_story_candidates(candidates: list[dict]) -> list[dict]:
    """Rank story candidates with PHASE 4 payoff protection.
    
    Priority order:
    1. story_completion_score (0.25 per arc element)
    2. payoff presence (is_complete or payoff_filled)
    3. story_coherence_score
    4. clarity_score
    5. hook_score
    6. general score
    """
    return sorted(
        list(candidates or []),
        key=lambda item: (
            _as_float(item.get("story_completion_score", 0.0)),
            # PHASE 4: Payoff protection — boost complete stories
            1 if (item.get("is_complete") or 
                  (item.get("score_breakdown") or {}).get("payoff_filled")) else 0,
            _as_float(item.get("story_coherence_score", 0.0)),
            _as_float(item.get("clarity_score", item.get("story_clarity_score", 0.0))),
            _as_float(item.get("hook_score", 0.0)),
            _as_float(item.get("score", 0.0)),
        ),
        reverse=True,
    )


def select_publishable_candidates(candidates: list[dict], max_outputs: int = 5, min_duration: float = 20.0) -> list[dict]:
    """Select top publishable candidates.
    
    PHASE 4: Changed from hard 35s floor to 20s with quality gates.
    - Hard reject: < 10s (micro-fragments)
    - Range 10-20s: require completion_score >= 0.75 OR is_complete=True
    - >= 20s: accept if above min_duration
    """
    selected = []
    for candidate in rank_story_candidates(candidates):
        duration = _as_float(candidate.get("duration", 0.0))
        
        # Hard reject micro-fragments
        if duration < 10.0:
            continue
        
        # Quality gate for short candidates (10-20s)
        if duration < 20.0:
            completion = _as_float(candidate.get("story_completion_score", 0.0))
            is_complete = bool(candidate.get("is_complete", False))
            if completion < 0.75 and not is_complete:
                continue
        
        # Standard duration check
        if duration < min_duration:
            continue
            
        selected.append(candidate)
        if len(selected) >= max_outputs:
            break
    return selected


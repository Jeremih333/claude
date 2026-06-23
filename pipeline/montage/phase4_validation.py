"""
phase4_validation.py
--------------------
PHASE 4 validation metrics and diagnostic reporting.

Validates that PHASE 4 changes achieve:
1. Reduced story chain fragmentation
2. Higher payoff completion rates
3. Quality-aware duration flexibility
4. Chain continuation priority

Usage:
    python -m pipeline.montage.phase4_validation <subtitle_json_path>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .story_pipeline import build_story_chains_for_episode


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def validate_phase4_improvements(
    subtitle_info: dict,
    *,
    cfg: dict | None = None,
) -> dict:
    """Run PHASE 4 validation and return diagnostic metrics.
    
    Returns
    -------
    dict
        Validation report with:
        - chain_count: total chains produced
        - complete_chain_count: chains with all 4 arc elements
        - completion_rate: % of complete chains
        - avg_completion_score: mean completion score
        - short_quality_chains: chains 6-35s with completion >= 0.75
        - extended_chain_count: chains rescued via payoff extension
        - avg_chain_duration: mean duration in seconds
        - duration_distribution: bucketed chain counts
        - payoff_protection_score: % chains with payoff_filled
    """
    cfg = cfg or {}
    
    chains = build_story_chains_for_episode(
        subtitle_info,
        cfg=cfg,
        source_id="phase4_validation",
    )
    
    if not chains:
        return {
            "status": "NO_CHAINS_PRODUCED",
            "chain_count": 0,
            "complete_chain_count": 0,
            "completion_rate": 0.0,
            "avg_completion_score": 0.0,
            "short_quality_chains": 0,
            "extended_chain_count": 0,
            "avg_chain_duration": 0.0,
            "duration_distribution": {},
            "payoff_protection_score": 0.0,
        }
    
    # Metrics
    chain_count = len(chains)
    complete_chains = [c for c in chains if c.is_complete]
    complete_chain_count = len(complete_chains)
    completion_rate = round(complete_chain_count / chain_count, 4) if chain_count else 0.0
    
    completion_scores = [float(c.completion_score) for c in chains]
    avg_completion_score = round(sum(completion_scores) / len(completion_scores), 4)
    
    # Short quality chains (6-35s with high completion)
    durations = [_as_float(c.end) - _as_float(c.start) for c in chains]
    short_quality_chains = sum(
        1 for c, dur in zip(chains, durations)
        if 6.0 <= dur < 35.0 and (c.completion_score >= 0.75 or c.is_complete)
    )
    
    # Extended chains (payoff rescue)
    extended_chain_count = sum(1 for c in chains if c.search_extended)
    
    # Duration stats
    avg_chain_duration = round(sum(durations) / len(durations), 2) if durations else 0.0
    
    duration_distribution = {
        "micro_<6s": sum(1 for d in durations if d < 6.0),
        "short_6-20s": sum(1 for d in durations if 6.0 <= d < 20.0),
        "target_20-35s": sum(1 for d in durations if 20.0 <= d < 35.0),
        "ideal_35-60s": sum(1 for d in durations if 35.0 <= d < 60.0),
        "long_60s+": sum(1 for d in durations if d >= 60.0),
    }
    
    # Payoff protection
    chains_with_payoff = sum(1 for c in chains if c.payoff)
    payoff_protection_score = round(chains_with_payoff / chain_count, 4) if chain_count else 0.0
    
    return {
        "status": "OK",
        "chain_count": chain_count,
        "complete_chain_count": complete_chain_count,
        "completion_rate": completion_rate,
        "avg_completion_score": avg_completion_score,
        "short_quality_chains": short_quality_chains,
        "extended_chain_count": extended_chain_count,
        "avg_chain_duration": avg_chain_duration,
        "duration_distribution": duration_distribution,
        "payoff_protection_score": payoff_protection_score,
        "phase4_metrics": {
            "fragmentation_reduction": "Measured by avg_chain_duration increase",
            "payoff_completion": f"{payoff_protection_score:.1%}",
            "quality_short_stories": short_quality_chains,
            "continuation_success": f"{extended_chain_count} chains extended",
        },
    }


def print_validation_report(report: dict) -> None:
    """Pretty-print validation report."""
    print("\n" + "=" * 70)
    print("PHASE 4 VALIDATION REPORT")
    print("=" * 70)
    
    if report.get("status") != "OK":
        print(f"❌ Status: {report.get('status')}")
        return
    
    print(f"✅ Status: {report['status']}")
    print(f"\n📊 CHAIN METRICS:")
    print(f"   Total chains: {report['chain_count']}")
    print(f"   Complete chains: {report['complete_chain_count']} ({report['completion_rate']:.1%})")
    print(f"   Avg completion score: {report['avg_completion_score']:.2f}")
    print(f"   Avg chain duration: {report['avg_chain_duration']}s")
    
    print(f"\n🎯 PHASE 4 IMPROVEMENTS:")
    phase4 = report.get("phase4_metrics", {})
    print(f"   Payoff protection: {phase4.get('payoff_completion', 'N/A')}")
    print(f"   Quality short stories: {phase4.get('quality_short_stories', 0)}")
    print(f"   Chain continuation: {phase4.get('continuation_success', 'N/A')}")
    
    print(f"\n📏 DURATION DISTRIBUTION:")
    dist = report.get("duration_distribution", {})
    for bucket, count in dist.items():
        bar = "█" * min(count, 50)
        print(f"   {bucket:.<20} {count:>3} {bar}")
    
    print("\n" + "=" * 70 + "\n")


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.montage.phase4_validation <subtitle_json_path>")
        sys.exit(1)
    
    subtitle_path = Path(sys.argv[1])
    
    if not subtitle_path.exists():
        print(f"❌ File not found: {subtitle_path}")
        sys.exit(1)
    
    try:
        with open(subtitle_path, encoding="utf-8") as f:
            subtitle_info = json.load(f)
    except Exception as e:
        print(f"❌ Failed to load subtitle file: {e}")
        sys.exit(1)
    
    # Run validation
    report = validate_phase4_improvements(subtitle_info)
    
    # Print report
    print_validation_report(report)
    
    # Export JSON report
    report_path = subtitle_path.parent / f"{subtitle_path.stem}_phase4_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"📄 Detailed report saved: {report_path}")


if __name__ == "__main__":
    main()

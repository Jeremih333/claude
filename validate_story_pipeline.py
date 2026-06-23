"""
Validation script for Sprint 1.6: Story-centric pipeline

Runs the same episode through both legacy and story-centric modes,
then compares metrics to prove the new pipeline is working.
"""

import json
import os
import shutil
from pathlib import Path
from pipeline.config import load_config
from pipeline.highlight import Pipeline


def run_validation(episode_path, output_base):
    """Run both legacy and story-centric modes, collect metrics."""
    
    results = {
        'episode': episode_path,
        'legacy': {},
        'story_centric': {},
    }
    
    # 1. RUN LEGACY MODE
    print("\n" + "="*80)
    print("RUNNING LEGACY MODE (use_story_centric_pipeline: false)")
    print("="*80 + "\n")
    
    legacy_output = Path(output_base) / "legacy_run"
    legacy_output.mkdir(parents=True, exist_ok=True)
    
    cfg = load_config("settings.yaml")
    cfg['use_story_centric_pipeline'] = False
    cfg['output_root'] = str(legacy_output)
    
    pipe_legacy = Pipeline(cfg)
    legacy_report = pipe_legacy.process_episode(episode_path, progress_callback=print)
    
    # Save legacy report
    legacy_report_path = legacy_output / "validation_report.json"
    with open(legacy_report_path, 'w', encoding='utf-8') as f:
        json.dump(legacy_report, f, indent=2, ensure_ascii=False)
    
    results['legacy'] = extract_metrics(legacy_report)
    
    # 2. RUN STORY-CENTRIC MODE
    print("\n" + "="*80)
    print("RUNNING STORY-CENTRIC MODE (use_story_centric_pipeline: true)")
    print("="*80 + "\n")
    
    story_output = Path(output_base) / "story_run"
    story_output.mkdir(parents=True, exist_ok=True)
    
    cfg_story = load_config("settings.yaml")
    cfg_story['use_story_centric_pipeline'] = True
    cfg_story['output_root'] = str(story_output)
    
    pipe_story = Pipeline(cfg_story)
    story_report = pipe_story.process_episode(episode_path, progress_callback=print)
    
    # Save story report
    story_report_path = story_output / "validation_report.json"
    with open(story_report_path, 'w', encoding='utf-8') as f:
        json.dump(story_report, f, indent=2, ensure_ascii=False)
    
    results['story_centric'] = extract_metrics(story_report)
    
    # 3. COMPARE
    print("\n" + "="*80)
    print("COMPARISON RESULTS")
    print("="*80 + "\n")
    
    comparison = compare_metrics(results['legacy'], results['story_centric'])
    results['comparison'] = comparison
    
    # Save full results
    results_path = Path(output_base) / "validation_results.json"
    with open(results_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print_comparison(comparison)
    
    return results


def extract_metrics(report):
    """Extract key metrics from episode report."""
    stats = report.get('stats', {})
    
    metrics = {
        'status': report.get('status'),
        'total_windows': stats.get('total_windows', 0),
        'total_story_candidates': stats.get('total_story_candidates', 0),
        'publishable_candidates': stats.get('publishable_candidates', 0),
        'generated_outputs': len(report.get('generated_outputs', [])),
        'main_rejection_reason': stats.get('main_rejection_reason', 'unknown'),
        'rejection_reasons': stats.get('rejection_reasons', {}),
        'ranking_timeouts': stats.get('ranking_timeouts', 0),
        'semantic_preview_timeouts': stats.get('semantic_preview_timeouts', 0),
        'final_visual_rejects': stats.get('final_visual_rejects', 0),
        'titles_generated': stats.get('titles_generated', 0),
        'outputs': [],
    }
    
    # Extract output details
    for output in report.get('generated_outputs', []):
        metrics['outputs'].append({
            'title': output.get('title', ''),
            'duration': output.get('duration_seconds', 0),
            'story_summary': output.get('story_summary', ''),
            'transcript_excerpt': output.get('transcript_excerpt', ''),
            'completion_score': output.get('completion_score', 0),
            'payoff_score': output.get('payoff_score', 0),
            'context_score': output.get('context_score', 0),
            'subtitle_quality_score': output.get('subtitle_quality_score', 0),
        })
    
    # Calculate averages
    if metrics['outputs']:
        metrics['avg_completion_score'] = sum(o['completion_score'] for o in metrics['outputs']) / len(metrics['outputs'])
        metrics['avg_payoff_score'] = sum(o['payoff_score'] for o in metrics['outputs']) / len(metrics['outputs'])
        metrics['avg_context_score'] = sum(o['context_score'] for o in metrics['outputs']) / len(metrics['outputs'])
        metrics['avg_subtitle_quality_score'] = sum(o['subtitle_quality_score'] for o in metrics['outputs']) / len(metrics['outputs'])
    else:
        metrics['avg_completion_score'] = 0
        metrics['avg_payoff_score'] = 0
        metrics['avg_context_score'] = 0
        metrics['avg_subtitle_quality_score'] = 0
    
    return metrics


def compare_metrics(legacy, story):
    """Compare legacy vs story-centric metrics."""
    comparison = {
        'windows': {
            'legacy': legacy['total_windows'],
            'story': story['total_windows'],
            'delta': story['total_windows'] - legacy['total_windows'],
        },
        'candidates': {
            'legacy': legacy['total_story_candidates'],
            'story': story['total_story_candidates'],
            'delta': story['total_story_candidates'] - legacy['total_story_candidates'],
        },
        'publishable': {
            'legacy': legacy['publishable_candidates'],
            'story': story['publishable_candidates'],
            'delta': story['publishable_candidates'] - legacy['publishable_candidates'],
        },
        'outputs': {
            'legacy': legacy['generated_outputs'],
            'story': story['generated_outputs'],
            'delta': story['generated_outputs'] - legacy['generated_outputs'],
        },
        'quality': {
            'completion': {
                'legacy': legacy['avg_completion_score'],
                'story': story['avg_completion_score'],
                'delta': story['avg_completion_score'] - legacy['avg_completion_score'],
            },
            'payoff': {
                'legacy': legacy['avg_payoff_score'],
                'story': story['avg_payoff_score'],
                'delta': story['avg_payoff_score'] - legacy['avg_payoff_score'],
            },
            'context': {
                'legacy': legacy['avg_context_score'],
                'story': story['avg_context_score'],
                'delta': story['avg_context_score'] - legacy['avg_context_score'],
            },
            'subtitle': {
                'legacy': legacy['avg_subtitle_quality_score'],
                'story': story['avg_subtitle_quality_score'],
                'delta': story['avg_subtitle_quality_score'] - legacy['avg_subtitle_quality_score'],
            },
        },
        'rejections': {
            'legacy': legacy['rejection_reasons'],
            'story': story['rejection_reasons'],
        },
    }
    
    return comparison


def print_comparison(comparison):
    """Pretty-print comparison results."""
    
    print(f"Total Windows:")
    print(f"  Legacy: {comparison['windows']['legacy']}")
    print(f"  Story:  {comparison['windows']['story']}")
    print(f"  Delta:  {comparison['windows']['delta']:+d}")
    print()
    
    print(f"Story Candidates:")
    print(f"  Legacy: {comparison['candidates']['legacy']}")
    print(f"  Story:  {comparison['candidates']['story']}")
    print(f"  Delta:  {comparison['candidates']['delta']:+d}")
    print()
    
    print(f"Publishable Candidates:")
    print(f"  Legacy: {comparison['publishable']['legacy']}")
    print(f"  Story:  {comparison['publishable']['story']}")
    print(f"  Delta:  {comparison['publishable']['delta']:+d}")
    print()
    
    print(f"Generated Outputs:")
    print(f"  Legacy: {comparison['outputs']['legacy']}")
    print(f"  Story:  {comparison['outputs']['story']}")
    print(f"  Delta:  {comparison['outputs']['delta']:+d}")
    print()
    
    print("Quality Scores:")
    for metric, data in comparison['quality'].items():
        print(f"  {metric.capitalize()}:")
        print(f"    Legacy: {data['legacy']:.3f}")
        print(f"    Story:  {data['story']:.3f}")
        print(f"    Delta:  {data['delta']:+.3f}")
    print()
    
    print("Top Rejection Reasons (Legacy):")
    for reason, count in sorted(comparison['rejections']['legacy'].items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {reason}: {count}")
    print()
    
    print("Top Rejection Reasons (Story):")
    for reason, count in sorted(comparison['rejections']['story'].items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {reason}: {count}")
    print()


if __name__ == '__main__':
    episode = "episode01_test.avi"
    output_dir = "_validation_sprint_1_6"
    
    if not os.path.exists(episode):
        print(f"ERROR: Episode file not found: {episode}")
        exit(1)
    
    print("="*80)
    print("STORY-CENTRIC PIPELINE VALIDATION (Sprint 1.6)")
    print("="*80)
    print(f"Episode: {episode}")
    print(f"Output:  {output_dir}")
    print()
    
    results = run_validation(episode, output_dir)
    
    print("\n" + "="*80)
    print(f"VALIDATION COMPLETE")
    print("="*80)
    print(f"Full results saved to: {output_dir}/validation_results.json")
    print()

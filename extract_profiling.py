#!/usr/bin/env python3
"""Extract profiling data from validation results."""

import json
import os
from pathlib import Path

def extract_profiling():
    runs = ['legacy_run', 'story_run']
    results = []
    
    for run in runs:
        shorts_dir = Path('_validation_sprint_1_6') / run / 'episode01_test_shorts'
        if not shorts_dir.exists():
            continue
        
        for fname in os.listdir(shorts_dir):
            if fname.startswith('short_') and fname.endswith('.json'):
                with open(shorts_dir / fname, encoding='utf-8') as f:
                    data = json.load(f)
                results.append((run, fname, data))
    
    print('\n' + '=' * 80)
    print('PROFILING DATA EXTRACTION')
    print('=' * 80 + '\n')
    
    for run, fname, data in results:
        print(f'{run}/{fname}:')
        timings = data.get('score_breakdown', {}).get('debug_timings', {})
        if timings:
            print(f'  Timings:')
            for key, val in timings.items():
                print(f'    {key}: {val}')
        else:
            print(f'  Timings: N/A')
        print()
    
    print('\n' + '=' * 80)
    print('AGGREGATED TIMING SUMMARY')
    print('=' * 80 + '\n')
    
    # Aggregate timings by stage
    stage_times = {}
    for run, fname, data in results:
        timings = data.get('score_breakdown', {}).get('debug_timings', {})
        for key, val in timings.items():
            if key not in stage_times:
                stage_times[key] = []
            stage_times[key].append((run, fname, val))
    
    for stage, entries in sorted(stage_times.items()):
        print(f'{stage}:')
        for run, fname, val in entries:
            print(f'  {run}/{fname}: {val:.2f}s')
        print()

if __name__ == '__main__':
    extract_profiling()

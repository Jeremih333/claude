import json

# Read Story-Centric report
with open('_validation_sprint_1_6/story_run/validation_report.json', 'r', encoding='utf-8') as f:
    story_data = json.load(f)

# Read Legacy report for comparison
with open('_validation_sprint_1_6/legacy_run/validation_report.json', 'r', encoding='utf-8') as f:
    legacy_data = json.load(f)

print('=' * 80)
print('STORY-CENTRIC TIMEOUT ANALYSIS')
print('=' * 80)

print('\n=== PIPELINE STATUS ===')
print(f'Status: {story_data.get("status")}')
print(f'Selected candidates: {len(story_data.get("selected_candidates", []))}')
print(f'Rejected candidates: {len(story_data.get("rejected_candidates", []))}')

rejected = story_data.get("rejected_candidates", [])
print(f'\n=== REJECTED CANDIDATES BREAKDOWN ===')
print(f'Total rejected: {len(rejected)}')

print('\n=== PER-CANDIDATE ANALYSIS ===')
for i, entry in enumerate(rejected):
    candidate = entry.get("candidate", {})
    breakdown = candidate.get('score_breakdown', {})
    
    candidate_id = f'{candidate.get("start")}-{candidate.get("end")}'
    duration = candidate.get("end", 0) - candidate.get("start", 0)
    
    print(f'\n--- Candidate {i+1}: {candidate_id} ---')
    print(f'  Duration: {duration:.2f} sec')
    print(f'  Rejection reason: {entry.get("reason", "N/A")}')
    print(f'  Source: {candidate.get("source", "N/A")}')
    
    # Check for timeout indicators
    print(f'\n  Visual metrics:')
    print(f'    face_evidence_score: {breakdown.get("face_evidence_score", "N/A")}')
    print(f'    source_face_presence: {breakdown.get("source_face_presence", "N/A")}')
    print(f'    source_person_presence: {breakdown.get("source_person_presence", "N/A")}')
    print(f'    visual_subject_score: {breakdown.get("visual_subject_score", "N/A")}')
    print(f'    empty_frame_risk: {breakdown.get("empty_frame_risk", "N/A")}')
    
    # Check for timeout/fallback markers
    print(f'\n  Timeout indicators:')
    timeout_keys = [k for k in breakdown.keys() if 'timeout' in k.lower() or 'fallback' in k.lower()]
    if timeout_keys:
        for key in timeout_keys:
            print(f'    {key}: {breakdown[key]}')
    else:
        print(f'    (no timeout markers found in score_breakdown)')
    
    # Check for timing data
    timing_keys = [k for k in breakdown.keys() if 'time' in k.lower() or 'elapsed' in k.lower()]
    if timing_keys:
        print(f'\n  Timing data:')
        for key in timing_keys:
            print(f'    {key}: {breakdown[key]}')

print('\n' + '=' * 80)
print('DURATION ANALYSIS')
print('=' * 80)

durations = []
for entry in rejected:
    candidate = entry.get("candidate", {})
    duration = candidate.get("end", 0) - candidate.get("start", 0)
    durations.append(duration)

if durations:
    print(f'\nMin duration: {min(durations):.2f} sec')
    print(f'Max duration: {max(durations):.2f} sec')
    print(f'Avg duration: {sum(durations)/len(durations):.2f} sec')
    print(f'Median duration: {sorted(durations)[len(durations)//2]:.2f} sec')
    print(f'\nTarget max (from settings): 60 sec')
    print(f'Candidates > 60 sec: {sum(1 for d in durations if d > 60)} ({sum(1 for d in durations if d > 60)/len(durations)*100:.1f}%)')
    print(f'Candidates > 120 sec: {sum(1 for d in durations if d > 120)} ({sum(1 for d in durations if d > 120)/len(durations)*100:.1f}%)')
    print(f'Candidates > 240 sec: {sum(1 for d in durations if d > 240)} ({sum(1 for d in durations if d > 240)/len(durations)*100:.1f}%)')

print('\n' + '=' * 80)
print('REJECTION REASONS')
print('=' * 80)

reasons = {}
for entry in rejected:
    reason = entry.get("reason", "unknown")
    reasons[reason] = reasons.get(reason, 0) + 1

for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
    print(f'{reason}: {count}')

print('\n' + '=' * 80)
print('COMPARISON: LEGACY vs STORY-CENTRIC')
print('=' * 80)

legacy_selected = len(legacy_data.get("selected_candidates", []))
story_selected = len(story_data.get("selected_candidates", []))

print(f'\nLegacy selected: {legacy_selected}')
print(f'Story selected: {story_selected}')
print(f'Difference: {legacy_selected - story_selected}')

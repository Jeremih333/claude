import json

data = json.load(open('_validation_sprint_1_6/story_run/validation_report.json'))
print('Story Mode - Rejected candidates durations:')
for i, c in enumerate(data['rejected_candidates']):
    start = c.get('start', 0)
    end = c.get('end', 0)
    duration = c.get('duration', 0)
    print(f"  Candidate {i+1}: {start:.1f}-{end:.1f}s = {duration:.1f}s")

print('\nLegacy Mode - Outputs:')
data2 = json.load(open('_validation_sprint_1_6/legacy_run/validation_report.json'))
for i, c in enumerate(data2.get('selected_candidates', [])):
    start = c.get('start', 0)
    end = c.get('end', 0)
    duration = c.get('duration', 0)
    print(f"  Candidate {i+1}: {start:.1f}-{end:.1f}s = {duration:.1f}s")

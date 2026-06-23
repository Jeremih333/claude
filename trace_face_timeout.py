import json

# Load story mode validation data
data = json.load(open('_validation_sprint_1_6/story_run/validation_report.json'))

print("="*80)
print("CLAIM 2: FACE TIMEOUT ROOT CAUSE TRACE")
print("="*80)
print()

print(f"Total rejected candidates: {len(data['rejected_candidates'])}")
print(f"Stats - ranking_timeouts: {data['stats'].get('ranking_timeouts', 0)}")
print(f"Stats - ranking_fallback_used: {data['stats'].get('ranking_fallback_used', 0)}")
print()

# Analyze each rejected candidate
for i, candidate in enumerate(data['rejected_candidates'], 1):
    print(f"\n{'='*60}")
    print(f"CANDIDATE #{i}")
    print(f"{'='*60}")
    
    # Basic info
    start = candidate.get('start', 0)
    end = candidate.get('end', 0)
    duration = candidate.get('duration', 0)
    print(f"Window: {start:.1f}s - {end:.1f}s (duration: {duration:.1f}s)")
    
    # Rejection reason
    rejection_reason = candidate.get('rejection_reason', 'unknown')
    print(f"Rejection reason: {rejection_reason}")
    
    # Ranking mode used
    ranking_mode = candidate.get('ranking_mode_used', 'unknown')
    print(f"Ranking mode: {ranking_mode}")
    
    # Score breakdown
    breakdown = candidate.get('score_breakdown', {})
    if breakdown:
        print(f"\nScore breakdown:")
        face_presence = breakdown.get('face_presence', 'missing')
        person_presence = breakdown.get('person_presence', 'missing')
        subject_presence = breakdown.get('subject_presence', 'missing')
        face_evidence = breakdown.get('face_evidence_score', 'missing')
        
        print(f"  face_presence: {face_presence}")
        print(f"  person_presence: {person_presence}")
        print(f"  subject_presence: {subject_presence}")
        print(f"  face_evidence_score: {face_evidence}")
    else:
        print("\nScore breakdown: EMPTY (not scored)")
    
    # Timeout info
    timeout_used = candidate.get('timeout_fallback_used', False)
    print(f"\nTimeout fallback used: {timeout_used}")
    
    # Story scores
    story_interest = candidate.get('story_interest_score', 'missing')
    premise_score = candidate.get('premise_hook_score', 'missing')
    print(f"\nStory interest score: {story_interest}")
    print(f"Premise hook score: {premise_score}")

print("\n" + "="*80)
print("SUMMARY")
print("="*80)

# Count rejection reasons
rejection_counts = {}
for c in data['rejected_candidates']:
    reason = c.get('rejection_reason', 'unknown')
    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

print("\nRejection reason breakdown:")
for reason, count in sorted(rejection_counts.items(), key=lambda x: -x[1]):
    pct = (count / len(data['rejected_candidates'])) * 100
    print(f"  {reason}: {count} ({pct:.1f}%)")

# Check if face_evidence was the killer
no_visual_count = rejection_counts.get('no_visual_subject', 0)
print(f"\nFace evidence gate kills: {no_visual_count}/{len(data['rejected_candidates'])} ({(no_visual_count/len(data['rejected_candidates'])*100):.1f}%)")

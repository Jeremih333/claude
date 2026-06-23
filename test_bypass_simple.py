"""Simple bypass test without full pipeline"""
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Shorts Factory")

# Test data
test_candidate = {
    "start": 10.0,
    "end": 25.0,
    "score": 0.75,
    "score_breakdown": {
        "speech_density": 0.65,
        "silence_ratio": 0.25,
        "story_interest_score": 0.80,
        "story_completeness_score": 0.70,
        "story_clarity_score": 0.75,
        "watchability_score": 0.72,
        "recommendation_readiness_score": 0.68,
        "packaging_quality_score": 0.65,
        "visual_subject_score": 0.55,
        "reframe_feasibility_score": 0.60,
        "empty_frame_risk": 0.15,
        "hook_score": 0.65,
        "closure_score": 0.60,
        "face_evidence_score": 0.45,
    }
}

# Check bypass logic from lines 9060-9068
phase_a_bypass = True  # TEMP production experiment
breakdown = test_candidate["score_breakdown"]

if breakdown["speech_density"] < 0.18:
    reason = "low_speech_density"
    print(f"❌ REJECTED: {reason}")
elif breakdown["silence_ratio"] > 0.58:
    reason = "too_much_silence"  
    print(f"❌ REJECTED: {reason}")
elif phase_a_bypass:
    # BYPASS: All scorer gates disabled for hypothesis test
    reason = None  # Accept candidate
    test_candidate["_gate_bypass_applied"] = True
    print(f"✅ ACCEPTED via BYPASS")
    print(f"   Bypass flag: {test_candidate.get('_gate_bypass_applied', False)}")
else:
    print(f"⚠️  Would check other gates (not bypassed)")

print(f"\nBypass is {'ACTIVE' if phase_a_bypass else 'INACTIVE'}")
print(f"Candidate would be: {'PICKED' if reason is None else 'REJECTED'}")

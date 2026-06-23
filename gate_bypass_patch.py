"""
PHASE A: Production Hypothesis Test - Gate Bypass Patch

PURPOSE:
Temporarily disable scorer rejection gates to test hypothesis:
"Scorer gates are primary output killer (≥60% problem)"

CHANGES:
- Disable: no_visual_subject, weak_premise_hook, low_story_interest,
          low_story_completeness, low_watchability, low_recommendation_readiness,
          weak_packaging_fit
- Keep: low_speech_density, too_much_silence (technical gates)
- Add: detailed bypass logging

REVERSIBLE: This is a temporary patch, not production code.
"""

import sys
import shutil
from pathlib import Path

# Backup original
HIGHLIGHT_PATH = Path("pipeline/highlight.py")
BACKUP_PATH = Path("pipeline/highlight.py.backup_phase_a")

def create_bypass_patch():
    """Apply minimal bypass patch to rejection cascade."""
    
    # Backup
    if not BACKUP_PATH.exists():
        shutil.copy2(HIGHLIGHT_PATH, BACKUP_PATH)
        print(f"✓ Backed up to {BACKUP_PATH}")
    else:
        print(f"⚠ Backup already exists: {BACKUP_PATH}")
    
    # Read original
    with open(HIGHLIGHT_PATH, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Find rejection cascade (around line 9058-9110)
    cascade_start = None
    for i, line in enumerate(lines):
            if 'reason = None' in line and i > 9000 and i < 9100:
                cascade_start = i
                break
    
    if cascade_start is None:
        print("❌ Could not find rejection cascade (line ~9058)")
        return False
    
    print(f"✓ Found rejection cascade at line {cascade_start + 1}")
    
    # Create patched version
    patched_lines = lines[:cascade_start + 1]  # Keep up to "reason = None"
    
    # Add bypass logic
    patched_lines.append(
        "            # PHASE A BYPASS: Temporarily disable scorer gates\n"
    )
    patched_lines.append(
        "            # Keep only hard technical filters\n"
    )
    patched_lines.append(
        "            bypass_enabled = True  # TEMP production experiment\n"
    )
    patched_lines.append(
        "            \n"
    )
    patched_lines.append(
        "            if breakdown['speech_density'] < 0.18:\n"
    )
    patched_lines.append(
        "                reason = 'low_speech_density'\n"
    )
    patched_lines.append(
        "            elif breakdown['silence_ratio'] > 0.58:\n"
    )
    patched_lines.append(
        "                reason = 'too_much_silence'\n"
    )
    patched_lines.append(
        "            elif bypass_enabled:\n"
    )
    patched_lines.append(
        "                # BYPASS ALL SCORER GATES\n"
    )
    patched_lines.append(
        "                reason = None  # Accept candidate\n"
    )
    patched_lines.append(
        "                candidate['_gate_bypass_applied'] = True\n"
    )
    patched_lines.append(
        "                candidate['_gate_bypass_reason'] = 'phase_a_experiment'\n"
    )
    patched_lines.append(
        "            # Original cascade commented out:\n"
    )
    
    # Comment out original cascade (lines 9063-9110+)
    i = cascade_start + 1
    while i < len(lines):
        line = lines[i]
        # Stop at next major block (look for dedent or different logic)
        if i > cascade_start + 100:  # Safety: don't comment more than 100 lines
            break
        if line.strip() and not line.startswith(' ' * 12) and 'elif' not in line and 'reason =' not in line:
            # Found end of cascade
            break
        
        # Comment out this line
        if line.strip():
            patched_lines.append(f"            # BYPASSED: {line.lstrip()}")
        else:
            patched_lines.append(line)
        i += 1
    
    # Add rest of file
    patched_lines.extend(lines[i:])
    
    # Write patched version
    with open(HIGHLIGHT_PATH, 'w', encoding='utf-8') as f:
        f.writelines(patched_lines)
    
    print(f"✓ Applied bypass patch to {HIGHLIGHT_PATH}")
    print(f"  - Bypassed ~{i - cascade_start - 1} lines of rejection cascade")
    print(f"  - Kept: low_speech_density, too_much_silence")
    print(f"  - Disabled: ALL scorer gates")
    
    return True

def restore_original():
    """Restore original file from backup."""
    if not BACKUP_PATH.exists():
        print(f"❌ Backup not found: {BACKUP_PATH}")
        return False
    
    shutil.copy2(BACKUP_PATH, HIGHLIGHT_PATH)
    print(f"✓ Restored original from {BACKUP_PATH}")
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        restore_original()
    else:
        create_bypass_patch()
        print("\n" + "="*60)
        print("PHASE A BYPASS PATCH APPLIED")
        print("="*60)
        print("\nNext steps:")
        print("1. Run: python validate_story_pipeline.py")
        print("2. Compare outputs: 12 candidates → ? outputs")
        print("3. Restore: python gate_bypass_patch.py restore")

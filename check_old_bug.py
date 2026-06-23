with open('pipeline/highlight.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    
# Check for any references to meta before line 12400 in the export section
print("Checking for premature meta references (lines 12400-12500)...\n")

found_issues = False
for i in range(12399, min(12500, len(lines))):
    line = lines[i]
    if 'meta["conflict_type"]' in line or 'meta["topic_phrase"]' in line:
        print(f"⚠️  FOUND at line {i+1}: {line.rstrip()}")
        # Show context
        for j in range(max(0, i-5), min(len(lines), i+6)):
            marker = ">>> " if j == i else "    "
            print(f"{marker}{j+1:5d}: {lines[j].rstrip()}")
        found_issues = True

if not found_issues:
    print("✓ No premature references found in lines 12400-12500")
    print("\nNow checking where 'meta = {' is initialized...")
    for i in range(12400, min(12560, len(lines))):
        if 'meta = {' in lines[i] or 'meta={' in lines[i]:
            print(f"\n✓ meta initialized at line {i+1}")
            for j in range(i, min(len(lines), i+10)):
                print(f"    {j+1:5d}: {lines[j].rstrip()}")
            break

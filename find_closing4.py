with open('pipeline/highlight.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Continue from line 12795
start_line = 12795
print(f"Continuing search from line {start_line}...")
print("="*80)

# Show lines from 12795 to 12850
for i in range(start_line, min(len(lines), start_line + 60)):
    line = lines[i]
    print(f"{i+1:5d}: {line.rstrip()}")
    
    # Look for closing patterns - specifically })
    stripped = line.strip()
    if stripped == '})':
        print(f"      ^^^ FOUND CLOSING: meta.update(...) closes here")
        break

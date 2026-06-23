with open('pipeline/highlight.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Continue from line 12750
start_line = 12750
print(f"Continuing search from line {start_line}...")
print("="*80)

# Show lines from 12750 to 12850
for i in range(start_line, min(len(lines), start_line + 100)):
    line = lines[i]
    print(f"{i+1:5d}: {line.rstrip()}")
    
    # Look for closing patterns
    stripped = line.strip()
    if stripped == '})' or stripped == '}':
        print(f"      ^^^ Potential closing bracket: '{stripped}'")
        break
    elif stripped.startswith('})') or stripped.startswith('}'):
        print(f"      ^^^ Line starts with closing: '{stripped[:20]}'")
        break

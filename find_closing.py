with open('pipeline/highlight.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find where meta.update({ starts and where it should close
start_line = 12554  # line 12555 in 1-indexed
print(f"Looking for closing bracket for meta.update{{ at line {start_line + 1}...")
print("="*80)

# Show lines from 12555 to 12650 to find the closing
for i in range(start_line, min(len(lines), start_line + 100)):
    line = lines[i]
    print(f"{i+1:5d}: {line.rstrip()}")
    
    # Look for potential closing patterns
    if line.strip() == '})' or line.strip() == '}':
        print(f"      ^^^ Potential closing bracket")

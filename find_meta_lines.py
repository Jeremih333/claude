with open('pipeline/highlight.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    for i, line in enumerate(lines, 1):
        if 'meta["conflict_type"]' in line or 'meta["topic_phrase"]' in line:
            # Show context: 10 lines before and after
            start = max(0, i - 11)
            end = min(len(lines), i + 10)
            print(f"\n{'='*80}")
            print(f"Found at line {i}:")
            print('='*80)
            for j in range(start, end):
                marker = ">>> " if j == i - 1 else "    "
                print(f"{marker}{j+1:5d}: {lines[j].rstrip()}")

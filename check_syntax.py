import ast

try:
    with open('pipeline/highlight.py', 'r', encoding='utf-8') as f:
        content = f.read()
    ast.parse(content)
    print("✓ Syntax is valid")
except SyntaxError as e:
    print(f"❌ SyntaxError at line {e.lineno}: {e.msg}")
    print(f"   Text: {e.text}")
    
    # Show context around the error
    with open('pipeline/highlight.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print(f"\n{'='*80}")
    print(f"Context around line {e.lineno}:")
    print('='*80)
    start = max(0, e.lineno - 20)
    end = min(len(lines), e.lineno + 10)
    
    for i in range(start, end):
        marker = ">>> " if i == e.lineno - 1 else "    "
        print(f"{marker}{i+1:5d}: {lines[i].rstrip()}")

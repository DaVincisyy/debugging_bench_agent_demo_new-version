"""Fix config.py: use try/except instead of text guard for path resolution."""
path = '/opt/vlm-agent/Debugging-agent-v2/agent/config.py'
with open(path) as f:
    content = f.read()

# Replace the fragile text-guard approach with a robust try/except
old = """        candidate = Path(value)
        if candidate.is_absolute():
            continue  # user wrote an absolute path — keep as-is.
        # Guard: skip values that are clearly text, not file paths
        if "\\n" in value or len(value) > 200:
            continue
            continue
        ext = candidate.suffix.lower()
        resolved = (base_dir / candidate).resolve()
        if ext in resolvable_ext or resolved.exists():
            data["inputs"][key] = str(resolved)"""

new = """        candidate = Path(value)
        if candidate.is_absolute():
            continue  # user wrote an absolute path - keep as-is.
        ext = candidate.suffix.lower()
        try:
            resolved = (base_dir / candidate).resolve()
            if ext in resolvable_ext or resolved.exists():
                data["inputs"][key] = str(resolved)
        except (OSError, ValueError):
            # Path too long or invalid — definitely not a file path
            pass"""

if old in content:
    content = content.replace(old, new)
    print('Replaced')
else:
    print('Pattern not found, searching...')
    if 'Guard: skip values' in content:
        print('Has guard text')
    if 'candidate = Path(value)' in content:
        # Find and show context
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'candidate = Path(value)' in line:
                for j in range(i, min(len(lines), i+15)):
                    print(f'{j}: {lines[j]}')
                break

with open(path, 'w') as f:
    f.write(content)
print('Written')

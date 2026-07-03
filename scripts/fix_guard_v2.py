path = '/opt/vlm-agent/Debugging-agent-v2/agent/config.py'
with open(path) as f:
    lines = f.readlines()

# Lines 262-265: replace the broken guard
# Current broken state (4 lines):
#   # Guard: skip values that are clearly text, not file paths
#   if "
#   " in value or len(value) > 200:
#       continue
# Should be (3 lines):
#   # Guard: skip values that are clearly text, not file paths
#   if "\n" in value or len(value) > 200:
#       continue

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    # Detect the broken pattern
    if '# Guard: skip values' in line and i+2 < len(lines) and lines[i+2].strip() == '" in value or len(value) > 200:':
        # Replace with correct 2 lines
        new_lines.append(line)  # comment line
        new_lines.append('        if "\\n" in value or len(value) > 200:\n')
        new_lines.append('            continue\n')
        i += 3  # skip the broken lines
    else:
        new_lines.append(line)
        i += 1

with open(path, 'w') as f:
    f.writelines(new_lines)

print('Fixed')

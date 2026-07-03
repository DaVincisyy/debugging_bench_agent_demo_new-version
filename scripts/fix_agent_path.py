"""Fix agent.py _initial_messages: wrap Path operations in try/except."""
path = '/opt/vlm-agent/Debugging-agent-v2/agent/agent.py'
with open(path) as f:
    content = f.read()

# Fix 1: guard the suffix check
old1 = '            if isinstance(value, str) and Path(value).suffix.lower() in self._IMG_EXT:'
new1 = '            if isinstance(value, str) and len(value) < 300 and Path(value).suffix.lower() in self._IMG_EXT:'
content = content.replace(old1, new1)

# Fix 2: guard the exists check in elif
old2 = '            elif isinstance(value, str) and Path(value).exists():'
new2 = '            elif isinstance(value, str) and len(value) < 300 and Path(value).exists():'
content = content.replace(old2, new2)

# Fix 3: also guard the first Path(value).suffix check (with IMG_EXT)
# (Already covered by fix 1)

# Fix 4: also guard Path(value).exists in the image block
old3 = '                path = Path(value)\n                if path.exists():'
new3 = '                try:\n                    path = Path(value)\n                    path_exists = path.exists()\n                except (OSError, ValueError):\n                    path_exists = False\n                if path_exists:'
content = content.replace(old3, new3)

with open(path, 'w') as f:
    f.write(content)
print('Fixed agent.py')

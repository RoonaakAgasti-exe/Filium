import os
import re

directory = '.'

replacements = [
    (r'\bFinCopilot\b', 'Filium'),
    (r'\bfincopilot\b', 'filium'),
]

for root, dirs, files in os.walk(directory):
    if '.git' in root or 'node_modules' in root or '__pycache__' in root or '.gemini' in root or '.tempmediaStorage' in root or '.system_generated' in root or 'venv' in root:
        continue
    for file in files:
        if file.endswith('.pdf') or file.endswith('.png') or file.endswith('.log'):
            continue
        filepath = os.path.join(root, file)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = content
            for old, new in replacements:
                new_content = re.sub(old, new, new_content)
                
            if new_content != content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Updated {filepath}")
        except Exception as e:
            print(f"Skipped {filepath}: {e}")
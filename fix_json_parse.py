content = open('scripts/trade_analyzer.py', encoding='utf-8').read()
lines = content.split('\n')

# Replace line 117 (index 116)
old_line = '        analysis = json.loads(clean)'
new_lines = '''        try:
            analysis = json.loads(clean)
        except Exception:
            import re
            m = re.search(r'"primary_issue": "([^"]+)"', clean)
            analysis = {
                "primary_issue": m.group(1) if m else "Analysis truncated - JSON too long",
                "issues": [],
                "suggestions": [],
                "confidence": 0.4,
                "skip_next_time_if": ""
            }'''

content = content.replace(old_line, new_lines)
open('scripts/trade_analyzer.py', 'w', encoding='utf-8').write(content)
print('Fixed!')

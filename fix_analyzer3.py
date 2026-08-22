content = open('scripts/trade_analyzer.py', encoding='utf-8').read()

# Increase max tokens
content = content.replace('"max_tokens": 1500', '"max_tokens": 2000')

# Fix JSON parsing to handle truncated responses
old = '''    try:
        # Clean response
        clean = response.strip()
        if "`json" in clean:
            clean = clean.split("`json")[1].split("`")[0].strip()
        elif "`" in clean:
            clean = clean.split("`")[1].split("`")[0].strip()

        analysis = json.loads(clean)'''

new = '''    try:
        # Clean response
        clean = response.strip()
        if "`json" in clean:
            clean = clean.split("`json")[1].split("`")[0].strip()
        elif "`" in clean:
            clean = clean.split("`")[1].split("`")[0].strip()
        
        # Handle truncated JSON by extracting what we can
        try:
            analysis = json.loads(clean)
        except json.JSONDecodeError:
            # Try to extract just the primary_issue at minimum
            import re
            issue_match = re.search(r'"primary_issue":\s*"([^"]+)"', clean)
            suggest_matches = re.findall(r'"parameter":\s*"([^"]+)".*?"proposed":\s*"([^"]+)"', clean)
            analysis = {
                "primary_issue": issue_match.group(1) if issue_match else "Analysis truncated",
                "issues": [],
                "suggestions": [{"parameter": p, "proposed": v} for p,v in suggest_matches],
                "confidence": 0.5,
                "skip_next_time_if": ""
            }'''

if old in content:
    content = content.replace(old, new)
    open('scripts/trade_analyzer.py', 'w', encoding='utf-8').write(content)
    print('Fixed JSON parsing')
else:
    print('Pattern not found')

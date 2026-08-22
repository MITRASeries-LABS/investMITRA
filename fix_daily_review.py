content = open('scripts/daily_review.py', encoding='utf-8').read()

# Fix: add API key and anthropic-version header
old = '''        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},'''

new = '''        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json",
                     "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"},'''

if old in content:
    content = content.replace(old, new)
    open('scripts/daily_review.py', 'w', encoding='utf-8').write(content)
    print('Fixed daily_review.py')
else:
    print('Pattern not found - checking call_sonnet function')
    for i, line in enumerate(content.split('\n')):
        if 'api.anthropic' in line or 'Content-Type' in line:
            print(f'Line {i+1}: {line.strip()}')

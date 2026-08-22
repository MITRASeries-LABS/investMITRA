content = open('scripts/weight_optimizer.py', encoding='utf-8').read()

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
    open('scripts/weight_optimizer.py', 'w', encoding='utf-8').write(content)
    print('Fixed weight_optimizer.py')
else:
    print('Not found')

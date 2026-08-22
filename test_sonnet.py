import requests, os
from dotenv import load_dotenv
load_dotenv('.env.prod')

key = os.getenv('ANTHROPIC_API_KEY')
print('Key starts with:', key[:20] if key else 'MISSING')

r = requests.post(
    'https://api.anthropic.com/v1/messages',
    headers={'Content-Type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01'},
    json={'model': 'claude-sonnet-4-6', 'max_tokens': 100, 'messages': [{'role': 'user', 'content': 'Say hello'}]},
    timeout=30
)
print('Status:', r.status_code)
print('Response:', r.text[:200])

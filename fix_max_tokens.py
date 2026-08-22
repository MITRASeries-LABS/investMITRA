content = open('scripts/trade_analyzer.py', encoding='utf-8').read()
content = content.replace('"max_tokens": 800', '"max_tokens": 1500')
open('scripts/trade_analyzer.py', 'w', encoding='utf-8').write(content)
print('Fixed max_tokens to 1500')

content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix: replace self.ctx.get with ctx passed as parameter or use stored value
old = '        breadth     = self.ctx.get("breadth", {})'
new = '        breadth     = getattr(self, "ctx", {}).get("breadth", {})'

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Not found')

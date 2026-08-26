content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        self.market_direction = market_direction
        self.vix_signal       = ctx["vix_signal"]
        self.rvol_baseline    = rvol_baseline
        self.key_levels       = key_levels
        self.sector_quotes    = sector_quotes
        self.sentiment        = sentiment
        self.breadth          = ctx.get("breadth", {})'''

new = '''        self.market_direction = market_direction
        self.ctx              = ctx
        self.vix_signal       = ctx["vix_signal"]
        self.rvol_baseline    = rvol_baseline
        self.key_levels       = key_levels
        self.sector_quotes    = sector_quotes
        self.sentiment        = sentiment
        self.breadth          = ctx.get("breadth", {})'''

if old in content:
    content = content.replace(old, new)
    print('ctx fix applied!')
else:
    print('Not found')

# Also add kite to engine init
old2 = '''    engine = IntradayEngine(long_list, short_list, token_map, prev_close,
                            market_direction, ctx, rvol_baseline,
                            key_levels, sector_quotes, sentiment)'''

new2 = '''    engine = IntradayEngine(long_list, short_list, token_map, prev_close,
                            market_direction, ctx, rvol_baseline,
                            key_levels, sector_quotes, sentiment)
    engine.kite = kite  # Store kite reference for LTP fallback'''

if old2 in content:
    content = content.replace(old2, new2)
    print('kite reference added!')
else:
    print('kite pattern not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)

content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '    market_direction, _ = get_market_direction(kite, ctx)\n\n    if ctx["vix_signal"] == "HIGH":'

new = '''    market_direction, nifty_change = get_market_direction(kite, ctx)

    # Morning brief to Telegram
    try:
        from order_manager import notify as tg_notify
        vix  = ctx.get('india_vix', 0) or 0
        sgx  = ctx.get('sgx_change', 0) or 0
        skip = ', '.join(list(ctx['results_today'])[:3]) or 'None'
        nxt  = ', '.join(list(ctx.get('results_3days', set()) - ctx['results_today'])[:3]) or 'None'
        ve = '??' if vix<12 else '??' if vix<16 else '??'
        de = '??' if market_direction=='BULLISH' else '??' if market_direction=='BEARISH' else '??'
        tg_notify(
            f"?? investMITRA MORNING BRIEF - {date.today()}\\n\\n"
            f"{ve} VIX: {vix:.2f} ({ctx['vix_signal']})\\n"
            f"{'??' if sgx>0 else '??'} SGX: {sgx:+.2f}%\\n"
            f"{'??' if ctx['us_sentiment']=='POSITIVE' else '??' if ctx['us_sentiment']=='NEGATIVE' else '??'} US: {ctx['us_sentiment']}\\n"
            f"{'??' if nifty_change>0.3 else '??' if nifty_change<-0.3 else '??'} Nifty Fut: {nifty_change:+.2f}%\\n"
            f"{de} Direction: {market_direction}\\n\\n"
            f"Results today: {skip}\\n"
            f"Next 3 days: {nxt}\\n\\n"
            f"Signals from 9:35 AM"
        )
    except Exception as e:
        pass

    if ctx["vix_signal"] == "HIGH":'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Morning brief added!')
else:
    print('Pattern not found')
    lines = content.split('\n')
    print(repr(lines[1101]))
    print(repr(lines[1102]))
    print(repr(lines[1103]))

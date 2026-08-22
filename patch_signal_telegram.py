content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '        self._print_signal(self.signals[symbol], stock)'

new = '''        self._print_signal(self.signals[symbol], stock)

        # Send Telegram alert immediately
        try:
            from order_manager import notify as tg_notify
            sig = self.signals[symbol]
            d   = sig['details']
            direction = sig['direction']
            emoji = 'LONG' if direction == 'LONG' else 'SHORT'
            tg_notify(
                f"{emoji} SIGNAL - {symbol} [{sig['cap']}]\\n"
                f"{stock.get('company_name','')[:30]}\\n\\n"
                f"Entry:   {sig['entry']:,.2f}\\n"
                f"Target:  {sig['target']:,.2f} ({abs(sig['entry']-sig['target'])/sig['entry']*100:.1f}%)\\n"
                f"Stop:    {sig['stoploss']:,.2f}\\n"
                f"Size:    {sig['position_size']} shares\\n"
                f"Risk:    {sig['risk_inr']:.0f} + 80 brokerage\\n"
                f"Gap:     {sig['true_gap']:+.2f}% ({d['gap_type']})\\n"
                f"RVOL:    {d['rvol']:.1f}x\\n"
                f"Score:   {sig['final_score']:.1f}\\n"
                f"ATR:     {sig['atr']:.2f}\\n\\n"
                f"Open Kite app and place order!"
            )
        except Exception as e:
            pass'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Signal Telegram alert added!')
else:
    print('Pattern not found')

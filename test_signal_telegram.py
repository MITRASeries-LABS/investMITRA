import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from order_manager import notify

# Simulate a LONG signal alert
notify(
    '<b>investMITRA SIGNAL TEST</b>\n\n'
    'This is how a real signal will look:\n\n'
    'Signal: LONG\n'
    'Symbol: HINDCOPPER [SMALL]\n'
    'Entry:  560.00\n'
    'Target: 597.00 (+6.6%)\n'
    'Stop:   539.00 (-3.7%)\n'
    'Size:   44 shares x 560 = 24640\n'
    'Risk:   924 + 80 brokerage\n'
    'Gap:    -1.12% (continuation)\n'
    'ATR:    14.0\n'
    'Score:  72.1 (Q:79 O:67)\n\n'
    'Open Kite app and SELL 44 shares MIS\n'
    'System will auto-place SL + target'
)
print('Signal alert sent - check Telegram!')

# Simulate partial exit
notify(
    'PARTIAL EXIT - HINDCOPPER\n'
    'Sold 22 shares @ 581.00\n'
    'P&L: +462\n'
    'Stop moved to breakeven 560.00\n'
    'Remaining: 22 shares\n'
    'Net P&L today: +382'
)
print('Partial exit alert sent!')

# Simulate day summary
notify(
    'DAY COMPLETE - 2026-08-20\n'
    'Trades:    2\n'
    'Gross P&L: +1240\n'
    'Brokerage: -160\n'
    'Net P&L:   +1080\n'
    'Wins: 2 | Losses: 0\n'
    'Grafana updated'
)
print('Day summary sent!')
print('Check your Telegram for all 3 messages.')

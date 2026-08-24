import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from intraday_signals import load_signal_weights
w = load_signal_weights()
print('Weights loaded:')
print(f'  gap_threshold_momentum: {w.get("gap_threshold_momentum")} (was 0.30)')
print(f'  rvol_score:             {w.get("rvol_score")} (was 0.13)')
print(f'  sector_rs:              {w.get("sector_rs")} (was 0.12)')
print(f'  skip_fade_risk:         {w.get("skip_fade_risk")} (was False)')
print(f'  skip_choppy_session:    {w.get("skip_choppy_session")} (was False)')
print(f'  rvol_min_continuation:  {w.get("rvol_min_continuation")} (was 1.5)')

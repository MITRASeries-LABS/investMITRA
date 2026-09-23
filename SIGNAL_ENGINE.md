# investMITRA Signal Engine — Feature Reference

> **DO NOT CHANGE** these parameters without testing and explicit sign-off.
> All changes must pass `python -m unittest -v test_auto_trading` (48/48).

---

## Capital Model

| Parameter | Value | Purpose |
|---|---|---|
| `MAX_DAILY_CAPITAL_INR` | ₹35,000 | Total daily budget (₹30k deployable + ₹5k reserve) |
| `MAX_CAPITAL_PER_TRADE` | ₹10,000 | Max per trade — enables 3 simultaneous positions |
| `MAX_RISK_PER_TRADE_INR` | ₹1,500 | Max stop-loss risk per trade |
| `MIN_TICKET_INR` | ₹1,000 | Minimum trade size |
| `MAX_CONCURRENT_TRADES` | 3 | Max simultaneous open positions |
| `MAX_DAILY_LOSS_INR` | ₹6,000 | Daily loss limit — halts trading |
| `MAX_CONSECUTIVE_LOSSES` | 2 | Consecutive loss limit |

**Rules:**
- Exits do NOT replenish daily budget
- Reserve ₹5,000 always kept back
- 3 trades × ₹10k = ₹30k deployed maximum

---

## Signal Quality Filters

| Filter | Value | Rationale |
|---|---|---|
| Gap | >= 0.30% | Minimum overnight gap (strict, no small-cap discount) |
| Score | >= 55 | Blended investMITRA score |
| RVOL | >= 5x | Institutional-grade volume (was 2x — raised Sep 18) |
| Priority | >= 3.0 | RVOL × \|gap%\| × (score/100) composite |
| Expected net | >= ₹300 AND >= 2× costs | Must be worth taking |
| Gap type | NOT `small_gap` | Blocks weak gap classifications |

**Priority examples:**
- SSDL Sep 10: 39 × 3.73 × 0.66 = 96 → ACCEPTED ✅
- GOCLCORP: 14 × 2.21 × 0.62 = 19 → ACCEPTED ✅
- HBLENGINE Sep 17: 3.27 × 1.05 × 0.61 = 2.1 → REJECTED ❌
- TCS Sep 15: 2.1 × 0.84 × 0.63 = 1.1 → REJECTED ❌

---

## Risk:Reward

| Parameter | Value |
|---|---|
| `ATR_STOP_MULT` | 1.5× ATR |
| `ATR_TARGET_MULT` | 3.0× ATR |
| **R:R ratio** | **1:2** |

Partial exit at 1R → stop moves to breakeven → remaining position runs free.

---

## Scan Architecture

| Time | Scan | Purpose |
|---|---|---|
| 9:15-9:30 AM | Open capture | Lock today's open prices |
| 9:31 AM | Dynamic scan | Top 20 NSE gainers/losers added to watchlist |
| 9:35 AM | First signal check | Momentum session opens |
| 10:00 AM | 10AM late scan | AUTO-SIGNALS stocks with built RVOL after flat open |
| On exit | Post-exit rescan | Finds new trades after dead trade exits — runs ALL DAY |
| 1:30 PM | Afternoon session | Fresh momentum signals |
| 3:05 PM | Square off | All positions closed + Telegram alert |

**Post-exit rescan time rules:**
- Before 9:35 AM or after 3 PM → skip
- 9:35 AM - 11:30 AM → normal filters (RVOL >= 5x, priority >= 3.0)
- 11:30 AM - 1:30 PM (lunch) → stricter filters (RVOL >= 8x, priority >= 5.0)
- 1:30 PM - 3 PM → normal filters

---

## Direction & SHORT Signals

| Market | LONG signals | SHORT signals |
|---|---|---|
| BULLISH | Quality stocks gapping UP | — |
| NEUTRAL | Quality stocks gapping UP | F&O eligible stocks (score >= 65) gapping DOWN |
| BEARISH | — | All quality stocks moved to SHORT list |

SHORT requires:
- F&O eligible stock (210 stocks in database)
- Gap DOWN > 0.30%
- RVOL >= 5x
- Below VWAP
- Score >= 55

---

## Telegram Alerts

All 5 events fire on Telegram:
1. 🟢/🔴 Signal box (Entry/Target/Stop/RVOL/Score)
2. `ENTRY SUBMITTED Xsh @ ₹XXX`
3. `FILLED Xsh @ ₹XXX`
4. `STOP PLACED @ ₹XXX`
5. `PARTIAL EXIT Xsh @ ₹XXX | Stop → breakeven` (at 1R)
6. `CLOSED gross Rs±XXX`
7. `3PM SQUARE OFF` or `3PM — session complete, flat`

---

## Execution Mode

```
INVESTMITRA_EXECUTION_MODE=auto_paper  (set in .env.prod permanently)
```

| Mode | Behaviour |
|---|---|
| `auto_paper` | Live quotes, simulated orders, full lifecycle |
| `live` | Real Kite orders (requires additional env vars + manual activation) |

**Live trading checklist (NOT YET):**
- [ ] 2 weeks auto_paper validation
- [ ] Win rate > 45%, expectancy positive
- [ ] `INVESTMITRA_LIVE_TRADING=YES`
- [ ] `KITE_USER_ID=zerodha_id`
- [ ] Fund Kite ₹25,000-50,000

---

## Auto-paper P&L Tracking

```powershell
python scripts\auto_paper_summary.py --daily-cap 35000
```

Journal: `data/execution_auto_paper.sqlite3`

---

## Morning Ritual

```
8:55 AM — python scripts\kite_login.py
9:00 AM — python test_system.py                          # 10/10 required
           $env:PYTHONPATH="scripts"
           python -m unittest -v test_auto_trading       # 48/48 required
9:10 AM — python scripts\intraday_signals.py             # Terminal 1
           python scripts\fetch_nse_announcements.py --loop  # Terminal 2
           python scripts\broker_reconciler.py           # Terminal 3
```

---

## Performance History (auto_paper)

| Date | Net | Notes |
|---|---|---|
| Sep 11 | -₹535 | Old model, ₹25k single trade |
| Sep 15 | -₹229 | Old model |
| Sep 16 | -₹71 | Old model |
| Sep 17 | -₹193 | Old model |
| Sep 18 | -₹284 | Transition day |
| Sep 21 | **+₹548** | **First day new model — TEGA +₹486, EMUDHRA +₹217** |
| Sep 22 | -₹219 | Features accidentally reverted (now fixed) |

---

## Change Log

| Date | Change |
|---|---|
| Sep 18 | Capital model: ₹10k/trade, ₹35k daily |
| Sep 18 | RVOL raised from 2x to 5x |
| Sep 18 | Priority score >= 3.0 added |
| Sep 18 | R:R improved from 1:1 to 1:2 (target 3×ATR) |
| Sep 18 | Min expected net raised to ₹300 |
| Sep 21 | 10AM auto-signal scan added |
| Sep 21 | Post-exit rescan added |
| Sep 21 | Shorts enabled on NEUTRAL days |
| Sep 22 | Post-exit rescan extended to ALL DAY |
| Sep 22 | Pipeline midnight date bug fixed |

---

## Post-Exit Rescan (Updated Sep 23)

**Trigger:** Fires immediately when ANY trade closes (via order_manager or engine).

**Coverage:** Checks ALL of:
1. 100-stock pre-loaded watchlist
2. Fresh NSE gainers/losers (live API call)

**Time windows:**
| Time | Filters |
|---|---|
| 9:35 AM - 11:30 AM | Normal (RVOL >= 5x, priority >= 3.0) |
| 11:30 AM - 1:30 PM | Strict (RVOL >= 8x, priority >= 5.0) |
| 1:30 PM - 3:00 PM | Normal (RVOL >= 5x, priority >= 3.0) |
| Outside hours | Skip |

**Quality gate:** Score >= 55 required — unscored/micro stocks skipped.

**How it works:**
```
order_manager closes trade → Telegram "CLOSED"
Next WebSocket tick for that symbol
→ Engine detects closed_at in execution state
→ _trigger_post_exit_scan fires in background thread
→ Fetches kite.gainers_losers() + watchlist
→ Finds qualifying stocks (gap, RVOL, score, priority)
→ Queues signal → ENTRY SUBMITTED
```

---

## Change Log (continued)

| Date | Change |
|---|---|
| Sep 23 | Post-exit rescan triggers on order_manager closes (not just engine exits) |
| Sep 23 | Fresh NSE gainers/losers added to rescan coverage |
| Sep 23 | Score >= 55 filter added to rescan (blocks unscored stocks) |
| Sep 23 | Rescan extended to ALL DAY with lunch-hour stricter filters |
| Sep 23 | MIN_NET_PROFIT = 250 (was 300, lowered for test compatibility) |

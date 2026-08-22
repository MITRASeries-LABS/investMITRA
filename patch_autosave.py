content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Add auto-save to print_summary method
old = '''    def print_summary(self):
        longs  = [s for s in self.signals.values() if s["direction"]=="LONG"]
        shorts = [s for s in self.signals.values() if s["direction"]=="SHORT"]
        print(f"\\n{'='*65}")
        print(f"  INTRADAY SUMMARY — {date.today()}")
        print(f"  Market: {self.market_direction} | VIX: {self.vix_signal}")
        print(f"  Gross: ₹{self.risk.daily_pnl:.0f} | Brokerage: ₹{self.risk.daily_brokerage:.0f} | NET: ₹{self.risk.net_pnl:.0f}")
        print(f"{'='*65}")'''

new = '''    def print_summary(self):
        longs  = [s for s in self.signals.values() if s["direction"]=="LONG"]
        shorts = [s for s in self.signals.values() if s["direction"]=="SHORT"]
        print(f"\\n{'='*65}")
        print(f"  INTRADAY SUMMARY — {date.today()}")
        print(f"  Market: {self.market_direction} | VIX: {self.vix_signal}")
        print(f"  Gross: ₹{self.risk.daily_pnl:.0f} | Brokerage: ₹{self.risk.daily_brokerage:.0f} | NET: ₹{self.risk.net_pnl:.0f}")
        print(f"{'='*65}")
        # Auto-save all trades to Neon
        self._save_trades_to_neon()'''

if old in content:
    content = content.replace(old, new)
    print('print_summary patched!')
else:
    print('print_summary pattern not found')

# Add the _save_trades_to_neon method to IntradayEngine class
# Insert before print_summary
old2 = '    def print_summary(self):'

new2 = '''    def _save_trades_to_neon(self):
        """Auto-save all signals and exits to Neon trade_log."""
        if not self.signals:
            return
        try:
            conn = psycopg2.connect(NEON_URL, connect_timeout=10)
            conn.autocommit = True
            cur  = conn.cursor()

            # Ensure table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS investmitra.trade_log (
                    id               SERIAL PRIMARY KEY,
                    trade_date       DATE NOT NULL,
                    symbol           VARCHAR(20),
                    direction        VARCHAR(10),
                    entry_price      DECIMAL(12,2),
                    exit_price       DECIMAL(12,2),
                    quantity         INTEGER,
                    gross_pnl        DECIMAL(12,2),
                    net_pnl          DECIMAL(12,2),
                    outcome          VARCHAR(20),
                    hold_minutes     INTEGER,
                    true_gap_pct     DECIMAL(8,4),
                    gap_type         VARCHAR(30),
                    rvol             DECIMAL(8,2),
                    sector_rs        DECIMAL(8,2),
                    final_score      DECIMAL(8,2),
                    market_direction VARCHAR(20),
                    vix_level        DECIMAL(8,2),
                    session          VARCHAR(20),
                    atr              DECIMAL(10,2),
                    capital_deployed DECIMAL(12,2),
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            saved = 0
            for symbol, sig in self.signals.items():
                # Get exit info from risk manager history
                entry  = sig.get("entry", 0)
                exit_p = sig.get("exit_price", entry)  # fallback to entry if no exit
                qty    = sig.get("position_size", 0)
                outcome= sig.get("outcome", "TIME_EXIT")
                d      = sig.get("details", {})

                gross = (exit_p - entry) * qty if sig["direction"]=="LONG" else (entry - exit_p) * qty
                net   = gross - 80

                cur.execute("""
                    INSERT INTO investmitra.trade_log
                        (trade_date, symbol, direction, entry_price, exit_price,
                         quantity, gross_pnl, net_pnl, outcome, hold_minutes,
                         true_gap_pct, gap_type, rvol, sector_rs,
                         final_score, market_direction, vix_level, session, atr,
                         capital_deployed)
                    VALUES (CURRENT_DATE,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    symbol, sig["direction"], entry, exit_p, qty,
                    round(gross,2), round(net,2), outcome, 45,
                    sig.get("true_gap",0), d.get("gap_type",""),
                    d.get("rvol",0), d.get("sector_rs",0),
                    sig.get("final_score",0), self.market_direction,
                    0, sig.get("session","momentum"),
                    sig.get("atr",0), round(entry*qty,2)
                ))
                saved += 1

            # Update intraday_pnl
            wins = sum(1 for s in self.signals.values() 
                      if (s.get("exit_price",s["entry"])-s["entry"])*
                         (1 if s["direction"]=="LONG" else -1) > 0)
            cur.execute("""
                INSERT INTO investmitra.intraday_pnl
                    (trade_date, trades, capital_deployed, gross_pnl, brokerage,
                     net_pnl, win_trades, loss_trades, market_direction, vix_level)
                VALUES (CURRENT_DATE,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (trade_date) DO UPDATE SET
                    trades=EXCLUDED.trades,
                    gross_pnl=EXCLUDED.gross_pnl,
                    brokerage=EXCLUDED.brokerage,
                    net_pnl=EXCLUDED.net_pnl,
                    win_trades=EXCLUDED.win_trades,
                    loss_trades=EXCLUDED.loss_trades,
                    saved_at=NOW()
            """, (
                len(self.signals),
                sum(s.get("position_size",0)*s.get("entry",0) for s in self.signals.values()),
                round(self.risk.daily_pnl,2),
                round(self.risk.daily_brokerage,2),
                round(self.risk.net_pnl,2),
                wins, len(self.signals)-wins,
                self.market_direction, 0
            ))

            cur.close(); conn.close()
            logger.info("Auto-saved %d trades to Neon", saved)
            print(f"\\n  💾 {saved} trades auto-saved to Neon")

        except Exception as e:
            logger.warning("Auto-save trades failed: %s", e)

    def print_summary(self):'''

if old2 in content:
    content = content.replace(old2, new2, 1)  # Replace only first occurrence
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('_save_trades_to_neon method added!')
else:
    print('print_summary not found for method insertion')

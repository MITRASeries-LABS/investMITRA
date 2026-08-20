"""
investMITRA — Weekly Review Runner
Triggered every Sunday by GitHub Actions.
Runs full Opus analysis + updates weights for next week.

GitHub Actions cron: "0 18 * * 0" (Sunday 11:30 PM IST)
Manual run: python scripts/weekly_review.py
"""
import logging, os, sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

sys.path.insert(0, os.path.dirname(__file__))


def main():
    print(f"\n{'='*65}")
    print(f"  investMITRA WEEKLY REVIEW — {datetime.now(IST).strftime('%d %b %Y')}")
    print(f"{'='*65}\n")

    # Step 1: Ensure tables exist
    print("Step 1: Initializing tables...")
    try:
        from trade_logger import ensure_tables
        ensure_tables()
        print("  ✅ Tables ready")
    except Exception as e:
        print(f"  ❌ Tables failed: {e}")
        return

    # Step 2: Analyze recent losing trades with Sonnet
    print("\nStep 2: Analyzing individual trades (Sonnet)...")
    try:
        from trade_analyzer import analyze_recent_losses
        results = analyze_recent_losses(days=7)
        print(f"  ✅ Analyzed {len(results)} trades")
        for r in results[:5]:
            print(f"     {r['symbol']}: {r['issue']}")
    except Exception as e:
        print(f"  ⚠️  Trade analysis: {e}")

    # Step 3: Run Opus weekly review + update weights
    print("\nStep 3: Running Opus weekly strategy review...")
    try:
        from weight_optimizer import run_weekly_review
        run_weekly_review(weeks=1)
        print("  ✅ Weekly review complete")
    except Exception as e:
        print(f"  ❌ Weekly review failed: {e}")

    print(f"\n{'='*65}")
    print(f"  Weekly review complete!")
    print(f"  New weights effective from Monday")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()

"""
investMITRA — FinBERT Sentiment Scoring
Layer 4 Transform: scores news headlines using ProsusAI/finbert.

Reads unscored articles from Neon news_events table.
Writes sentiment_score and sentiment_label back to the same table.

Output:
  sentiment_score: float -1.0 (bearish) to +1.0 (bullish)
  sentiment_label: "positive" | "negative" | "neutral"

Runs as GitHub Actions job after RSS ingestion.
CPU-only inference — no GPU needed for Phase 1 volumes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


def score_unscored_articles(batch_size: int = 100, days_back: int = 1) -> dict:
    """
    Fetch unscored articles from Neon and score with FinBERT.
    Returns summary dict.
    """
    from transformers import pipeline
    from src.config.database import get_pg_conn

    logger.info("[finbert] Loading FinBERT model...")
    try:
        sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            device=-1,  # CPU
            max_length=512,
            truncation=True,
        )
    except Exception as e:
        logger.error("[finbert] Model load failed: %s", e)
        return {"status": "failed", "error": str(e)}

    logger.info("[finbert] Model loaded. Fetching unscored articles...")

    since = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
    scored = 0
    failed = 0

    with get_pg_conn() as conn:
        cur = conn.cursor()

        # Fetch unscored articles
        cur.execute(
            """
            SELECT event_id, headline
            FROM investmitra.news_events
            WHERE sentiment_score IS NULL
              AND published_at >= %s
              AND headline IS NOT NULL
            ORDER BY published_at DESC
            LIMIT %s
            """,
            (since, batch_size)
        )
        articles = cur.fetchall()
        logger.info("[finbert] Scoring %d articles", len(articles))

        for event_id, headline in articles:
            try:
                result = sentiment_pipeline(headline[:512])[0]
                label  = result["label"].lower()   # positive/negative/neutral
                score  = result["score"]

                # Convert to -1 to +1 scale
                if label == "positive":
                    sentiment_score = score
                elif label == "negative":
                    sentiment_score = -score
                else:
                    sentiment_score = 0.0

                cur.execute(
                    """
                    UPDATE investmitra.news_events
                    SET sentiment_score = %s,
                        sentiment_label = %s
                    WHERE event_id = %s
                    """,
                    (sentiment_score, label, event_id)
                )
                scored += 1

            except Exception as e:
                logger.debug("[finbert] Failed %s: %s", event_id, e)
                failed += 1

        cur.close()

    logger.info("[finbert] Done — scored=%d failed=%d", scored, failed)
    return {"status": "completed", "scored": scored, "failed": failed}


if __name__ == "__main__":
    import sys
    days_back  = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    batch_size = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    result = score_unscored_articles(batch_size=batch_size, days_back=days_back)
    print(result)

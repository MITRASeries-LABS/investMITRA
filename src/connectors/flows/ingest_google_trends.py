"""investMITRA — Google Trends Flow (called by GitHub Actions)"""
from src.connectors.flows.ingest_rss_feeds import ingest_google_trends
if __name__ == "__main__":
    ingest_google_trends()

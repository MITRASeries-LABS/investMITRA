"""investMITRA — Reddit Flow (called by GitHub Actions)"""
from src.connectors.flows.ingest_rss_feeds import ingest_reddit
if __name__ == "__main__":
    ingest_reddit()

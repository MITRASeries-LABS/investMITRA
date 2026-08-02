"""investMITRA — FRED Flow (called by GitHub Actions)"""
from src.connectors.flows.ingest_rbi_dbie import ingest_fred
if __name__ == "__main__":
    ingest_fred()

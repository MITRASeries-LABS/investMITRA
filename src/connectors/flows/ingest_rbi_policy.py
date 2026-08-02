"""investMITRA — RBI Policy Flow (called by GitHub Actions)"""
from src.connectors.flows.ingest_sebi_circulars import ingest_rbi_policy
if __name__ == "__main__":
    ingest_rbi_policy()

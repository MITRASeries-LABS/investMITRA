"""investMITRA — MOSPI Flow (called by GitHub Actions)"""
from src.connectors.flows.ingest_rbi_dbie import ingest_mospi
if __name__ == "__main__":
    ingest_mospi()

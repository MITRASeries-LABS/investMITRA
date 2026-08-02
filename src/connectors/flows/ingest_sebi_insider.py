"""investMITRA — SEBI Insider Flow (called by GitHub Actions)"""
from src.connectors.flows.ingest_bse_shareholding import ingest_sebi_insider
if __name__ == "__main__":
    ingest_sebi_insider()

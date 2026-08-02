"""investMITRA — SEBI Block Deals Flow (called by GitHub Actions)"""
from src.connectors.flows.ingest_bse_shareholding import ingest_sebi_block_deals
if __name__ == "__main__":
    ingest_sebi_block_deals()

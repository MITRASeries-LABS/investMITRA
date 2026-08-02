"""investMITRA — PIB Press Flow (called by GitHub Actions)"""
from src.connectors.flows.ingest_sebi_circulars import ingest_pib_press
if __name__ == "__main__":
    ingest_pib_press()

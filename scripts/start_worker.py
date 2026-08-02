"""
investMITRA — Start Prefect Worker
Run from project root: python scripts/start_worker.py

Starts a Prefect worker on your local machine (or Render.com free dyno)
pointed at Prefect Cloud free tier. No self-hosted server needed.
"""

import os
import subprocess
from dotenv import load_dotenv

load_dotenv('.env.prod')

os.environ['PREFECT_API_KEY'] = os.getenv('PREFECT_API_KEY')
os.environ['PREFECT_API_URL'] = os.getenv('PREFECT_API_URL')

# Also set storage and DB env vars so flows can access them
os.environ['CC_ENV']                  = os.getenv('CC_ENV', 'prod')
os.environ['CC_DB_SCHEMA']            = os.getenv('CC_DB_SCHEMA', 'investmitra')
os.environ['CC_POSTGRES_URL']         = os.getenv('CC_POSTGRES_URL', '')
os.environ['CC_TIMESCALE_URL']        = os.getenv('CC_TIMESCALE_URL', '')
os.environ['AWS_ENDPOINT_URL']        = os.getenv('AWS_ENDPOINT_URL', '')
os.environ['AWS_ACCESS_KEY_ID']       = os.getenv('AWS_ACCESS_KEY_ID', '')
os.environ['AWS_SECRET_ACCESS_KEY']   = os.getenv('AWS_SECRET_ACCESS_KEY', '')
os.environ['AWS_REGION']              = os.getenv('AWS_REGION', 'auto')
os.environ['CC_BUCKET_RAW']           = os.getenv('CC_BUCKET_RAW', 'cc-raw')
os.environ['CC_BUCKET_QUARANTINE']    = os.getenv('CC_BUCKET_QUARANTINE', 'cc-quarantine')

print("Starting Prefect worker...")
print("Pool: cc-worker-pool")
print("Connected to: Prefect Cloud")
print("Press Ctrl+C to stop.\n")

subprocess.run(
    "prefect worker start --pool cc-worker-pool",
    shell=True
)

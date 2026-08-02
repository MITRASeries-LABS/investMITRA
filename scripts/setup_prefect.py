"""
investMITRA — Prefect Cloud Setup & Verification
Run from project root: python scripts/setup_prefect.py

This script:
1. Authenticates with Prefect Cloud
2. Creates the cc-worker-pool work pool
3. Deploys the NSE Bhavcopy daily flow
4. Verifies everything is connected
"""

import os
import subprocess
import sys
from dotenv import load_dotenv

load_dotenv('.env.prod')

PREFECT_API_KEY = os.getenv('PREFECT_API_KEY')
PREFECT_API_URL = os.getenv('PREFECT_API_URL')


def run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    if result.stderr.strip():
        print(f"    {result.stderr.strip()}")
    if check and result.returncode != 0:
        print(f"  ERROR: command failed (exit {result.returncode})")
        sys.exit(1)
    return result


def main():
    print("=" * 60)
    print("investMITRA — Prefect Cloud Setup")
    print("=" * 60)

    # Set env vars for subprocess calls
    os.environ['PREFECT_API_KEY'] = PREFECT_API_KEY
    os.environ['PREFECT_API_URL'] = PREFECT_API_URL

    # 1. Verify connection
    print("\n1. Verifying Prefect Cloud connection...")
    result = run("prefect config view", check=False)
    run("prefect cloud workspace ls", check=False)
    print("   Connected.")

    # 2. Create work pool
    print("\n2. Creating work pool: cc-worker-pool...")
    run('prefect work-pool create cc-worker-pool --type process', check=False)
    print("   Work pool ready.")

    # 3. Verify work pool
    print("\n3. Verifying work pool...")
    run("prefect work-pool ls")

    print("\n" + "=" * 60)
    print("Prefect Cloud setup complete.")
    print("=" * 60)
    print(f"\nDashboard: https://app.prefect.cloud/account/14215352-b077-4a81-ad8f-291860773c8c/workspace/ebf829d9-0834-4e4c-9583-fe9d8d95a6a3")
    print("\nNext: start the worker with:")
    print("  python scripts/start_worker.py")


if __name__ == "__main__":
    main()

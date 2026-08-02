"""
investMITRA — Cloudflare R2 Setup & Verification
Run from project root: python scripts/setup_r2.py
"""

import boto3
from botocore.exceptions import ClientError
import os
from dotenv import load_dotenv

load_dotenv('.env.prod')

ENDPOINT  = os.getenv('AWS_ENDPOINT_URL')
KEY_ID    = os.getenv('AWS_ACCESS_KEY_ID')
SECRET    = os.getenv('AWS_SECRET_ACCESS_KEY')
RAW       = os.getenv('CC_BUCKET_RAW', 'cc-raw')
QUARANT   = os.getenv('CC_BUCKET_QUARANTINE', 'cc-quarantine')

def main():
    print("Connecting to Cloudflare R2...")
    s3 = boto3.client(
        's3',
        endpoint_url=ENDPOINT,
        aws_access_key_id=KEY_ID,
        aws_secret_access_key=SECRET,
        region_name='auto',
    )
    print("Connected.")

    # Create buckets
    for bucket in [RAW, QUARANT]:
        try:
            s3.head_bucket(Bucket=bucket)
            print(f"  Bucket exists: {bucket}")
        except ClientError:
            s3.create_bucket(Bucket=bucket)
            print(f"  Created bucket: {bucket}")

    # Test write
    print("\nTesting write...")
    s3.put_object(
        Bucket=RAW,
        Key='prod/health_check/test.txt',
        Body=b'investMITRA R2 connection OK',
    )
    print("  Write OK")

    # Test read
    print("Testing read...")
    obj = s3.get_object(Bucket=RAW, Key='prod/health_check/test.txt')
    content = obj['Body'].read()
    print(f"  Read OK: {content.decode()}")

    # Clean up
    s3.delete_object(Bucket=RAW, Key='prod/health_check/test.txt')
    print("  Cleanup OK")

    # List buckets
    buckets = s3.list_buckets().get('Buckets', [])
    print(f"\nR2 buckets ready:")
    for b in buckets:
        print(f"  - {b['Name']}")

    print("\nCloudflare R2 setup complete.")

if __name__ == "__main__":
    main()

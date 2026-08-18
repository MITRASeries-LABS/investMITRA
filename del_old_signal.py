import boto3, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
s3 = boto3.client('s3', endpoint_url=os.getenv('AWS_ENDPOINT_URL'), aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'), aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'), region_name='auto')
s3.delete_object(Bucket='cc-raw', Key='prod/signals/early_signals/year=2026/month=08/early_signals_20260807.parquet')
print('Deleted old signal file')

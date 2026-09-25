"""Ping externally configured monitoring URLs without exposing their credentials."""
import os
from urllib.parse import urlsplit
from urllib.request import urlopen


def ping(variable, required=False):
    url = os.getenv(variable)
    if not url:
        if required:
            raise RuntimeError(f'{variable} must be configured for independent missed-run monitoring')
        return
    if urlsplit(url).scheme != 'https' or not urlsplit(url).netloc:
        raise ValueError('Heartbeat URL must use HTTPS')
    try:
        with urlopen(url, timeout=15) as response:
            response.read(1024)
    except Exception:
        raise RuntimeError('External heartbeat delivery failed') from None

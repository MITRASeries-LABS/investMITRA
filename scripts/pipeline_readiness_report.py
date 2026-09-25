"""Report pipeline readiness to the existing configured operations channel."""
import json
import os
from urllib.request import Request, urlopen


def main():
    ready = os.getenv('READY') == 'true'
    status = 'EOD DATA READY' if ready else 'EOD DATA NOT READY - attention required'
    day = os.getenv('TRADE_DATE') or 'calendar/date unresolved'
    url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    message = f'investMITRA: {status}\nSession: {day}\n{url}'
    if ready:
        message += '\nData preparation only; Kite login and engine preflight are still required.'
    else:
        message += '\nOvernight retries are bounded; trading remains blocked until data is valid.'
    print(message)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write(message+'\n')
    if not ready or os.getenv('MORNING') == 'true':
        token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
        if not token or not chat:
            raise RuntimeError('Readiness notification not delivered: Telegram secrets missing')
        body = json.dumps({'chat_id': chat, 'text': message}).encode('utf-8')
        request = Request(f'https://api.telegram.org/bot{token}/sendMessage', data=body,
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=20) as response:
                result = json.load(response)
            if not result.get('ok'):
                raise ValueError('Telegram rejected notification')
        except Exception:
            # Do not leak a token-bearing URL through an HTTP error traceback.
            raise RuntimeError('Readiness notification delivery failed; inspect Actions summary') from None
    if not ready:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

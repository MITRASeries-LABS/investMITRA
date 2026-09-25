"""systemd OnFailure handler, including process timeout/crash failures."""
import os
from pipeline_heartbeat import ping
from pipeline_readiness_report import main as report


def main():
    os.environ.update(READY='false', MORNING='true')
    try:
        ping('PIPELINE_HEARTBEAT_FAILURE_URL')
    finally:
        report()


if __name__ == '__main__':
    main()

"""Read-only reports after confirmed square-off and stopped execution worker."""
from datetime import date
import logging

LOG = logging.getLogger(__name__)


def run_session_reports(execution):
    """Independent report failures must not block each other or journal cleanup.

    Use the executor's session date and actual journal/observer paths, never the
    wall-clock date or a hardcoded default. Called once from main's shutdown.
    No broker calls, alerts, uploads, or journal mutations occur here.
    """
    result = {"execution": "skipped", "shadow": "skipped"}
    view = execution.snapshot()
    if not view.get("flat"):
        LOG.warning("Automatic reports deferred: square-off not confirmed")
        return result
    day = view.get("day")
    try:
        date.fromisoformat(day)
    except (ValueError, TypeError):
        LOG.warning("Automatic reports skipped: no valid execution session date")
        return result

    print(f"\nAUTOMATIC SESSION REPORTS — {day}", flush=True)
    try:
        from auto_paper_summary import load_state, summarise
        path = execution.journal.path
        if load_state(path, day) is None:
            raise ValueError("No execution journal data for the session date")
        summarise(path, day)
        result["execution"] = "ok"
    except Exception as exc:
        result["execution"] = "failed"
        LOG.error("Automatic trade summary unavailable (%s); local journal retained. "
                  "Retry scripts/auto_paper_summary.py --date %s with the same --db path.",
                  type(exc).__name__, day)

    observer = getattr(execution, "shadow_observer", None)
    if observer is None:
        LOG.info("Automatic shadow report skipped: observer was not started in this run")
        return result
    if observer.worker.is_alive():
        LOG.warning("Automatic shadow report deferred: observer still draining. "
                    "Run scripts/shadow_validation_report.py --date %s after it stops.", day)
        return result
    if observer.failed:
        LOG.warning("Shadow capture failed during this run; any results below have incomplete coverage")
    try:
        from shadow_validation_report import summarise
        summarise(observer.path, day, day)
        result["shadow"] = "ok"
    except Exception as exc:
        result["shadow"] = "failed"
        LOG.error("Automatic shadow report unavailable (%s); research database retained. "
                  "Retry scripts/shadow_validation_report.py --date %s with the same --db path.",
                  type(exc).__name__, day)
    return result

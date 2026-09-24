"""Compatibility entry point; the maintained engine lives in scripts/."""
from pathlib import Path
import runpy
import sys

scripts = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(scripts))
if __name__ == "__main__":
    runpy.run_path(str(scripts / "intraday_signals.py"), run_name="__main__")
else:
    import importlib
    sys.modules[__name__] = importlib.import_module("scripts.intraday_signals")

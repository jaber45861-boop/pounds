"""Compatibility entry point for runtimes expecting the shorter bot filename."""

from pathlib import Path
import runpy


BOT_FILE = Path(__file__).with_name(
    "bot_(3)_1785867111284_1786343880132_1786345791164_1786347988203.py"
)

runpy.run_path(str(BOT_FILE), run_name="__main__")
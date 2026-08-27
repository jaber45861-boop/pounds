"""Start the Telegram bot and its Flask API in one process."""

import sys
import runpy
from pathlib import Path


def main() -> None:
    base = Path(__file__).parent
    assets = base / "attached_assets"

    # Allow the bot to import modules stored in attached_assets
    if assets.exists():
        sys.path.insert(0, str(assets))

    candidates = []

    for folder in [base, assets]:
        if folder.exists():
            candidates.extend(
                p for p in folder.glob("bot*.py")
                if p.is_file()
            )

    if not candidates:
        raise FileNotFoundError(
            "No bot*.py file was found."
        )

    # Use the largest bot file
    bot_path = max(candidates, key=lambda p: p.stat().st_size)

    print(f"Starting bot file: {bot_path}")
    print(f"Bot file size: {bot_path.stat().st_size} bytes")

    runtime = runpy.run_path(
        str(bot_path),
        run_name="telegram_bot_runtime"
    )

    start_bot = runtime.get("run_bot")

    if not callable(start_bot):
        raise RuntimeError(
            f"The bot file {bot_path.name} does not expose run_bot()."
        )

    start_bot()


if __name__ == "__main__":
    main()
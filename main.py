"""جنيهات — Entry point for the new Telegram bot with fresh database."""

import sys
from pathlib import Path


def main() -> None:
    base = Path(__file__).parent

    # Import and run the ganaihat bot directly
    sys.path.insert(0, str(base))

    from ganaihat_bot import run_bot
    run_bot()


if __name__ == "__main__":
    main()

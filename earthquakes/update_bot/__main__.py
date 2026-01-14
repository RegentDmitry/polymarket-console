"""
Entry point for update bot.

Usage:
    python -m update_bot
    python -m update_bot --interval 12h
"""

import sys
from pathlib import Path

# Add parent directory to path so we can import from earthquakes
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Load environment variables from .env
try:
    from dotenv import load_dotenv
    # Load .env from earthquakes directory (parent of update_bot)
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not installed, rely on system environment

from .config import parse_args
from .ui.app import run_update_bot


def main():
    """Main entry point."""
    config = parse_args()
    run_update_bot(config)


if __name__ == "__main__":
    main()

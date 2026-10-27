"""Entry point: python main.py --incident INC0010001"""

import sys
from pathlib import Path

# Allow running from a fresh clone without `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from support_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

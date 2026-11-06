"""Set one secret in .env without echoing it or putting it in shell history.

Usage: python3 scripts/set_secret.py VOYAGE_API_KEY
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"[A-Z][A-Z0-9_]*", sys.argv[1]):
        print("Usage: python3 scripts/set_secret.py NAME")
        return 2
    name = sys.argv[1]
    value = getpass.getpass(f"Paste {name} (hidden): ").strip()
    if not value:
        print("Nothing entered; .env unchanged.")
        return 1
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    line = f"{name}={value}"
    pattern = re.compile(rf"^{name}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(lambda _: line, text)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    ENV_PATH.write_text(text)
    print(f"{name} saved to .env ({len(value)} characters).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

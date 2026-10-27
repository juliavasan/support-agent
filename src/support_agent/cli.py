"""Terminal interface: python main.py --incident INC0010001"""

from __future__ import annotations

import argparse
import json
import sys

from support_agent.graph.workflow import build_graph


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrieve a ServiceNow incident (read-only).")
    parser.add_argument(
        "--incident", required=True, help="Incident number (INC0010001) or 32-character sys_id"
    )
    parser.add_argument("--json", action="store_true", help="Print the final graph state as JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, graph=None) -> int:  # type: ignore[no-untyped-def]
    args = parse_args(argv)
    graph = graph or build_graph()
    result = graph.invoke({"incident_id": args.incident})

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["display"])
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())

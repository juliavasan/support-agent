"""Read-only ServiceNow Table API connectivity test.

Fetches 5 incidents with HTTP Basic Auth, prints a summary table, and saves the
response to data/servicenow_incidents_sample.json. Never creates, modifies, or
deletes records, and never prints credentials.

Config (from .env or the environment):
  SERVICENOW_INSTANCE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD
If SERVICENOW_PASSWORD is empty, you are prompted for it (input hidden).

Usage: python3 scripts/test_connection.py
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
OUTPUT_PATH = ROOT / "data" / "servicenow_incidents_sample.json"

PARAMS = {
    "sysparm_limit": "5",
    "sysparm_fields": (
        "number,short_description,description,state,priority,category,close_notes,sys_id"
    ),
    "sysparm_display_value": "true",
}


def load_env(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding values already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            if value.strip() and not os.environ.get(key.strip()):
                os.environ[key.strip()] = value.strip()


def sanitized_error(response: requests.Response) -> str:
    """Return ServiceNow's error message only, never request headers or credentials."""
    try:
        error = response.json().get("error", {})
        return f"{error.get('message', '')} - {error.get('detail', '')}".strip(" -")
    except ValueError:
        return response.text[:200]


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def main() -> int:
    load_env(ENV_PATH)
    base_url = os.environ.get("SERVICENOW_INSTANCE_URL", "").rstrip("/")
    username = os.environ.get("SERVICENOW_USERNAME", "")
    if not base_url or not username:
        print("Set SERVICENOW_INSTANCE_URL and SERVICENOW_USERNAME in .env")
        return 1
    password = os.environ.get("SERVICENOW_PASSWORD") or getpass.getpass(
        f"ServiceNow password for {username}: "
    )

    url = f"{base_url}/api/now/table/incident"
    print(f"GET {url}  (user: {username}, auth: Basic)")
    try:
        response = requests.get(
            url,
            params=PARAMS,
            auth=(username, password),
            headers={"Accept": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"Request failed before reaching ServiceNow: {type(exc).__name__}")
        return 1

    print(f"HTTP {response.status_code}")

    if response.status_code == 401:
        print(f"Authentication failed: {sanitized_error(response)}")
        print(
            "ServiceNow rejected the credentials for API access. Check, without changing\n"
            "security settings automatically:\n"
            "  - the password works for this user at /login.do\n"
            "  - the user is active, not locked out, and has no pending password reset\n"
            "  - no REST API Access Policy or auth profile blocks Basic Auth\n"
            "  - the instance is awake (developer portal -> Manage instance)\n"
            "See docs/SERVICENOW.md -> Troubleshooting."
        )
        return 2
    if response.status_code == 403:
        print(f"Permission denied: {sanitized_error(response)}")
        print("The user authenticated but lacks read access to incident (e.g. the itil role).")
        return 3
    if response.status_code != 200:
        print(f"Unexpected response: {sanitized_error(response)}")
        return 4

    incidents = response.json().get("result", [])
    total = response.headers.get("X-Total-Count", "?")
    print(f"Retrieved {len(incidents)} incident(s) (X-Total-Count: {total})\n")

    print(f"| {'Number':<11} | {'Short Description':<45} | {'State':<12} | {'Priority':<12} |")
    print(f"|{'-' * 13}|{'-' * 47}|{'-' * 14}|{'-' * 14}|")
    for inc in incidents:
        print(
            f"| {inc.get('number', ''):<11} "
            f"| {truncate(inc.get('short_description', ''), 45):<45} "
            f"| {truncate(inc.get('state', ''), 12):<12} "
            f"| {truncate(inc.get('priority', ''), 12):<12} |"
        )

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(response.json(), indent=2) + "\n")
    print(f"\nSaved response to {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

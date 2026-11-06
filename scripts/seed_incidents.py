"""Seed synthetic companies, callers, and incidents into ServiceNow, then read them back.

Companies come from data/seed/companies.json, callers from data/seed/callers.json,
incidents from data/seed/incidents.json. The customer is the caller's company: the agent
reads it via caller_id.company, because the itil role can't set incident.company here.
Writes run as the `seed_loader` account (never the agent). The read-back runs as
`support_agent` to prove the agent can see its queue.

Idempotent: companies are matched by name, callers by user_name, incidents by
correlation_id (= seed_id), so re-running never creates duplicates. Existing callers
get their company set if it is missing or different.

Usage: python3 scripts/seed_incidents.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
SEED_DIR = ROOT / "data" / "seed"

# Seed-file fields that are not sent to ServiceNow.
LOCAL_ONLY_FIELDS = {"seed_id", "source", "expected_type", "caller"}


def load_env(path: Path) -> None:
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            if value.strip() and not os.environ.get(key.strip()):
                os.environ[key.strip()] = value.strip()


class TableClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/json"})

    def _url(self, table: str) -> str:
        return f"{self.base_url}/api/now/table/{table}"

    def query(self, table: str, query: str, fields: str, limit: int = 100) -> list[dict[str, Any]]:
        resp = self.session.get(
            self._url(table),
            params={
                "sysparm_query": query,
                "sysparm_fields": fields,
                "sysparm_limit": str(limit),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["result"]

    def create(self, table: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(self._url(table), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()["result"]

    def update(self, table: str, sys_id: str, body: dict[str, Any]) -> None:
        resp = self.session.patch(
            f"{self._url(table)}/{sys_id}",
            json=body,
            params={"sysparm_fields": "sys_id"},
            timeout=30,
        )
        resp.raise_for_status()


def sys_id_of(client: TableClient, table: str, query: str) -> str | None:
    rows = client.query(table, query, "sys_id", limit=1)
    return rows[0]["sys_id"] if rows else None


def main() -> int:
    load_env(ENV_PATH)
    env = os.environ
    base_url = env["SERVICENOW_INSTANCE_URL"]
    group_name = env["SERVICENOW_ASSIGNMENT_GROUP"]
    loader = TableClient(base_url, env["SERVICENOW_SEED_USERNAME"], env["SERVICENOW_SEED_PASSWORD"])
    agent = TableClient(base_url, env["SERVICENOW_USERNAME"], env["SERVICENOW_PASSWORD"])

    companies = json.loads((SEED_DIR / "companies.json").read_text())["companies"]
    callers = json.loads((SEED_DIR / "callers.json").read_text())["callers"]
    incidents = json.loads((SEED_DIR / "incidents.json").read_text())["incidents"]

    print(f"Seeding {base_url} as {env['SERVICENOW_SEED_USERNAME']}")

    company_ids: dict[str, str] = {}
    for company in companies:
        existing = sys_id_of(loader, "core_company", f"name={company['name']}")
        if existing:
            company_ids[company["name"]] = existing
            print(f"  = company {company['name']}")
        else:
            try:
                created = loader.create("core_company", company)
            except requests.HTTPError as exc:
                print(f"  ! could not create company {company['name']}: {exc}")
                print("    Create it as admin (Organization > Companies) and re-run.")
                return 1
            company_ids[company["name"]] = created["sys_id"]
            print(f"  + company {company['name']}")

    caller_ids: dict[str, str] = {}
    for caller in callers:
        company_id = company_ids[caller["company"]]
        body = {**caller, "company": company_id}
        rows = loader.query(
            "sys_user", f"user_name={caller['user_name']}", "sys_id,company", limit=1
        )
        if rows:
            caller_ids[caller["user_name"]] = rows[0]["sys_id"]
            if rows[0]["company"] != caller["company"]:
                loader.update("sys_user", rows[0]["sys_id"], {"company": company_id})
                print(f"  ~ caller {caller['user_name']} -> {caller['company']}")
            else:
                print(f"  = caller {caller['user_name']}")
        else:
            created = loader.create("sys_user", {**body, "active": "true"})
            caller_ids[caller["user_name"]] = created["sys_id"]
            print(f"  + caller {caller['user_name']}")

    group_id = sys_id_of(loader, "sys_user_group", f"name={group_name}")
    if group_id is None:
        print(f"Assignment group '{group_name}' not found. Run scripts/servicenow_setup.py")
        return 1

    for inc in incidents:
        existing = loader.query("incident", f"correlation_id={inc['seed_id']}", "number", limit=1)
        if existing:
            print(f"  = {existing[0]['number']}  {inc['seed_id']}")
            continue
        body = {k: v for k, v in inc.items() if k not in LOCAL_ONLY_FIELDS}
        body.update(
            {
                "caller_id": caller_ids[inc["caller"]],
                "assignment_group": group_id,
                "contact_type": "email",
                "correlation_id": inc["seed_id"],
                "correlation_display": "support-agent seed",
                "impact": str(inc["impact"]),
                "urgency": str(inc["urgency"]),
            }
        )
        created = loader.create("incident", body)
        print(f"  + {created['number']}  {inc['seed_id']}")

    seed_query = (
        f"assignment_group.name={group_name}^correlation_idSTARTSWITHerpnext-gh-^ORDERBYnumber"
    )
    print(f"\nRead back as {env['SERVICENOW_USERNAME']}:")
    rows = agent.query(
        "incident",
        seed_query,
        "number,caller_id,caller_id.company,short_description,state,priority,category,correlation_id",
    )
    print(
        f"| {'Number':<11} | {'Caller':<14} | {'Company':<26} | {'Priority':<12} "
        f"| {'State':<8} | Short description"
    )
    print(f"|{'-' * 13}|{'-' * 16}|{'-' * 28}|{'-' * 14}|{'-' * 10}|{'-' * 50}")
    for r in rows:
        print(
            f"| {r['number']:<11} | {r['caller_id'][:14]:<14} | {r['caller_id.company'][:26]:<26} "
            f"| {r['priority'][:12]:<12} | {r['state'][:8]:<8} | {r['short_description'][:60]}"
        )
    print(f"\n{len(rows)} seeded incident(s) visible to the agent.")

    ui_query = urllib.parse.quote(seed_query, safe="=^")
    print(f"View in UI (log in as admin):\n{base_url}/incident_list.do?sysparm_query={ui_query}")
    return 0 if len(rows) == len(incidents) else 2


if __name__ == "__main__":
    sys.exit(main())

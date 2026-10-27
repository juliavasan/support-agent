"""Idempotent ServiceNow PDI setup for support-agent.

Creates the "AI Support Agent" assignment group, a least-privilege
`support_agent` API user, and a "Customer Portal Support" knowledge base that
only that group can read, then writes the user's credentials into .env.

Reads SERVICENOW_INSTANCE_URL from .env and SN_ADMIN_PW from .env.admin.
Secrets are never printed. Safe to re-run (e.g. after a PDI is reclaimed).

Usage: python3 scripts/servicenow_setup.py
"""

from __future__ import annotations

import base64
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ADMIN_ENV_PATH = ROOT / ".env.admin"

GROUP_NAME = "AI Support Agent"
USER_NAME = "support_agent"
# Roles are granted to groups, not users, so access is managed in one place.
AGENT_ACCOUNT: dict[str, object] = {
    "user_name": USER_NAME,
    "first_name": "Support",
    "last_name": "Agent",
    "group": GROUP_NAME,
    "group_description": "Queue worked by the support-agent AI.",
    # itil: read/write incidents and journal entries (work notes, comments).
    "roles": ["itil"],
}
# Separate account for loading synthetic data, so seeded tickets are never authored by the agent.
SEED_ACCOUNT: dict[str, object] = {
    "user_name": "seed_loader",
    "first_name": "Seed",
    "last_name": "Loader",
    "group": "Seed Data Loaders",
    "group_description": "Loads synthetic demo data (callers, incidents, KB). Not used by the agent.",
    # itil: create incidents. user_admin: create synthetic caller users.
    "roles": ["itil", "user_admin"],
}
KB_TITLE = "Customer Portal Support"
CRITERIA_NAME = "AI Support Agent - KB read"
ACCESS_CHECK_ARTICLE = "support-agent KB access check"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    lines.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(lines) + "\n")


class ServiceNow:
    def __init__(self, base_url: str, user: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def request(
        self, method: str, table: str, params: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> list[dict[str, str]] | dict[str, str]:
        url = f"{self.base_url}/api/now/table/{table}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https base URL)
            return json.load(resp)["result"]

    def find_one(self, table: str, query: str) -> dict[str, str] | None:
        rows = self.request("GET", table, {"sysparm_query": query, "sysparm_limit": "1"})
        assert isinstance(rows, list)
        return rows[0] if rows else None

    def ensure(self, table: str, query: str, body: dict[str, object], label: str) -> str:
        existing = self.find_one(table, query)
        if existing:
            print(f"  = {label} (exists)")
            return existing["sys_id"]
        created = self.request("POST", table, body=body)
        assert isinstance(created, dict)
        print(f"  + {label} (created)")
        return created["sys_id"]


def generate_password() -> str:
    # Random core plus guaranteed character classes to satisfy PDI password policy.
    return secrets.token_urlsafe(24) + "Aa1!"


def ensure_service_account(
    admin: ServiceNow, spec: dict[str, object], stored_password: str | None
) -> tuple[str, str]:
    """Ensure an API-only user, its group, and the group's roles. Returns (group_id, password)."""
    group_name = str(spec["group"])
    user_name = str(spec["user_name"])
    group_id = admin.ensure(
        "sys_user_group", f"name={group_name}",
        {"name": group_name, "description": spec["group_description"], "active": "true"},
        f"group '{group_name}'",
    )
    for role_name in spec["roles"]:  # type: ignore[attr-defined]
        role = admin.find_one("sys_user_role", f"name={role_name}")
        if role is None:
            raise SystemExit(f"Role '{role_name}' not found on this instance")
        admin.ensure(
            "sys_group_has_role", f"group={group_id}^role={role['sys_id']}",
            {"group": group_id, "role": role["sys_id"], "inherits": "true"},
            f"role '{role_name}' on '{group_name}'",
        )

    # Reuse the stored password if there is one; otherwise generate a new one.
    password = stored_password or generate_password()
    user_fields: dict[str, object] = {
        "user_name": user_name,
        "first_name": spec["first_name"],
        "last_name": spec["last_name"],
        "active": "true",
        # API-only account: cannot log in to the UI, and exempt from the Basic Auth restriction.
        "web_service_access_only": "true",
        "password_needs_reset": "false",
        "user_password": password,
    }
    # sysparm_input_display_value makes ServiceNow hash user_password instead of storing it raw.
    user = admin.find_one("sys_user", f"user_name={user_name}")
    if user is None:
        created = admin.request(
            "POST", "sys_user", {"sysparm_input_display_value": "true"}, user_fields
        )
        assert isinstance(created, dict)
        user_id = created["sys_id"]
        print(f"  + user '{user_name}' (created)")
    else:
        user_id = user["sys_id"]
        admin.request(
            "PATCH", f"sys_user/{user_id}", {"sysparm_input_display_value": "true"}, user_fields
        )
        print(f"  = user '{user_name}' (exists, settings and password re-applied)")

    admin.ensure(
        "sys_user_grmember", f"group={group_id}^user={user_id}",
        {"group": group_id, "user": user_id},
        f"'{user_name}' member of '{group_name}'",
    )
    return group_id, password


def main() -> int:
    env = read_env(ENV_PATH)
    base_url = env.get("SERVICENOW_INSTANCE_URL", "")
    admin_pw = read_env(ADMIN_ENV_PATH).get("SN_ADMIN_PW", "")
    if not base_url or not admin_pw:
        print("Missing SERVICENOW_INSTANCE_URL in .env or SN_ADMIN_PW in .env.admin")
        return 1

    admin = ServiceNow(base_url, "admin", admin_pw)
    print(f"Configuring {base_url}")

    try:
        admin.find_one("sys_user", "user_name=admin")
    except urllib.error.HTTPError as err:
        print(f"Admin login failed (HTTP {err.code}). Check the password in .env.admin.")
        return 1

    group_id, password = ensure_service_account(
        admin, AGENT_ACCOUNT, env.get("SERVICENOW_PASSWORD")
    )
    _, seed_password = ensure_service_account(
        admin, SEED_ACCOUNT, env.get("SERVICENOW_SEED_PASSWORD")
    )

    # A dedicated knowledge base readable only by the agent's group, instead of a broad
    # knowledge role or access to the instance's demo KBs.
    criteria_id = admin.ensure(
        "user_criteria", f"name={CRITERIA_NAME}",
        {"name": CRITERIA_NAME, "group": group_id, "active": "true"},
        f"user criteria '{CRITERIA_NAME}'",
    )
    admin_user = admin.find_one("sys_user", "user_name=admin")
    assert admin_user is not None
    kb_id = admin.ensure(
        "kb_knowledge_base", f"title={KB_TITLE}",
        {
            "title": KB_TITLE,
            "description": "Synthetic KB for the support-agent demo customer portal.",
            "owner": admin_user["sys_id"],
            "active": "true",
        },
        f"knowledge base '{KB_TITLE}'",
    )
    admin.ensure(
        "kb_uc_can_read_mtom", f"kb_knowledge_base={kb_id}^user_criteria={criteria_id}",
        {"kb_knowledge_base": kb_id, "user_criteria": criteria_id},
        "group can read knowledge base",
    )
    admin.ensure(
        "kb_knowledge", f"kb_knowledge_base={kb_id}^short_description={ACCESS_CHECK_ARTICLE}",
        {
            "kb_knowledge_base": kb_id,
            "short_description": ACCESS_CHECK_ARTICLE,
            "text": "<p>Placeholder used by scripts/servicenow_setup.py to verify KB read "
                    "access. Safe to keep.</p>",
            "workflow_state": "published",
        },
        "access-check article",
    )

    set_env_values(ENV_PATH, {
        "SERVICENOW_USERNAME": USER_NAME,
        "SERVICENOW_PASSWORD": password,
        "SERVICENOW_ASSIGNMENT_GROUP": GROUP_NAME,
        "SERVICENOW_KNOWLEDGE_BASE": KB_TITLE,
        "SERVICENOW_SEED_USERNAME": str(SEED_ACCOUNT["user_name"]),
        "SERVICENOW_SEED_PASSWORD": seed_password,
    })
    print("  .env updated with service account credentials (not printed)")

    print("Verifying as support_agent")
    agent = ServiceNow(base_url, USER_NAME, password)
    checks = [
        ("read incident", "incident", "", True),
        (f"read KB '{KB_TITLE}'", "kb_knowledge", f"kb_knowledge_base={kb_id}", True),
        ("read sys_properties (should be denied)", "sys_properties", "", False),
    ]
    ok = True
    for label, table, query, should_succeed in checks:
        try:
            rows = agent.request("GET", table, {"sysparm_limit": "1", "sysparm_query": query})
            allowed = bool(rows)
        except urllib.error.HTTPError as err:
            allowed = False if err.code in (401, 403) else None
        passed = allowed == should_succeed
        ok &= passed
        print(f"  {'PASS' if passed else 'FAIL'} {label}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

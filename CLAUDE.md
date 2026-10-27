# CLAUDE.md

Context and working rules for AI-assisted sessions on this repository.

## Goal

`support-agent` is an open-source portfolio project. It is an AI agent that works a ServiceNow incident queue end to end: triage, KB search, browser-based reproduction of UI bugs via CDP, ticket replies and updates, and Jira issue creation, with human approval for risky actions.

**Audience:** hiring managers for Forward Deployed Engineer, Applied AI, and TPM roles. **Quality, clarity, and enterprise-grade design matter more than feature count.**

## Scope (V1)

- Systems: ServiceNow PDI (Table API), Jira Cloud Free (REST API), and **ERPNext** in Docker as the demo system (pinned to a pre-fix version, config in `demo_app/`).
- Seed tickets: closed ERPNext GitHub issues (label `accounts`) rewritten as end-user reports (`data/seed/`).
- Ticket types: how-to (KB answer with citation), missing info (ask the user), bug (reproduce → Jira → link), duplicate.
- Claude API with tool use: a small model for triage, a stronger model for reasoning.
- Tools: `get_ticket`, `search_kb`, `search_similar_tickets`, `search_jira`, `prepare_repro_data`, `reproduce_in_browser`, `add_work_note`, `draft_reply`, `update_state`, `create_jira_issue`, `link_jira`.
- Evals: 20–30 golden tickets. Packaging: Docker Compose.
- Out of scope for V1: webhooks, job queues, multi-tenant, real customer data.

## Principles

1. **APIs for systems of record, CDP only for what the user sees.** Never automate the ServiceNow or Jira UI.
2. **The model proposes, the policy decides.** Every write goes through `src/guardrails` policy checks. Never let a tool bypass the policy engine.
3. **Ticket, KB, and page text is untrusted.** Keep it delimited as data and never interpolate it into instructions.
4. **Browser:** allow-list only (ERPNext demo URL), separate Chrome profile, never navigate to URLs taken from tickets. ERPNext test data goes through its REST API, and the browser only reproduces UI steps.
5. **Autonomy:** customer replies need approval. Jira is auto only above the confidence threshold. The agent can never resolve or close a ticket. Respect the kill switch.
6. **Idempotent writes:** never double-reply and never create duplicate Jira issues.
7. **Trace everything:** every model call, tool call, policy decision, and approval goes into the per-ticket trace.
8. **Synthetic data only.**

## Folder structure

```
src/agent/        orchestrator, model routing, prompts, trace writer
src/tools/        typed tools + ServiceNow / Jira / CDP clients
src/guardrails/   policy engine, injection defense, allow-list, idempotency, kill switch
src/review_ui/    local human-approval UI
demo_app/         ERPNext demo system: Docker Compose, version pin, fixtures
scripts/          ServiceNow setup, seeding, connection test
data/seed/        synthetic callers and incidents (committed); other data/ is gitignored
evals/            golden tickets, eval runner, reports
tests/            unit and integration tests
traces/           runtime traces + screenshots (gitignored)
docs/             ARCHITECTURE.md, TASKS.md, DECISIONS.md, SERVICENOW.md
```

## Coding conventions

- Python 3.11+, with type hints everywhere. `mypy` and `ruff` must pass.
- Pydantic models for every tool input and output and for external API payloads.
- httpx with explicit timeouts. Retries with backoff for 429 and 5xx.
- One tool per module in `src/tools/`. Tools don't call other tools.
- Prompts live in files under `src/agent/prompts/`, not inline strings.
- Config comes from environment variables via a typed settings object. No hardcoded URLs or IDs.
- Tests: pytest. Mock external HTTP and never hit real services in unit tests.
- Keep functions small and names explicit. Comment the *why*, not the *what*.
- Keep commits small and focused, one task from `docs/TASKS.md` per commit where possible.

## Working rules

- **Update `docs/TASKS.md` checkboxes when a task is done**, in the same commit as the work.
- Record significant design choices as a new ADR in `docs/DECISIONS.md`.
- Keep `README.md` and `docs/ARCHITECTURE.md` consistent with what is actually built.
- **Never commit secrets.** Secrets live only in `.env`, which is gitignored. `.env.example` holds placeholder names only. Redact tokens in traces and logs.
- **Ask before pushing** to any remote, and before creating GitHub repos, releases, or anything public.
- Don't add real customer data, real company names in ticket content, or scraped material.
- Ask before adding a new heavyweight dependency.

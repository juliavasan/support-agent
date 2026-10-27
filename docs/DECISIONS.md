# Decision Log

Lightweight ADRs. Each entry records the context, the decision, and its consequences. Add new entries at the bottom and never rewrite accepted ones. Supersede them with a new entry instead.

---

## ADR-001: APIs for systems of record, CDP only for UI reproduction

- **Date:** 2026-10-26
- **Status:** Accepted

**Context.** The agent must read and write ServiceNow incidents and Jira issues, and it must also confirm UI bugs that users report. Browser automation could do both, but enterprise systems have stable, permissioned REST APIs.

**Decision.** All ServiceNow and Jira interactions use their REST APIs (ServiceNow Table API, Jira Cloud REST v3). The browser, driven via the Chrome DevTools Protocol, is used only to reproduce what the user sees in the demo customer portal.

**Consequences.**
- Writes are auditable, fast, and resilient to UI changes.
- The browser's blast radius is limited to one allow-listed app, which makes sandboxing straightforward.
- CDP gives low-level evidence capture (console, network, screenshots) and request interception for allow-list enforcement.
- Raw CDP means more code than a high-level framework. A Playwright-over-CDP adapter stays an option.

## ADR-002: Human-in-the-loop autonomy policy

- **Date:** 2026-10-26
- **Status:** Accepted

**Context.** An agent acting on customer tickets can cause visible harm: wrong answers, spam replies, closed tickets, noisy Jira issues. Hiring audiences and enterprise buyers expect explicit control.

**Decision.** A deterministic policy layer, outside the model, governs every action:
- Automatic: classify, search, reproduce in browser, internal work notes.
- Human approval: any customer-visible reply.
- Conditional: Jira creation is automatic above a confidence threshold with a successful repro, otherwise it needs approval.
- Forbidden for the agent: resolving or closing tickets.
- A kill switch disables all writes instantly.

**Consequences.**
- Safety doesn't depend on the model obeying instructions, which defends against prompt injection.
- Reviewers carry some load. The confidence threshold is tuned with evals to balance load and risk.
- The approval queue needs state and a UI. In V1 that's a local review UI, and in production it moves to ServiceNow approvals.

## ADR-003: Synthetic data only

- **Date:** 2026-10-26
- **Status:** Accepted

**Context.** This is a public portfolio repository. Real tickets contain PII and confidential information.

**Decision.** All tickets, KB articles, users, and demo app content are synthetic and generated or written for this project. Integrations target a personal ServiceNow developer instance and a free Jira Cloud site.

**Consequences.**
- No compliance risk, and the repo is safe to publish.
- Golden eval tickets can be designed to cover edge cases on purpose, including injection attempts.
- Realism depends on the quality of the synthetic data. Tickets are written to mimic real L1 phrasing, including vague and noisy reports.

## ADR-004: Python 3.11

- **Date:** 2026-10-26
- **Status:** Accepted

**Context.** We need a language with first-class Anthropic SDK support, good HTTP and async libraries, CDP clients, and strong typing tools.

**Decision.** Python 3.11+, with Pydantic for schemas, httpx for HTTP, pytest for tests, ruff for lint and format, and mypy for type checking.

**Consequences.**
- Fast iteration and a familiar stack for AI and FDE reviewers.
- Typed tool schemas map directly to Claude tool definitions.
- Performance is not a concern at V1 scale, since the work is I/O bound.

## ADR-005: Docker Compose for packaging

- **Date:** 2026-10-26
- **Status:** Accepted

**Context.** Reviewers should be able to run the project with one command. The system has several moving parts: the agent, review UI, browser, and demo app.

**Decision.** Ship a `docker-compose.yml` that runs every component, plus an offline profile with mocked ServiceNow and Jira so the demo works without accounts. Services are stateless where possible, so the same images can later move to Cloud Run, ECS, or Kubernetes.

**Consequences.**
- One-command setup, with reproducible environments.
- The browser runs in its own container, which reinforces isolation.
- Compose isn't a production orchestrator. The migration path is documented in `ARCHITECTURE.md` §9.

## ADR-006: ServiceNow access via a WSAO service account, OAuth later

- **Date:** 2026-10-27
- **Status:** Accepted

**Context.** New ServiceNow PDIs enforce the Basic authentication restriction. API calls with username and password return `401` unless the account is Web Services Access Only (WSAO), holds the `snc_basic_auth_api_access` exception role, or uses MFA. The agent also needs KB read access, which ServiceNow controls per knowledge base through user criteria rather than roles.

**Decision.**
- The agent uses a dedicated `support_agent` account with **WSAO** enabled: API only, no UI login. It gets its access only through the **AI Support Agent** group (`itil`).
- KB access is scoped to one dedicated knowledge base, **Customer Portal Support**, through user criteria for that group. The agent cannot read the instance's other KBs.
- `admin` gets `snc_basic_auth_api_access` only temporarily, to run the idempotent setup script. The agent never uses `admin`.
- Production target: **OAuth 2.0 client credentials** (short-lived tokens, no stored password).

**Consequences.**
- No standing security exception for the agent. Least privilege is verified by the setup script (incident ✅, own KB ✅, `sys_properties` denied ✅).
- Basic Auth still means a long-lived password in `.env`. That's acceptable for a synthetic-data demo, and the OAuth migration is on the roadmap.
- The whole configuration can be rebuilt with one command after a PDI reclaim.

## ADR-007: ERPNext as the demo system, seeded from real closed issues

- **Date:** 2026-10-27
- **Status:** Accepted. Supersedes the custom "customer portal" demo app in earlier docs.

**Context.** The agent needs a business application whose bugs it can reproduce in a browser. A small custom app with planted bugs is easy to build but unconvincing. Hiring audiences care how the agent handles a real enterprise application.

**Decision.** Use **ERPNext** (open-source ERP, Frappe framework) in Docker as the demo system. Seed tickets are closed ERPNext GitHub issues (label `accounts`), rewritten as end-user reports from fictional callers. Pin ERPNext to a version that predates the fixes. Prepare test data through ERPNext's REST API and reproduce UI steps through CDP.

**Consequences.**
- Realistic tickets with real, verifiable ground truth: each upstream issue documents its cause and fix.
- Heavier demo stack (ERPNext needs several containers), and the version pin must be chosen carefully. Bugs that don't reproduce on the pinned version become `not_reproducible_in_demo`, which is still a valid eval path.
- Accounting bugs often need specific setups (currencies, tax templates), which is why the API fixture layer exists.

## ADR-008: Retrieval with SQLite FTS5 + LLM rerank, synced as the agent identity

- **Date:** 2026-10-27
- **Status:** Accepted

**Context.** The agent must answer how-to tickets from the KB with citations, find similar or duplicate tickets, and avoid duplicate Jira issues. ServiceNow's Table API text search is weak for ranking. A vector database adds a service and an embeddings vendor.

**Decision.**
- Sync published KB articles and incidents from ServiceNow **as `support_agent`** into a local **SQLite FTS5** index, incrementally by `sys_updated_on`.
- Retrieve the top 20 by BM25, **rerank with the small model**, and apply a relevance threshold. Below it, "no match" means escalate rather than improvise.
- Customer replies must cite a KB article from the retrieved set. Past incidents are hints and are never quoted to customers.
- Jira dedupe uses live JQL search, not the index.

**Consequences.**
- No extra infrastructure, and the index is easy to inspect and test. BM25 is strong on ERP vocabulary (doctype names, exact error strings).
- Syncing as the agent means ServiceNow ACLs and KB user criteria apply automatically.
- Weaker on paraphrased queries. Embeddings (e.g. `sqlite-vec`) are a drop-in upgrade behind the same tool interface if retrieval evals call for it.

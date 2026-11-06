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
- **Status:** Superseded by ADR-013 (2026-11-05). SQLite FTS5 remains as the offline, keyword-only backend.

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

## ADR-009: LangGraph for agent orchestration

- **Date:** 2026-10-27
- **Status:** Accepted. Replaces the hand-written tool-use loop in the original design.

**Context.** The agent needs explicit branching by ticket type, typed state, pauses for human approval that can last hours, resumable runs, and a way to show its behavior visually to reviewers.

**Decision.** Build the orchestrator as a **LangGraph** `StateGraph`. Nodes are plain Python functions with injected clients. LLM calls (Claude) happen inside nodes we control. Guardrails remain deterministic code outside the model. Local development uses `langgraph dev` + LangSmith Studio. LangSmith tracing is optional and off by default.

**Consequences.**
- Checkpointing and `interrupt()` give human-in-the-loop and durable execution without custom infrastructure.
- Studio makes the workflow visible, which helps with debugging and demos.
- It adds a framework dependency and its abstractions. The client and tool layers stay framework-free, so they're reusable outside LangGraph.
- Optional LangSmith tracing sends run data to a third party. It stays off by default, with synthetic data only.

## ADR-010: Single `support_agent` package (src layout)

- **Date:** 2026-10-27
- **Status:** Accepted

**Context.** The initial skeleton had top-level packages `src/agent`, `src/tools`, `src/guardrails`, `src/review_ui`. Generic top-level names can clash with other installed distributions.

**Decision.** Use one importable package, `src/support_agent/`, with subpackages `servicenow`, `graph`, `tools`, `guardrails`, `review_ui`, plus `config.py` and `cli.py`.

**Consequences.** Unambiguous imports (`from support_agent.servicenow import ...`), standard `pip install -e .`, and the dev server installs the same package through `langgraph.json`.

## ADR-011: LLM triage writes a preliminary internal work note; priority from a matrix

- **Date:** 2026-10-27
- **Status:** Accepted

**Context.** Phase 2 Step 1 is the first LLM call and the first write to ServiceNow. The assessment must be useful to engineers, impossible to mistake for a decision, and safe to test against a real instance.

**Decision.**
1. **Structured output.** Claude returns a Pydantic-validated `TriageAnalysis` (summary, impact, urgency, category from a fixed list, hypotheses, troubleshooting steps, missing information) via `messages.parse`. Default model `claude-opus-5-5` at `medium` effort with server-side refusal fallback. Both are configurable (`TRIAGE_MODEL`, `TRIAGE_EFFORT`).
2. **Priority is computed, not generated.** The model has no priority field. P1–P5 comes from a configurable impact × urgency matrix (ServiceNow's out-of-box lookup by default) and is only recommended. Incident fields are never changed.
3. **One write path, one field.** `ServiceNowClient.add_work_note` builds the body `{"work_notes": ...}` itself. Customer-visible comments and all other fields are out of reach.
4. **Dry run by default.** Writes need explicit write mode, an incident listed in `WRITE_TEST_INCIDENTS`, the kill switch off, and no existing triage note (journal marker). Each write is verified by reading the journal back and comparing protected fields.

**Consequences.**
- The model's judgment is visible and reviewable, while the deterministic parts (priority, what may be written, when) stay in code that tests cover.
- One triage note per incident. Re-assessing a changed incident needs a later rule (for example, a new note when the description changes).
- `WRITE_TEST_INCIDENTS` is a development-time gate. When the poller arrives, it becomes a policy on the agent's own assignment group plus the autonomy table (ADR-002).
- The code default favors quality (Opus 5.5). Development runs use `TRIAGE_MODEL=claude-haiku-5-5`, about 40× cheaper with a comparable assessment on the first live test. Phase 6 evals will decide the production default.
- Server-side refusal fallback is sent only to models that support it. Haiku 5.5 refusals surface as an analysis error.

## ADR-012: Premium customers get a higher recommended priority, by a deterministic rule

- **Date:** 2026-10-27
- **Status:** Accepted

**Context.** Enterprise support desks prioritize by contract as well as by impact and urgency: a premium customer's P4 is often handled like a P3. That rule is a business policy. It must be predictable, auditable, and impossible to talk the system into.

**Decision.**
1. **Separate node after the LLM.** `apply_customer_policy` runs between `analyze_incident` and `write_work_note`. The model estimates impact and urgency; the matrix gives the base priority; the tier rule adjusts it. Studio shows each step.
2. **Customer = the incident's Company**, falling back to the caller's company (`caller_id.company`) when the incident field is empty. Both come from ServiceNow records, never from ticket text, so "I'm a premium customer" in a description changes nothing.
3. **Tiers in a committed config file** (`analysis/customer_tiers.json`, override with `CUSTOMER_TIERS_PATH`). Premium: one level higher, never lower than P3, never above P1. Unknown or missing company: the default tier (standard, no change).
4. **Matched by company name, case-insensitively, whole name only.** Not by sys_id, because sys_ids differ per instance and this config must survive a PDI rebuild.
5. **The model never sees the tier**, so its impact and urgency estimate stays based on the ticket alone.
6. **Fail closed.** An unreadable or invalid tier file stops the run before the write, rather than treating a premium customer as standard.
7. **Still only a recommendation.** The note shows the base and adjusted priority and the reason. The priority field is never written, so the write surface (ADR-011) is unchanged.

**Consequences.**
- Every adjustment is recorded in `assessment.customer_adjustment` (customer, tier, base, final, rule text) and summarized in the work note.
- Renaming a company in ServiceNow without updating the config drops it to standard. Production would key on a stable account ID from the CRM.
- On this PDI the `itil` role can't set `incident.company` (the field write is silently ignored), so the seeded customers come from the callers' companies.

## ADR-013: Hybrid retrieval in Postgres + pgvector, knowledge ingested from its sources

- **Date:** 2026-11-05
- **Status:** Accepted. Supersedes ADR-008.

**Context.** Triage should be grounded in ERPNext documentation, known upstream bugs, and past tickets. ADR-008 planned keyword search (SQLite FTS5) over KB articles stored in ServiceNow. Two things changed while building it:
- **Paraphrase matters.** Users describe symptoms in their own words ("I can't find a button to reverse it"), while the right page uses product terms ("Correcting a Submitted Accounting Entry"). Keyword search alone misses those.
- **ServiceNow wasn't the right home for this content.** The docs live on docs.frappe.io and the bugs on GitHub. Copying them into ServiceNow KB articles added a step that the API can't finish: articles created through the Table API stay in Draft, and the publish workflow reverts API changes to `workflow_state`, even for admin. Publishing needed a person to run a background script in the UI.

**Decision.**
1. **One Postgres database with two indexes per chunk** (`docker-compose.yml`, image `pgvector/pgvector:0.8.6-pg17`):
   - **keyword:** a generated, weighted `tsvector` (title > heading > body) with a GIN index, ranked by `ts_rank_cd`. Strong on ERP vocabulary, field names, and exact error strings.
   - **vector:** a `vector(1024)` column with an HNSW index (cosine). Strong on paraphrase.
2. **Embeddings from Voyage AI** (`voyage-4`, 1,024 dimensions), Anthropic's recommended provider, called over REST with `requests` (no SDK). Documents and queries use their own `input_type`. The model and dimensions are stored in the index and a mismatch is refused, because vectors from different models aren't comparable.
3. **Reciprocal Rank Fusion** (k = 60) merges the two rankings, best chunk per document. RRF needs no score calibration between BM25-style ranks and cosine distances.
4. **The small model reranks** the fused candidates (8 per kind: KB article, known issue, incident) with a 0–10 rubric, under opaque ids so it can only score what was retrieved. Below `KB_RELEVANCE_THRESHOLD` (7) a hit is dropped, and "no match" is a valid outcome.
5. **Knowledge is ingested directly from its sources** (`scripts/ingest_kb.py`): the docs pages listed in `data/seed/kb_sources.json` and open ERPNext bugs from a GitHub search. Content is cached locally and never committed. Re-runs embed only changed documents (content hash for pages, `updated_at` for issues) and remove documents that disappeared (a closed bug).
6. **Incidents still sync from ServiceNow as `support_agent`**, incrementally, so ServiceNow ACLs decide which past tickets are searchable.
7. **Degrade, don't fail.** Embedding outage → keyword-only search with a warning. Sync failure → existing index with a warning. Postgres down → triage without retrieval, and the note says so. Without `RETRIEVAL_DATABASE_URL`, the SQLite FTS5 index (keyword only) is used, which also runs the unit tests.

**Consequences.**
- Retrieval works on symptoms as well as on terms. Each hit records which retriever found it (`keyword`, `vector`, or both), shown in the run output and the work note.
- One more service (Postgres) and one more vendor (Voyage). Both are standard in production RAG, and Postgres is also where state and approvals can move later.
- The knowledge isn't access-controlled by ServiceNow anymore. That's acceptable for public docs and public bugs. A private KB would sync from its own system as the agent identity, like incidents do.
- On Voyage's free tier (no payment method: about 10K tokens and 3 requests per minute) a full ingest takes minutes, so batches are sized by an estimated token count and 429s wait for the next request slot.
- The evidence for hybrid over keyword-only is still anecdotal (P2.13). A retrieval eval (hit@3 for keyword, vector, hybrid on the seed tickets) should confirm it.

## ADR-014: Switchable LLM backend: Anthropic API, or the Claude CLI for local development

- **Date:** 2026-11-05
- **Status:** Accepted

**Context.** Development runs stopped when the API account ran out of credit. The developer has a Claude subscription, which the Claude Code CLI can use in headless mode (`claude -p`) with JSON-schema structured output.

**Decision.**
- **One small interface** (`llm.py`: `StructuredLLM.parse` → `StructuredResult`) with two backends, chosen by `LLM_BACKEND`:
  - `api` (default, the production path): the Anthropic SDK, `messages.parse` with a Pydantic output format.
  - `claude_cli`: `claude -p --output-format json --json-schema ...`, the output validated against the same Pydantic model.
- **The CLI runs as a pure model call, not an agent:** `--tools ""` (no tools) and `--strict-mcp-config` (no MCP servers), so untrusted ticket text can't steer a session that can run commands. It runs in an empty temporary directory, so no `CLAUDE.md` or project settings are added to the prompt (measured: 669 prompt tokens from an empty directory against ~3,500 from the repository). The prompt goes in on stdin and the system prompt in a file, never in argv.
- **`ANTHROPIC_API_KEY` is removed from the CLI's environment**, so it uses the subscription login.

**Consequences.**
- The analyzer and reranker don't know which backend ran. Tests cover both.
- About 10 seconds per call for the CLI (process start-up), and usage counts against the subscription's limits. Fine for development, not for a queue.
- API-only features are skipped on the CLI path (the server-side refusal fallback).


# Architecture

This document describes the V1 architecture of `support-agent`: the systems it connects, how it retrieves knowledge, how it reproduces bugs, the policies that constrain it, and how it evolves into a production deployment.

> **Status legend:** ✅ built · 🚧 in progress · 📋 planned
>
> **Build log:** what has been implemented and how it was validated is recorded per phase at the end of this document. See [Phase 1: ServiceNow Integration and LangGraph Setup](#phase-1-servicenow-integration-and-langgraph-setup).

## 1. The scenario

A fictional company, **Brightline Supplies**, runs **ERPNext** (open-source ERP) for accounting. Its employees report problems to IT through **ServiceNow**. Engineering tracks real defects in **Jira**. Support answers questions from a **knowledge base**.

`support-agent` plays the role of an L1/L2 support engineer on that queue:

| System | Role in the scenario | Agent access |
|---|---|---|
| **ServiceNow** (PDI) | System of record for incidents, callers, and the KB | Table API, as `support_agent` |
| **ERPNext** (Docker) | The business application users report problems about. The **demo system** where bugs are reproduced | Browser via CDP (UI) + REST API (test-data setup) |
| **Jira Cloud** | Engineering backlog for confirmed defects | REST API |
| **Knowledge base** | Curated how-to and known-issue articles (ServiceNow KB) | Table API → local search index |
| **Claude API** | Triage (small model) and reasoning/drafting (strong model) | Messages API with tool use |

Seed tickets are closed ERPNext GitHub issues (label `accounts`), rewritten as end-user reports from fictional callers. See [SERVICENOW.md](SERVICENOW.md).

## 2. Design principles

1. **APIs for systems of record, CDP only for what the user sees.** ServiceNow and Jira are accessed only through their REST APIs. ERPNext test data is prepared through its REST API. The browser is used only to reproduce what the user saw in ERPNext's UI.
2. **The model proposes, the policy decides.** The LLM chooses tools and drafts content. A deterministic policy layer decides whether an action runs, waits for approval, or is refused.
3. **Narrow, typed tools.** Each tool does one thing, validates input with a schema, and returns structured output. No generic HTTP, SQL, or shell tools.
4. **Untrusted input stays data.** Ticket text, KB content, Jira content, and web page content are never treated as instructions.
5. **Grounded answers or no answer.** Customer-facing answers must cite a KB article. With no good match, the agent escalates instead of improvising.
6. **Separate identities per purpose.** The agent, the data seeder, and the human admin use different accounts in every system.
7. **Everything is traceable and idempotent.** Every run produces a trace. Every write is keyed so retries never double-reply or duplicate a Jira issue.
8. **Simple first, scalable later.** V1 is a single Python process plus containers. Component boundaries are drawn so each piece can become a service.

## 3. System context

```mermaid
flowchart LR
    U([Brightline employee]) -->|reports issue| SN

    subgraph SNOW["ServiceNow PDI"]
        SN[(Incidents)]
        KB[(Knowledge base)]
        USR[(Callers)]
    end

    subgraph AGENT["support-agent (Python)"]
        ORCH[Orchestrator<br/>tool-use loop]
        RET[Retrieval<br/>search index]
        GUARD[Guardrails<br/>policy · kill switch]
        BW[Browser worker]
        STATE[(SQLite<br/>state · approvals · index)]
        TR[(traces/)]
        RUI[Review UI]
    end

    subgraph DEMO["Demo system (Docker)"]
        ERP[ERPNext<br/>pinned version]
        CHR[Chrome<br/>CDP, isolated profile]
    end

    JIRA[(Jira Cloud<br/>project SUP)]
    LLM[[Claude API]]
    H([Support reviewer])

    SN <-->|Table API| ORCH
    KB -->|sync| RET
    SN -->|resolved tickets sync| RET
    ORCH <--> LLM
    ORCH --> RET
    ORCH --> GUARD
    GUARD -->|allowed writes| SN
    GUARD -->|allowed writes| JIRA
    ORCH --> BW --> CHR -->|allow-listed origin only| ERP
    BW -.->|REST: test data| ERP
    ORCH --> STATE
    ORCH --> TR
    H <--> RUI <--> STATE
```

## 4. Components

| Component | Location | Responsibility | Status |
|---|---|---|---|
| **ServiceNow setup** | `scripts/servicenow_setup.py` | Idempotent provisioning of groups, service accounts, and scoped KB | ✅ |
| **Seeder** | `scripts/seed_incidents.py`, `data/seed/` | Loads synthetic callers and incidents as `seed_loader` | ✅ |
| **Queue poller** | `src/support_agent/` | Polls incidents assigned to *AI Support Agent* that changed since the last cursor | 📋 |
| **Orchestrator** | `src/support_agent/graph/` | **LangGraph** `StateGraph`: triage → research → plan → act → disposition. Enforces step, token, and time budgets. Phase 1: read-only retrieve → format graph | 🚧 |
| **Model router** | `src/support_agent/graph/` | Small model for triage/classification. Strong model for reasoning, drafting, repro planning | 📋 |
| **Retrieval** | `src/support_agent/tools/` + SQLite | Syncs KB articles and resolved incidents into a local search index. Serves `search_kb` and `search_similar_tickets` (§5) | 📋 |
| **Typed tools** | `src/support_agent/tools/` | One module per tool, with Pydantic input/output schemas | 📋 |
| **ServiceNow client** | `src/support_agent/servicenow/` | Incidents, journal entries (work notes, comments), KB, callers. Phase 1: read-only `get_incident` | 🚧 |
| **Jira client** | `src/support_agent/tools/` | Search (JQL), create, comment, attach | 📋 |
| **ERPNext client** | `src/support_agent/tools/` | REST API for creating repro test data (fixtures) only | 📋 |
| **Browser worker** | `src/support_agent/tools/` | Drives Chrome over CDP against ERPNext: login, navigate, click, type, screenshot, collect errors | 📋 |
| **Guardrails** | `src/support_agent/guardrails/` | Autonomy policy, injection screening, argument validation, URL allow-list, idempotency, kill switch | 📋 |
| **Review UI** | `src/support_agent/review_ui/` | Local page to approve, edit, or reject pending actions | 📋 |
| **State store** | SQLite | Poll cursor, runs, approvals, idempotency keys, search index | 📋 |
| **Trace writer** | `src/support_agent/graph/` | `traces/<ticket>/<run_id>/trace.json` + screenshots. Optional LangSmith tracing is already available | 📋 |
| **Demo system** | `demo_app/` | Docker Compose for a pinned ERPNext + fixtures for the fictional company | 📋 |
| **Evals** | `evals/` | Golden tickets, runner, scoring | 📋 |

### Tool catalogue

| Tool | System | Side effect | Default policy |
|---|---|---|---|
| `get_ticket(number)` | ServiceNow | read | auto |
| `search_kb(query, k)` | local index (ServiceNow KB) | read | auto |
| `search_similar_tickets(query, k)` | local index (ServiceNow incidents) | read | auto |
| `search_jira(query)` | Jira | read | auto |
| `prepare_repro_data(scenario)` | ERPNext REST | demo-system write (isolated) | auto |
| `reproduce_in_browser(steps, expected)` | ERPNext UI via CDP | browser (sandboxed) | auto |
| `add_work_note(ticket, text)` | ServiceNow | internal write | auto |
| `draft_reply(ticket, text, citations)` | ServiceNow | pending reply | approval before send |
| `update_state(ticket, state)` | ServiceNow | write | auto for `In Progress` / `On Hold`. Resolve and close are **refused** |
| `create_jira_issue(summary, steps, evidence)` | Jira | external write | auto if confidence ≥ threshold, else approval |
| `link_jira(ticket, issue_key)` | ServiceNow + Jira | write | auto |

## 5. Retrieval

Retrieval answers three different questions, each with its own source:

| Question | Source | Tool | Used for |
|---|---|---|---|
| *"Is there an approved answer?"* | ServiceNow KB, the agent's knowledge base only | `search_kb` | How-to answers with citation, known-issue workarounds |
| *"Have we seen this before?"* | ServiceNow incidents: resolved (for answers) and open (for duplicates) | `search_similar_tickets` | Duplicate detection, past resolutions as hints |
| *"Is engineering already tracking it?"* | Jira project | `search_jira` | Jira dedupe before creating an issue |

### 5.1 Indexing pipeline

```mermaid
flowchart LR
    KBA[(ServiceNow<br/>kb_knowledge)] -->|Table API as support_agent<br/>published only| SYNC[Sync job<br/>incremental by sys_updated_on]
    INC[(ServiceNow<br/>incident)] -->|resolved + open in group| SYNC
    SYNC --> CLEAN[Normalize<br/>HTML → text, strip markup]
    CLEAN --> CHUNK[Chunk<br/>by heading, ~300–500 tokens]
    CHUNK --> IDX[(SQLite FTS5<br/>BM25 index + metadata)]
    IDX --> Q[search_kb / search_similar_tickets]
    Q --> RR[Rerank<br/>small model, top-20 → top-k]
    RR --> AG[Agent context<br/>as untrusted, cited snippets]
```

- **Sync identity = agent identity.** The sync runs as `support_agent`, so the index contains exactly what ServiceNow ACLs and KB user criteria allow the agent to read. Access control is inherited, not re-implemented.
- **Incremental.** Only records with `sys_updated_on` newer than the last sync are fetched. Retired or unpublished articles are removed from the index.
- **Chunk metadata:** source (`kb` / `incident`), number (`KB0010001` / `INC0010005`), article version, title, ERPNext module and version tags, URL, updated time.
- **Search:** SQLite FTS5 (BM25), built into Python with no extra service. Top 20 candidates are **reranked** by the small model for semantic relevance. Then a **relevance threshold** applies: below it, the result is "no match".
- **Embeddings later.** Vector search (e.g. `sqlite-vec` + an embeddings provider) is a drop-in upgrade behind the same tool interface, to be added if evals show BM25 + rerank recall is insufficient.

### 5.2 Using retrieved content

- Snippets enter the prompt inside delimited `<retrieved_document source=... id=...>` blocks, labeled as untrusted data.
- Customer replies must cite at least one `KB…` number. The policy layer rejects a draft reply whose citations aren't in the retrieved set.
- Resolved incidents are **hints, not sources.** They can point to the right KB article or suggest a workaround, but they are never quoted to a customer.
- No match above the threshold → no answer. The agent writes a work note listing what it searched and routes the ticket to a human.

### 5.3 KB content

- Synthetic ERPNext how-to and known-issue articles, provided separately and loaded by a KB seeder (as `seed_loader`) into the agent's knowledge base.
- Articles carry ERPNext version applicability, so the agent doesn't apply a v15 workaround to a v16 problem.
- Out of scope for V1: the official ERPNext documentation site and the instance's demo KBs. One curated source keeps answers auditable.

## 6. Bug reproduction in ERPNext

### 6.1 Demo system

- **ERPNext in Docker** (official `frappe_docker` images), **pinned to a version that predates the fixes** for the seeded bugs. The seed tickets are closed upstream issues, so a current release won't reproduce them.
- **Fixtures via REST API** (`prepare_repro_data`): the fictional company "Brightline Supplies", currencies (INR, USD, KWD), suppliers, accounts, item tax templates. Fixtures are created as a dedicated ERPNext API user, never Administrator.
- **Isolation:** each repro run creates uniquely named records (prefix `REPRO-<run_id>`). A database snapshot can be restored to reset the site between demo sessions.

### 6.2 Reproduction flow

1. The strong model turns the ticket into a **structured repro plan**: preconditions (data needed), UI steps, expected result, and failure signal (error text, wrong status value).
2. `prepare_repro_data` creates the preconditions through the ERPNext API.
3. `reproduce_in_browser` logs in as the repro user and executes the UI steps over CDP. It captures:
   - screenshots at each step and at failure
   - ERPNext error dialogs (DOM text), console errors, and failed network responses (Frappe returns validation errors as `417` with a message)
4. The result is `reproduced` / `not_reproduced` / `blocked`, with observations and evidence paths, all written to the trace.

### 6.3 Browser guardrails

- Chrome runs in its own container with a **dedicated profile** and no saved credentials except the repro user's session.
- A CDP request interceptor **blocks every origin except the ERPNext demo URL**.
- The agent **never navigates to URLs from tickets.** It always starts from the configured ERPNext base URL.
- The repro user has a limited ERPNext role (e.g. *Accounts User*), not System Manager.

## 7. Jira integration

| Aspect | Design |
|---|---|
| Project | `SUP`, issue type Bug |
| Content | Summary, steps to reproduce, expected vs actual, environment (ERPNext version), evidence (screenshots attached), link to the ServiceNow incident |
| Labels | `support-agent`, `snow-INC00100xx`, ERPNext module (e.g. `accounts`) |
| Dedupe | Before creating: JQL search by label `snow-<incident>`, then by summary similarity among open issues. A match means comment and link instead of creating a new issue |
| Back-link | Jira key and URL stored on the incident (work note now, plus a dedicated `u_jira_issue` field, 📋) and in the agent's state DB |
| Idempotency | Key `(incident, "create_jira")` stored before the API call, checked on retry |
| Autonomy | Auto-create only when **reproduced** and confidence ≥ `JIRA_AUTO_CONFIDENCE`. Otherwise it goes to the review UI |

## 8. Data flow by ticket type

### 8.1 How-to → KB answer (e.g. INC0010009, INC0010010)

```mermaid
sequenceDiagram
    participant SN as ServiceNow
    participant A as Agent
    participant R as Retrieval
    participant H as Reviewer
    SN->>A: incident (New, AI Support Agent)
    A->>A: triage → how_to (small model)
    A->>R: search_kb(query)
    R-->>A: top-k cited snippets
    alt match above threshold
        A->>A: draft answer citing KB article (strong model)
        A->>SN: work note: proposed answer + sources
        A->>H: pending reply
        H-->>A: approve / edit / reject
        A->>SN: customer comment, state → On Hold (awaiting caller)
    else no match
        A->>SN: work note: searched X, no approved answer → human
    end
```

### 8.2 Missing info → ask the caller (e.g. INC0010003)

1. Triage flags missing essentials: which document or screen, steps, ERPNext version, the exact error.
2. The agent drafts **one** concise question listing exactly what it needs.
3. The reply goes to approval. Once sent, state is set to *On Hold – Awaiting Caller*.
4. When the caller responds, the incident changes, gets re-polled, and is triaged again.

### 8.3 Bug → reproduce → Jira (e.g. INC0010005, INC0010006)

```mermaid
sequenceDiagram
    participant SN as ServiceNow
    participant A as Agent
    participant R as Retrieval
    participant E as ERPNext (API + CDP)
    participant J as Jira
    SN->>A: incident
    A->>A: triage → bug
    A->>R: search_kb + search_similar_tickets
    A->>J: search_jira (dedupe)
    alt known issue / existing Jira
        A->>SN: work note + link existing Jira, workaround reply → approval
    else new
        A->>A: repro plan (strong model)
        A->>E: prepare_repro_data (REST)
        A->>E: reproduce_in_browser (CDP)
        E-->>A: reproduced? screenshots, error text
        alt reproduced and confidence ≥ threshold
            A->>J: create_jira_issue + attachments
        else low confidence / not reproduced
            A->>A: queue for approval or route to missing-info
        end
        A->>SN: link_jira + work note (repro summary)
        A->>SN: draft reply to caller → approval
    end
```

### 8.4 Duplicate

If `search_similar_tickets` finds an open incident above the similarity threshold, or `search_jira` finds a matching open issue, the agent adds a work note linking the original, links the existing Jira issue, and creates nothing new.

## 9. Identities and access

| System | Account | Used by | Access | Credentials |
|---|---|---|---|---|
| ServiceNow | `admin` | Human, setup script | Full. API exception role only during setup | `.env.admin` |
| ServiceNow | `support_agent` | Agent | API only (WSAO). `itil` via group *AI Support Agent*. Reads only its knowledge base | `.env` |
| ServiceNow | `seed_loader` | Seeder scripts | API only (WSAO). `itil` + `user_admin` via group *Seed Data Loaders* | `.env` |
| ServiceNow | callers | Seed data | No roles, no login | none |
| ERPNext | Administrator | Human only | Full | password manager |
| ERPNext | `repro_bot` 📋 | Browser worker | UI login, limited role (Accounts User) | `.env` |
| ERPNext | `erp_fixtures` 📋 | `prepare_repro_data` | API key/secret, scoped to fixture doctypes | `.env` |
| Jira | agent account 📋 | Agent | API token, project `SUP` only | `.env` |
| Anthropic | API key 📋 | Agent | Spend-limited key | `.env` |

All secrets live only in gitignored `.env` files and are redacted from traces.

## 10. Autonomy and guardrail policy

The policy is declarative (`src/support_agent/guardrails/`) and evaluated **outside** the model:

| Action | Mode | Notes |
|---|---|---|
| Classify, search, read | auto | |
| Prepare repro data in ERPNext | auto | Demo system only, uniquely named records |
| Reproduce in browser | auto | ERPNext origin only |
| Work note | auto | Internal only |
| Customer reply | approval | Must cite retrieved KB. Reviewer can edit |
| Jira create | conditional | Auto if reproduced and confidence ≥ `JIRA_AUTO_CONFIDENCE`, else approval |
| Resolve / close | **forbidden** | Humans only |
| Any write while kill switch is on | **forbidden** | Read and draft only |

**Kill switch:** `AGENT_KILL_SWITCH=true` (env) or a runtime flag file, checked before every write.
**Budgets:** max steps, tokens, and wall-clock time per run. Hitting a limit ends the run with a work note and hands the ticket to a human.

## 11. Security model

| Threat | Mitigation |
|---|---|
| **Prompt injection in ticket text** | Delimited untrusted-data blocks. Deterministic policy outside the model. Heuristic injection screen flags tickets for review. No destructive tools exist |
| **Injection via KB, Jira, or page content** | Treated the same as ticket text. Page text is summarized as observations, never executed |
| **Malicious URLs in tickets** | Browser starts only from the configured ERPNext URL. Interceptor blocks all other origins |
| **Browser compromise or data leakage** | Separate container and profile, demo system only, synthetic data only |
| **Over-privileged credentials** | Separate least-privilege accounts per purpose (§9). No admin use at runtime |
| **Secret leakage** | Secrets only in `.env` (gitignored). Traces redact auth headers and tokens |
| **Duplicate or replayed actions** | Idempotency key per `(ticket, action, content hash)`. Jira dedupe search |
| **Runaway agent** | Budgets, rate limits, kill switch |
| **Ungrounded answers** | Citation required and validated against the retrieved set. No-match → escalate |

## 12. Observability

- **Per-ticket trace:** `traces/<ticket>/<run_id>/trace.json` holds an ordered list of events: model calls (model, tokens, latency, cost), retrieval queries and results (ids, scores), tool calls (input, output, policy decision), approvals, errors. Screenshots are stored alongside.
- **Run summary:** one line per run with disposition, duration, cost, and actions taken.
- **ServiceNow-visible audit:** every agent action leaves a work note, so humans see the agent's reasoning in the ticket's activity stream.

## 13. Evaluation

- **Golden set:** the 10 seed incidents plus 10–20 more (duplicates, prompt-injection attempts, non-reproducible bugs), each labeled in `data/seed/` or `evals/golden/` with expected type, KB article, escalation, and forbidden actions.
- **Metrics:** routing accuracy · KB retrieval hit@k · citation validity · correct escalation (Jira exactly when expected) · repro success rate · **unsafe actions = 0** (hard gate).
- **Execution:** offline by default against mocked ServiceNow/Jira/ERPNext clients (deterministic, free). Optional live mode against the PDI and demo ERPNext.
- **CI:** safety gate + fast subset on every PR.

## 14. Production architecture

V1 runs as one process to stay demoable. Production keeps the same tools and policy but changes the runtime around them.

```mermaid
flowchart LR
    SN[(ServiceNow)] -->|Business Rule / Flow webhook| GW[Ingest API]
    GW --> Q[[Job queue]]
    Q --> W1[Stateless worker]
    Q --> W2[Stateless worker]
    Q --> Wn[Stateless worker ...]
    W1 & W2 & Wn --> WF[(Durable execution<br/>workflow state)]
    W1 & W2 & Wn --> BP[Headless browser pool]
    W1 & W2 & Wn --> MR[Model router<br/>+ rate limiter]
    W1 & W2 & Wn --> VS[(Search service<br/>hybrid BM25 + vectors)]
    MR --> LLM[[Claude API]]
    W1 & W2 & Wn --> JR[(Jira)]
    W1 & W2 & Wn -->|approval request| SNA[ServiceNow approval records]
    SNA -->|approved| GW
    W1 & W2 & Wn --> OBS[Tracing / metrics<br/>OpenTelemetry]
```

| Concern | V1 | Production |
|---|---|---|
| **Ingest** | Polling the Table API | Business Rule / Flow Designer → webhook → ingest API. Polling kept as a reconciliation fallback |
| **Work distribution** | In-process loop | Job queue (SQS / Pub/Sub / Redis Streams). One message per ticket change, deduped by `sys_id + sys_mod_count` |
| **Workers** | Single process | Stateless workers that scale horizontally |
| **Long waits (approvals)** | SQLite pending table | Durable execution (e.g. Temporal): a run pauses for hours and resumes exactly once |
| **Retrieval** | SQLite FTS5 + LLM rerank | Managed hybrid search (BM25 + vectors), event-driven KB sync, per-tenant indexes |
| **Browser** | One local Chrome | Headless browser pool, one context per job, network-isolated to allow-listed apps |
| **Demo/target app** | Pinned ERPNext in Docker | Customer's staging environment, never production, through a dedicated repro tenant |
| **Models** | Two fixed models | Routing by step and difficulty, fallback model, prompt caching, per-tenant budgets |
| **Rate limits and retries** | Basic backoff | Token buckets per API, backoff with jitter, circuit breakers, dead-letter queue |
| **Approvals** | Local review UI | Native ServiceNow approvals or UI actions, where reviewers already work |
| **Auth** | Basic Auth (WSAO accounts) | OAuth 2.0 client credentials, short-lived tokens, secret manager |
| **Observability** | JSON traces | OpenTelemetry, dashboards (deflection rate, approval rate, cost per ticket), alerting |

### Deployment path

1. **Docker Compose:** agent, review UI, browser, ERPNext, all with one command.
2. **Cloud Run / ECS Fargate:** same containers, managed queue and Postgres.
3. **Kubernetes:** autoscaling browser pools, multi-tenant isolation, network policies.

## 15. Trade-offs

| Decision | Benefit | Cost |
|---|---|---|
| ERPNext as demo system (vs custom demo app) | Real, complex enterprise app with real bugs, which is credible for hiring audiences | Heavier to run (several containers). Version pinning needed for closed bugs |
| Seed tickets from real closed issues | Realistic language and bugs with known ground truth | Must run a pre-fix ERPNext version. Some bugs need specific data or currency setups |
| Polling over webhooks in V1 | Works on a PDI without inbound networking | Latency and extra API calls |
| SQLite FTS5 + LLM rerank over vector DB | Zero extra services, inspectable, good on keyword-heavy ERP text (doctype names, error strings) | Weaker on paraphrases. Embeddings are a planned drop-in |
| Index synced as the agent identity | ACLs inherited automatically | Index must be rebuilt if agent permissions change |
| Single process + SQLite | Trivial to run and reason about | No horizontal scale. Boundaries kept clean for migration |
| Local review UI over ServiceNow approvals | Fast to build, no PDI customization | Reviewers leave their usual tool |
| Raw CDP over a high-level framework | Explicit control over interception and evidence capture | More code than Playwright |
| Narrow tools | Auditable, easy to guard and test | New capability = new tool, by design |
| Confidence-gated Jira auto-create | Less reviewer load for clear bugs | Needs calibration through evals |

## 16. Open questions

| # | Question | Proposed default |
|---|---|---|
| 1 | Which ERPNext version to pin? The seed bugs span v15 and v16 fixes | Pick the version that reproduces the most seed bugs. Mark the others `not_reproducible_in_demo` (still a valid eval path) |
| 2 | Rename KB "Customer Portal Support" now that the demo system is ERPNext? | Rename to **"ERPNext Support"** via the setup script |
| 3 | Store the Jira key on the incident in a custom field? | Add `u_jira_issue` via setup script (admin). Work note in the meantime |
| 4 | Embeddings provider, if needed | Decide after the retrieval eval (§13) |

---

# Build log

## Phase 1: ServiceNow Integration and LangGraph Setup

**Status: ✅ complete (2026-10-27).** Read-only. No LLM calls, no writes to ServiceNow.

> Numbering note: this is the first *implementation* phase. It covers the "Read the queue" groundwork in [TASKS.md](TASKS.md) Phase 1, scoped to retrieving a single incident through a LangGraph workflow.

### P1.1 Goal

Prove the end-to-end skeleton the agent will grow on: typed configuration → a read-only ServiceNow client → a LangGraph workflow → a terminal command and a visual debugger (LangSmith Studio) → automated tests.

### P1.2 What was built

| Component | Location | Status |
|---|---|---|
| Configuration (typed, env-based, password hidden from `repr`) | `src/support_agent/config.py` | ✅ |
| ServiceNow client (read-only, typed errors) | `src/support_agent/servicenow/client.py` | ✅ |
| Incident model (Pydantic) | `src/support_agent/servicenow/models.py` | ✅ |
| Graph state (typed input / output / internal) | `src/support_agent/graph/state.py` | ✅ |
| Graph nodes | `src/support_agent/graph/nodes.py` | ✅ |
| Graph definition + Studio entry point | `src/support_agent/graph/workflow.py` | ✅ |
| Terminal interface | `src/support_agent/cli.py`, `main.py` | ✅ |
| LangGraph dev server / Studio config | `langgraph.json` | ✅ |
| Optional LangSmith tracing | `.env` (`LANGSMITH_*`) | ✅ (off by default) |
| Tests (mocked ServiceNow) | `tests/` | ✅ 30 offline + 1 live |
| RAG, LLM analysis, polling, webhooks, Jira, ERPNext | n/a | 📋 later phases |

### P1.3 Architecture

```mermaid
flowchart LR
    subgraph Entry["Entry points"]
        CLI["Terminal<br/>python main.py --incident INC0010001"]
        STUDIO["LangSmith Studio<br/>(browser)"]
    end

    DEV["langgraph dev<br/>local API server :2024<br/>(langgraph.json)"]

    subgraph Pkg["support_agent package"]
        subgraph G["graph/ (LangGraph)"]
            S((START)) --> R[retrieve_incident]
            R --> F[format_incident]
            F --> E((END))
        end
        CFG[config.py<br/>ServiceNowSettings]
        SNC[servicenow/client.py<br/>ServiceNowClient: GET only]
        M[servicenow/models.py<br/>Incident]
    end

    SN[(ServiceNow PDI<br/>Table API)]
    LS[(LangSmith<br/>optional tracing)]

    CLI -->|graph.invoke| G
    STUDIO <-->|HTTP| DEV --> G
    R --> SNC
    CFG --> SNC
    SNC -->|"GET /api/now/table/incident<br/>Basic Auth as support_agent"| SN
    SNC --> M
    G -. "LANGSMITH_TRACING=true" .-> LS
```

**Separation of concerns:**

| Layer | Knows about | Doesn't know about |
|---|---|---|
| `config` | Environment variables | ServiceNow, LangGraph |
| `servicenow` | Table API, HTTP, auth | LangGraph, terminal |
| `graph` | State, nodes, flow | HTTP details (client injected through a factory) |
| `cli` | Arguments, output | ServiceNow, HTTP |

The client is **injected** into the graph (`build_graph(client_factory=...)`), so tests run the real graph with a mocked client, and later phases can swap in other clients (Jira, ERPNext) the same way.

### P1.4 Tools and versions

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11.4 (`.venv`) | Runtime |
| LangGraph | 1.2.18 | Workflow / state machine |
| LangGraph CLI (`[inmem]`) | 0.4.40 | `langgraph dev` local server for Studio |
| langgraph-api | 0.16.7 | Dev server runtime (installed by the CLI) |
| LangSmith SDK | 0.14.13 | Optional tracing, Studio |
| Pydantic | 2.14.1 | Incident model, schemas |
| requests | 2.34.2 | ServiceNow HTTP (reuses the existing working connection) |
| python-dotenv | 1.2.4 | Load `.env` |
| pytest | 9.1.1 | Tests |

Dependencies are declared in `pyproject.toml` (runtime + `[dev]` extra). `langgraph.json` uses `"dependencies": ["."]`, so the dev server installs the same package.

### P1.5 Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then fill in SERVICENOW_* (see docs/SERVICENOW.md)
```

The ServiceNow connection reuses the existing `support_agent` service account and `.env` variables (`SERVICENOW_INSTANCE_URL`, `SERVICENOW_USERNAME`, `SERVICENOW_PASSWORD`). No new authentication was created.

### P1.6 LangGraph workflow

```
START → retrieve_incident → format_incident → END
```

| Node | Reads | Writes | Behavior |
|---|---|---|---|
| `retrieve_incident` | `incident_id` | `incident`, `error`, `error_type` | Calls `ServiceNowClient.get_incident`. Every `ServiceNowError` is caught and written to state, so the graph never crashes on API errors |
| `format_incident` | `incident`, `error` | `display` | Renders a text card (or `ERROR (<type>): <message>`) for the terminal and Studio |

**State schemas** (`graph/state.py`):
- `InputState`: `incident_id` only. This is what Studio shows as the input form.
- `OutputState`: `incident`, `error`, `display`. This is what callers get back.
- `IncidentState`: internal, adds `error_type`.

The workflow is deliberately linear. Later phases add nodes and conditional edges (triage → route by ticket type) without changing the entry points.

### P1.7 ServiceNow API integration

| Aspect | Implementation |
|---|---|
| Endpoint | `GET /api/now/table/incident` |
| By number | `sysparm_query=number=INC0010001`, `sysparm_limit=1` |
| By sys_id | `GET /api/now/table/incident/<sys_id>` |
| Fields | `sys_id, number, short_description, description, priority, state, category, assignment_group, assigned_to, caller_id, opened_at` |
| Display values | `sysparm_display_value=true`, `sysparm_exclude_reference_link=true` (names instead of sys_ids for group, assignee, caller) |
| Auth | Basic Auth as `support_agent` (WSAO account, see [SERVICENOW.md](SERVICENOW.md) §5) |
| Timeout | 30 s (`SERVICENOW_TIMEOUT_SECONDS`) |
| Input validation | Identifier must match `INC\d{7,}` or a 32-hex sys_id. Anything else is rejected **before** any request, which also blocks encoded-query injection such as `INC001^ORactive=true` |
| Errors | `InvalidIdentifierError`, `IncidentNotFoundError` (empty result / 404), `ServiceNowAuthError` (401), `ServiceNowPermissionError` (403), `ServiceNowError` (other HTTP, network, non-JSON). Messages contain only ServiceNow's error text |
| Read-only | The client's only public method is `get_incident`, and it only issues GET. A test enforces both |

### P1.8 Terminal commands

```bash
python main.py --incident INC0010001                          # by number
python main.py --incident 7b17848cc3d05310783ef9477d013199    # by sys_id
python main.py --incident INC0010001 --json                   # final graph state as JSON
```

Exit codes: `0` success, `1` error (not found, invalid id, auth, API). `main.py` adds `src/` to the path, so it also runs without `pip install -e .`.

**Validated output (live, 2026-10-27):**

```
$ python main.py --incident INC0010001
Number            : INC0010001
Short description : Some ERPNext screens stay in English after switching my language to Arabic
Priority          : 4 - Low
State             : New
Category          : Software
Assignment group  : AI Support Agent
Assigned to       : -
Caller            : Amira Haddad
Opened            : 2026-10-27 13:10:15
sys_id            : 7b17848cc3d05310783ef9477d013199

Description:
  Hi,
  I changed my ERPNext language to Arabic. ...

$ python main.py --incident INC9999999
ERROR (IncidentNotFoundError): Incident INC9999999 not found

$ python main.py --incident "INC001^ORactive=true"
ERROR (InvalidIdentifierError): 'INC001^ORactive=true' is not an incident number (e.g. INC0010001) or a 32-character sys_id
```

### P1.9 LangSmith Studio

**`langgraph.json`:**

```json
{
  "python_version": "3.11",
  "dependencies": ["."],
  "graphs": { "incident_workflow": "./src/support_agent/graph/workflow.py:graph" },
  "env": ".env"
}
```

**Run:**

```bash
source .venv/bin/activate
langgraph dev            # API on http://127.0.0.1:2024, opens Studio
```

Studio URL: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`

1. Sign in to LangSmith (free account) when prompted.
2. Select the **incident_workflow** graph. It shows `__start__ → retrieve_incident → format_incident → __end__`.
3. Enter `{"incident_id": "INC0010001"}` in the input form and click **Submit**.
4. Click each node to inspect its state update (`incident`, `error`, `display`).

**Optional tracing.** Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env` to send runs to the LangSmith project `support-agent`. It's off by default, because traces include incident content. That's fine for synthetic data, but it's a data-sharing decision.

**Browser note.** Safari blocks an HTTPS page from calling `http://127.0.0.1`. Use Chrome or Firefox, or run `langgraph dev --tunnel`.

**Validated (2026-10-27):** dev server started, `GET /ok` → 200, graph `incident_workflow` registered (`POST /assistants/search`), and `POST /runs/wait` with `{"incident_id": "INC0010005"}` returned the real incident. That's the same API Studio uses. No blocking-I/O warnings were raised.

**Validated in the Studio UI (2026-10-27):** the graph renders as connected nodes (`retrieve_incident → format_incident`). An incident id entered in the input form ran successfully (server log: *Created run → Starting background run → Background run succeeded*, ~0.5 s), and node outputs could be inspected.

### P1.10 Safety (read-only)

| Requirement | How it's enforced |
|---|---|
| No incident modification | Client has no write methods and issues GET only (tested) |
| No work notes or assignment changes | Same as above. No journal or PATCH code exists |
| No LLM calls | No model SDK is imported or installed for runtime |
| No credentials in logs | `ServiceNowSettings.password` has `repr=False`. Error messages contain only ServiceNow's error body (tested). `.env` is gitignored |
| Least privilege | Runs as `support_agent` (API-only, `itil` via group), never `admin` |

### P1.11 Testing results

`pytest` (offline, mocked ServiceNow): **30 passed**. `pytest -m live` (running dev server): **1 passed**.

| Area | Tests | Covers |
|---|---|---|
| ServiceNow client | 17 | By number, by sys_id, not found (empty / 404), invalid ids incl. injection (5), 401/403/500 mapping, network error, non-JSON, no password in errors or `repr`, read-only enforcement |
| LangGraph | 5 | Graph structure (edges), node-by-node state transitions (`stream_mode="updates"`), output schema, not-found and auth errors flowing to `display` |
| Terminal | 5 | Text output, `--json`, error exit code, required argument, `main.py` entry point |
| Studio config | 3 + 1 live | `langgraph.json` valid, graph path resolves to a compiled graph, input schema is `incident_id` only. Live: dev server `/ok` and graph registration |

Manual live validation: CLI by number and by sys_id, not-found, invalid id, and dev-server run (see P1.8 and P1.9).

### P1.12 Why LangGraph

| Need | How LangGraph addresses it |
|---|---|
| **Explicit, auditable control flow** | The agent is a graph of named nodes and edges, not a free-form loop. Reviewers can see exactly which paths exist (triage → KB answer / ask info / reproduce → Jira) |
| **Typed shared state** | One state object flows through nodes. Each node returns a partial update, which is easy to test node by node |
| **Human-in-the-loop** | Built-in interrupts and checkpointing pause a run for approval (customer replies, low-confidence Jira) and resume it later, which matches our autonomy policy |
| **Durable execution** | Checkpointers (SQLite → Postgres) persist state between steps, a path to the "durable execution" requirement in §14 |
| **Visual debugging** | Studio shows the graph, every node's input and output, and lets you re-run from a step |
| **Model-agnostic** | Nodes are plain Python. Claude is called from nodes we control, alongside deterministic guardrails |

Trade-off: it's an extra framework dependency and abstraction compared with a hand-written loop (the original design). We accept it for checkpointing, interrupts, and Studio, which we would otherwise build ourselves. See ADR-009.

### P1.13 How this supports later phases

| Future capability | Extension point |
|---|---|
| **RAG (KB + similar tickets)** | New `retrieve_context` node after `retrieve_incident`, backed by the retrieval module (§5). State gains `kb_results`, `similar_tickets` |
| **LLM analysis** | `triage` node (small model) and conditional edges by ticket type. `analyze` / `draft_reply` nodes (strong model) |
| **ERPNext integration** | `prepare_repro_data` / `reproduce_in_browser` nodes with an injected ERPNext client and browser worker, the same factory pattern as `ServiceNowClient` |
| **Polling** | A poller lists changed incidents and calls `graph.invoke({"incident_id": ...})` per ticket. The graph stays unchanged |
| **FastAPI webhooks** | A webhook endpoint enqueues the incident id. Workers invoke the same graph, or the LangGraph server API (`/runs`) serves it directly |
| **Jira escalation** | `search_jira` → `create_jira_issue` nodes behind a policy check and an `interrupt()` for approval |
| **Guardrails** | Policy checks run as nodes or wrappers before any write node. The kill switch is checked there |

### P1.14 Decisions and issues log

| # | Type | Item | Resolution |
|---|---|---|---|
| D1 | Decision | Orchestrate with **LangGraph** instead of a hand-written tool loop | ADR-009 |
| D2 | Decision | Single `support_agent` package under `src/` (instead of top-level `agent`, `tools`, ...) | ADR-010. Avoids clashing with other installed packages |
| D3 | Decision | Keep `requests` for ServiceNow (reuse the working connection) | `httpx` remains an option if we go async |
| D4 | Decision | Inject the client through a factory and read settings at run time, not import time | Studio can load the graph without credentials, and tests use mocks |
| D5 | Decision | Separate input and output schemas | Studio shows a clean one-field form, and internal fields stay private |
| I1 | Issue | Studio input schema failed on Python 3.11: *"Please use `typing_extensions.TypedDict` instead of `typing.TypedDict` on Python < 3.12"* (caught by `test_graph_input_schema_is_incident_id_only`) | State uses `typing_extensions.TypedDict` |
| I2 | Issue | `langgraph dev` crashed at startup: `ValueError: Unknown level: ''` | `.env` had an empty `LOG_LEVEL=` placeholder, which the dev server reads. Set `LOG_LEVEL=INFO` in `.env` and `.env.example` |
| I3 | Issue (earlier) | ServiceNow API returned 401 for `admin` despite a valid password | New PDIs enforce the Basic Auth restriction. The agent uses a WSAO account (ADR-006, [SERVICENOW.md](SERVICENOW.md) §5) |

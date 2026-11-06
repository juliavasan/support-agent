# Build Plan

Each task is sized for a single commit. Check a box when its "done when" condition is met.

---

## Phase 0: Setup

- [x] **Repository foundation** (docs, folder skeleton, license, `.gitignore`, `.env.example`, `pyproject.toml`). *Done when:* initial commit exists on `main`.
- [x] **ServiceNow PDI.** Request a Personal Developer Instance and note the instance URL. *Done when:* you can log in to the instance.
- [x] **ServiceNow service account.** Create a `support_agent` user with least-privilege roles (incident read/write, KB read, no admin). *Done when:* `curl` with basic auth reads `/api/now/table/incident?sysparm_limit=1`.
- [x] **ServiceNow assignment group.** Create an "AI Support Agent" group so the agent only works its own queue. *Done when:* the group exists and the service account can filter incidents by it.
- [ ] **Jira Cloud Free.** Create a site and a project (e.g. `SUP`) with a Bug issue type. *Done when:* the project is visible in the browser.
- [ ] **Jira API token.** Create a token for a dedicated account with access to the project only. *Done when:* `curl` reads `/rest/api/3/myself`.
- [ ] **Remove admin API exception.** After all ServiceNow data setup is done, remove `snc_basic_auth_api_access` from `admin`. *Done when:* admin API call returns 401 and `support_agent` still returns 200.
- [x] **Anthropic API key.** Create a key with a spend limit. *Done when:* a one-line test call returns a response. *(Rotate it: the first key was shared in a chat.)*
- [ ] **Local `.env`.** Copy `.env.example` and fill in values. *Done when:* `.env` is populated and `git status` doesn't show it.
- [x] **Python tooling.** Set up a virtualenv / `uv`, ruff, mypy, pytest, and pre-commit. *Done when:* `ruff check`, `mypy`, and `pytest` all run (with zero tests) and pass.
- [x] **Config loader.** Typed settings from env with validation that fails fast on missing vars. *Done when:* a unit test covers missing and valid config.
- [x] **Package layout decision.** Single `support_agent` package (ADR-010). *Done when:* `python -c "import support_agent.graph.workflow"` works in the venv.

## Phase 1: Read the queue (no AI)

- [~] **ServiceNow client: auth and base request.** Use HTTPS, timeouts, and retries with backoff on 429/5xx. *(Phase 1: auth, timeouts, typed errors done. Retries still to do.)* *Done when:* unit test with mocked HTTP passes.
- [x] **`get_ticket` tool.** *(Implemented as `ServiceNowClient.get_incident` + `retrieve_incident` graph node, by number or sys_id.)* Fetch an incident by number into a typed `Ticket` model. *Done when:* returns a parsed model for a seeded incident. Unit test uses a recorded fixture.
- [ ] **List queue.** Query incidents for the agent's assignment group, filtered by state and updated-since. *Done when:* CLI prints the open queue.
- [ ] **Poll cursor and state store.** SQLite table for last-seen `sys_updated_on` / `sys_mod_count`. *Done when:* a re-run doesn't reprocess unchanged tickets.
- [x] **Synthetic ticket seeder.** Script that creates 10+ synthetic incidents in the PDI from a seed file. *Done when:* `python3 scripts/seed_incidents.py` creates the tickets and is idempotent.
- [ ] **Trace writer skeleton.** Per-ticket `trace.json` with run id and events. *Done when:* the poller writes a trace for each ticket it sees.
- [x] **LangGraph skeleton.** `START → retrieve_incident → format_incident → END` with typed state. *Done when:* graph runs via CLI and Studio (see Phase 1 build log).
- [x] **Single-incident CLI.** `python main.py --incident INC0010001`. *Done when:* prints the incident, with error handling.
- [x] **LangSmith Studio.** `langgraph.json` + `langgraph dev`. Optional tracing. *Done when:* graph visible and runnable in Studio.
- [ ] **CLI entrypoint.** `support-agent poll --once`. *Done when:* the command lists new tickets and writes traces.

## Phase 2: Triage + KB search

### Step 1: LLM triage → internal work note

See the [Phase 2 Step 1 build log](ARCHITECTURE.md#phase-2-step-1-llm-triage-and-internal-work-note) and ADR-011.

- [x] **Structured triage output.** Pydantic `TriageAnalysis` via `messages.parse`: summary, impact, urgency, category, hypotheses, troubleshooting steps, missing info. *Done when:* invalid output is rejected in tests.
- [x] **Priority matrix.** Configurable impact × urgency → P1–P5, validated on load. *Done when:* all 9 combinations and invalid files are tested.
- [x] **Triage prompt file.** `src/support_agent/prompts/triage_system.md`, with ticket text delimited and escaped as untrusted data. *Done when:* a wrapper-escape test passes.
- [x] **`analyze_incident` node.** LLM failures (API, refusal, truncation, schema) recorded in state, never raised. *Done when:* graph tests cover each failure.
- [x] **Work-note format.** "AI Triage Assessment (Preliminary)", hypotheses labeled, run marker, 4,000-character cap. *Done when:* format tests pass.
- [x] **`add_work_note` + `list_work_notes` client methods.** Only `work_notes` can be written. *Done when:* a test pins the PATCH body and the public API.
- [x] **`write_work_note` node.** Dry run default, write policy (kill switch, `WRITE_TEST_INCIDENTS`), duplicate check, journal read-back, protected-field comparison. *Done when:* graph tests cover every status.
- [x] **CLI `--write` and Studio `mode` input.** *Done when:* CLI tests pass and Studio shows the new input field.
- [x] **Live dry run with the real LLM.** *Done when:* `python main.py --incident INC0010001` prints a real assessment and writes nothing. *(Opus 5.5 and Haiku 5.5, see build log P2.9.)*
- [x] **Live write on one test incident.** Write, rerun (duplicate skipped), and try a non-designated incident (blocked). *Done when:* the steps in build log P2.10 pass and are recorded. *(2026-11-05, after fixing the work-note read-back, I10.)*

### Step 1b: Premium-customer priority rule

See [build log P2.12](ARCHITECTURE.md#p212-premium-customer-priority-rule) and ADR-012.

- [x] **Customer tier config.** `analysis/customer_tiers.json` (premium: +1 level, never below P3), validated on load, `CUSTOMER_TIERS_PATH` override. *Done when:* the rule and invalid files are tested.
- [x] **`apply_customer_policy` node** between analyze and write. Customer from the incident's Company, else the caller's company. Fails closed on a bad config. *Done when:* graph tests cover premium, standard, a premium claim in ticket text, and a broken config.
- [x] **Work note shows the adjustment.** *Done when:* the note reads "matrix gives P5; raised for premium customer …".
- [x] **Seed customer companies.** Three fictional companies, callers linked to them. *Done when:* the read-back shows each seeded incident's company.
- [x] **Live dry run on a premium incident.** *Done when:* INC0010002 shows the adjusted priority and writes nothing.

### Step 2: Retrieval (RAG)

See the [Phase 2 Step 2 build log](ARCHITECTURE.md#phase-2-step-2-retrieval-rag) and ADR-013 / ADR-014. The ServiceNow-KB plan (rename the KB, seed articles, KB sync) was dropped: knowledge is ingested from its sources instead.

- [x] **Hybrid index.** Postgres + pgvector in Docker: weighted full text + 1,024-d vectors, RRF. *Done when:* `pytest -m postgres` passes.
- [x] **Embeddings.** Voyage `voyage-4` over REST, token-sized batches, 429 handling, model pinned in the index. *Done when:* tests pass and a live ingest completes on the free tier.
- [x] **Knowledge ingestion.** Docs pages and open ERPNext bugs, cached, incremental, closed bugs removed. *Done when:* `scripts/ingest_kb.py` indexes 32 pages and 40 bugs, and a re-run embeds nothing.
- [x] **Incident sync.** Incremental, as `support_agent`, excluding the incident being triaged. *Done when:* the index contains the seed incidents and a re-sync fetches none.
- [x] **Rerank + threshold.** Small model, opaque ids, 0–10, threshold 7, top 3 per kind. *Done when:* an unrelated query returns no match in tests.
- [x] **`retrieve_context` node.** Degrades on failure; citations validated against the retrieved set; "Related knowledge" in the note. *Done when:* a live dry run cites retrieved docs.
- [x] **LLM backend switch.** Anthropic API or Claude CLI (no tools, empty working directory). *Done when:* a live dry run completes on the CLI backend.
- [ ] **Retrieval eval.** Label the expected docs / known issue for each seed ticket; report hit@3 for keyword, vector, and hybrid. *Done when:* the numbers are in the build log and ADR-013's last consequence is resolved.
- [ ] **`search_kb` / `search_similar_tickets` tools.** Wrap the retrieval service for the Phase 3 agent loop. *Done when:* the tools return cited snippets with the same threshold.

## Phase 3: Agent loop with read-only tools (draft only)

- [~] **Claude client wrapper.** Model ids from config, retries, token and cost accounting into the trace. *(Phase 2 Step 1: model from config, SDK retries, token usage in state. Cost accounting and trace still to do.)* *Done when:* a unit test with a stubbed client records usage.
- [ ] **Tool schema registry.** Pydantic models → tool definitions, with dispatch and validation of tool inputs. *Done when:* invalid tool input is rejected with a structured error returned to the model.
- [x] **Untrusted-content wrapper.** Ticket and KB text are delimited and labeled as data in prompts. *(Ticket text in Phase 2 Step 1; retrieved documents and rerank candidates in Step 2.)* *Done when:* unit test verifies the wrapping and escaping of delimiter look-alikes.
- [ ] **Triage step (small model).** Structured output: category, confidence, missing fields, reasoning summary. *Done when:* it classifies 5 sample tickets correctly in a smoke test.
- [ ] **Reasoning loop (strong model).** Tool-use loop with read-only tools and step, token, and time budgets. *Done when:* it runs to completion on a how-to ticket and stops at the budget on a looping stub.
- [ ] **Draft-only disposition.** The agent outputs a proposed plan and draft reply to the trace with no writes. *Done when:* `support-agent run INC0010001 --dry-run` prints a cited draft.
- [~] **Prompt files.** System and step prompts live in versioned files, not inline strings. *(Triage prompt done in Phase 2 Step 1.)* *Done when:* prompts load from `src/support_agent/prompts/`.

## Phase 4: Write tools + human approval

- [ ] **Policy engine.** Declarative policy mapping each action to auto, approval, conditional, or forbidden. *Done when:* unit tests cover every row of the autonomy table.
- [~] **Kill switch.** Env plus runtime flag checked before every write. *(Env flag `AGENT_KILL_SWITCH` blocks work-note writes since Phase 2 Step 1. Runtime flag still to do.)* *Done when:* a test shows writes are blocked and reads continue while it's on.
- [ ] **Idempotency keys.** Store a key before executing a write and skip on duplicates. *Done when:* replaying a run produces no duplicate side effects in tests.
- [~] **`add_work_note` tool.** *(Client method, dedupe, and verification built in Phase 2 Step 1; live write and dedupe validated 2026-11-05. Tool wrapper for the agent loop still to do.)* *Done when:* a work note appears on a PDI ticket and a repeat call is deduplicated.
- [ ] **`update_state` tool.** Only allowed transitions. Resolve and close are refused. *Done when:* a test confirms close is refused, with a policy reason in the trace.
- [ ] **`draft_reply` → pending approval.** Drafts go to the approvals table, not to ServiceNow. *Done when:* the draft is stored with full context.
- [ ] **Review UI: list and detail.** A local page showing pending actions, the ticket, the trace, and citations. *Done when:* pending drafts are visible in the browser.
- [ ] **Review UI: approve, edit, reject.** On approval, the policy engine runs the action, which posts the customer comment. *Done when:* an approved reply appears in ServiceNow exactly once.
- [ ] **Audit trail.** Approver, timestamp, and the original vs. edited text are recorded. *Done when:* this is visible in the trace and the DB.
- [ ] **Missing-info flow end to end.** *Done when:* a vague ticket gets an approved clarifying question and state `On Hold`.
- [ ] **How-to flow end to end.** *Done when:* a how-to ticket gets an approved, KB-cited reply.

## Phase 5: ERPNext demo system + CDP reproduction

- [ ] **Pick the ERPNext version.** Check the fix commits of the seed bugs and choose the version that reproduces the most. Record it as an ADR. *Done when:* the version and the reproducible bug list are in `demo_app/BUGS.md`.
- [ ] **ERPNext in Docker.** Compose file based on `frappe_docker` with the pinned version. *Done when:* ERPNext runs locally and you can log in.
- [ ] **ERPNext accounts.** `repro_bot` (UI, Accounts User) and `erp_fixtures` (API key). No Administrator use by the agent. *Done when:* both work and their credentials are in `.env`.
- [ ] **Fixtures.** Brightline Supplies company, currencies (INR/USD/KWD), suppliers, accounts, tax templates via REST. *Done when:* the fixture script is idempotent on a fresh site.
- [ ] **Manual repro of 2 seed bugs.** *Done when:* the steps and screenshots are documented in `demo_app/BUGS.md`.
- [ ] **`prepare_repro_data` tool.** Creates per-run, uniquely named test records via REST. *Done when:* a scenario's preconditions are created and visible in ERPNext.
- [ ] **CDP connection.** Launch Chrome with a dedicated `--user-data-dir` and connect over the DevTools websocket. *Done when:* the script opens ERPNext, logs in as `repro_bot`, and takes a screenshot.
- [ ] **Allow-list enforcement.** A request interceptor blocks non-allow-listed origins, and navigation targets are validated. *Done when:* a test shows an external URL is blocked and logged.
- [ ] **Repro action primitives.** navigate, click, type, wait_for, screenshot, read error dialogs, collect console and network errors (Frappe 417 messages). *Done when:* each primitive has a test against ERPNext.
- [ ] **`reproduce_in_browser` tool.** Takes structured steps and expected behavior, and returns reproduced status, observations, and evidence paths. *Done when:* it reproduces a seed bug (e.g. INC0010006) with a screenshot and error text in the trace.
- [ ] **Jira client + `create_jira_issue`.** Steps, expected vs. actual, environment, and an attached screenshot. *Done when:* an issue is created in `SUP` with the attachment.
- [ ] **Jira dedupe.** Search for an existing issue (by label or ticket link) before creating one. *Done when:* a second run updates or comments on the existing issue instead of creating one.
- [ ] **`link_jira` tool.** Writes the Jira key and URL to the incident (field or work note). *Done when:* the incident shows the link.
- [ ] **Confidence-gated Jira creation.** *Done when:* high-confidence repros auto-create and low-confidence ones go to approval.
- [ ] **Bug flow end to end.** *Done when:* a bug ticket gets a repro, a Jira issue, a link, a work note, and a pending reply, in one run.
- [ ] **Non-reproducible path.** *Done when:* a failed repro produces a work note with the attempts and routes to missing-info.

## Phase 6: Evals + guardrail / injection tests

- [ ] **Golden ticket format.** YAML schema with input, expected category, KB id, escalation, and forbidden actions. *Done when:* the schema is validated by a test.
- [ ] **Golden set: 20–30 tickets.** Covers KB answer, needs info, real bug, non-repro bug, duplicate, and prompt injection. *Done when:* the files are committed in `evals/golden/`.
- [ ] **Mock ServiceNow and Jira backends.** In-memory fakes behind the same client interface. *Done when:* the agent runs a golden ticket fully offline.
- [ ] **Eval runner.** Runs all golden tickets and writes per-ticket results and traces. *Done when:* `python -m evals.run` completes.
- [ ] **Metrics and report.** Routing accuracy, KB match, correct escalation, and unsafe action count, written as a Markdown report. *Done when:* `evals/report.md` is generated.
- [ ] **Safety gate.** Fail the run if unsafe actions > 0. *Done when:* a deliberately unsafe stub fails the gate.
- [ ] **Prompt-injection suite.** Instructions in the ticket body, fake system messages, URL lures, "close this ticket", data exfiltration requests. *Done when:* all cases pass with zero unsafe actions.
- [ ] **Injection heuristic screen.** Flags suspicious tickets for human review. *Done when:* injection golden tickets are flagged and normal tickets aren't (precision noted in the report).
- [ ] **Threshold tuning.** Calibrate KB relevance and Jira confidence thresholds on the golden set. *Done when:* the chosen values and their rationale are recorded in `DECISIONS.md`.

## Phase 7: Docker Compose, CI, README polish

- [ ] **Agent Dockerfile.** Slim Python 3.11 image running as a non-root user. *Done when:* `docker build` succeeds and the CLI runs in the container.
- [ ] **ERPNext in the main Compose stack.** *Done when:* `docker compose up` starts ERPNext with fixtures loaded.
- [ ] **Browser container.** Headless Chrome exposing CDP on the internal network only. *Done when:* the agent container can connect to it and the host can't, other than through mapped debug ports.
- [ ] **docker-compose.yml.** agent, review-ui, browser, and demo-app, with health checks. *Done when:* `docker compose up` brings up the full stack with one command.
- [ ] **Offline demo mode.** Compose profile using mocked ServiceNow and Jira so reviewers can run without accounts. *Done when:* `docker compose --profile demo up` processes sample tickets end to end.
- [ ] **GitHub Actions CI.** ruff, mypy, pytest, and the eval safety gate on mocks. *Done when:* CI is green on a PR.
- [ ] **Secret scanning.** Pre-commit hook plus CI check (e.g. gitleaks). *Done when:* a planted fake secret fails the check.
- [ ] **README quickstart.** Real commands, prerequisites, and troubleshooting. *Done when:* a fresh clone works by following the README.
- [ ] **README results section.** Eval metrics table and a sample trace excerpt. *Done when:* the numbers are from a real eval run.
- [ ] **Architecture doc refresh.** Update the docs to match what was actually built. *Done when:* there are no stale statements in `ARCHITECTURE.md`.

## Phase 8: Demo video + LinkedIn post

- [ ] **Demo script.** A 2–3 minute storyline: queue → how-to → bug repro → Jira → approval → injection blocked. *Done when:* the script is in `docs/demo-script.md`.
- [ ] **Record demo.** Screen recording with narration or captions. *Done when:* the video is uploaded (YouTube / Loom).
- [ ] **README GIF.** A short GIF of the bug-repro flow at the top of the README. *Done when:* the GIF renders on GitHub.
- [ ] **Public repo hygiene.** Noreply email, history check for secrets, topics, description, social preview image. *Done when:* the repo is public and the checklist is complete.
- [ ] **LinkedIn post.** Problem, approach, guardrails, results, and links. *Done when:* the post is published.

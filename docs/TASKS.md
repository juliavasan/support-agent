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
- [ ] **Anthropic API key.** Create a key with a spend limit. *Done when:* a one-line test call returns a response.
- [ ] **Local `.env`.** Copy `.env.example` and fill in values. *Done when:* `.env` is populated and `git status` doesn't show it.
- [ ] **Python tooling.** Set up a virtualenv / `uv`, ruff, mypy, pytest, and pre-commit. *Done when:* `ruff check`, `mypy`, and `pytest` all run (with zero tests) and pass.
- [ ] **Config loader.** Typed settings from env with validation that fails fast on missing vars. *Done when:* a unit test covers missing and valid config.
- [ ] **Package layout decision.** Confirm the import paths for `src/` modules in `pyproject.toml`. *Done when:* `python -c "import agent, tools, guardrails"` works in the venv.

## Phase 1: Read the queue (no AI)

- [ ] **ServiceNow client: auth and base request.** Use HTTPS, timeouts, and retries with backoff on 429/5xx. *Done when:* unit test with mocked HTTP passes.
- [ ] **`get_ticket` tool.** Fetch an incident by number into a typed `Ticket` model. *Done when:* returns a parsed model for a seeded incident. Unit test uses a recorded fixture.
- [ ] **List queue.** Query incidents for the agent's assignment group, filtered by state and updated-since. *Done when:* CLI prints the open queue.
- [ ] **Poll cursor and state store.** SQLite table for last-seen `sys_updated_on` / `sys_mod_count`. *Done when:* a re-run doesn't reprocess unchanged tickets.
- [ ] **Synthetic ticket seeder.** Script that creates 10+ synthetic incidents in the PDI from YAML. *Done when:* `python -m scripts.seed` creates the tickets and is idempotent.
- [ ] **Trace writer skeleton.** Per-ticket `trace.json` with run id and events. *Done when:* the poller writes a trace for each ticket it sees.
- [ ] **CLI entrypoint.** `support-agent poll --once`. *Done when:* the command lists new tickets and writes traces.

## Phase 2: KB search

- [ ] **Seed synthetic KB articles.** 10–15 articles for the demo portal (login, billing, export, etc.). *Done when:* the articles are published in the PDI KB.
- [ ] **`search_kb` tool (API search).** Query `kb_knowledge` with text search and return top-k with ids and snippets. *Done when:* a known query returns the expected article.
- [ ] **Local KB index (optional upgrade).** Embedding or BM25 index synced from ServiceNow for better recall. *Done when:* recall on a small query set beats API search.
- [ ] **`search_similar_tickets` tool.** Find resolved or open incidents similar to a query. *Done when:* a seeded duplicate is returned for its original.
- [ ] **Relevance threshold.** Results below the configured score are dropped. *Done when:* an unrelated query returns an empty result in tests.

## Phase 3: Agent loop with read-only tools (draft only)

- [ ] **Claude client wrapper.** Model ids from config, retries, token and cost accounting into the trace. *Done when:* a unit test with a stubbed client records usage.
- [ ] **Tool schema registry.** Pydantic models → tool definitions, with dispatch and validation of tool inputs. *Done when:* invalid tool input is rejected with a structured error returned to the model.
- [ ] **Untrusted-content wrapper.** Ticket and KB text are delimited and labeled as data in prompts. *Done when:* unit test verifies the wrapping and escaping of delimiter look-alikes.
- [ ] **Triage step (small model).** Structured output: category, confidence, missing fields, reasoning summary. *Done when:* it classifies 5 sample tickets correctly in a smoke test.
- [ ] **Reasoning loop (strong model).** Tool-use loop with read-only tools and step, token, and time budgets. *Done when:* it runs to completion on a how-to ticket and stops at the budget on a looping stub.
- [ ] **Draft-only disposition.** The agent outputs a proposed plan and draft reply to the trace with no writes. *Done when:* `support-agent run INC0010001 --dry-run` prints a cited draft.
- [ ] **Prompt files.** System and step prompts live in versioned files, not inline strings. *Done when:* prompts load from `src/agent/prompts/`.

## Phase 4: Write tools + human approval

- [ ] **Policy engine.** Declarative policy mapping each action to auto, approval, conditional, or forbidden. *Done when:* unit tests cover every row of the autonomy table.
- [ ] **Kill switch.** Env plus runtime flag checked before every write. *Done when:* a test shows writes are blocked and reads continue while it's on.
- [ ] **Idempotency keys.** Store a key before executing a write and skip on duplicates. *Done when:* replaying a run produces no duplicate side effects in tests.
- [ ] **`add_work_note` tool.** *Done when:* a work note appears on a PDI ticket and a repeat call is deduplicated.
- [ ] **`update_state` tool.** Only allowed transitions. Resolve and close are refused. *Done when:* a test confirms close is refused, with a policy reason in the trace.
- [ ] **`draft_reply` → pending approval.** Drafts go to the approvals table, not to ServiceNow. *Done when:* the draft is stored with full context.
- [ ] **Review UI: list and detail.** A local page showing pending actions, the ticket, the trace, and citations. *Done when:* pending drafts are visible in the browser.
- [ ] **Review UI: approve, edit, reject.** On approval, the policy engine runs the action, which posts the customer comment. *Done when:* an approved reply appears in ServiceNow exactly once.
- [ ] **Audit trail.** Approver, timestamp, and the original vs. edited text are recorded. *Done when:* this is visible in the trace and the DB.
- [ ] **Missing-info flow end to end.** *Done when:* a vague ticket gets an approved clarifying question and state `On Hold`.
- [ ] **How-to flow end to end.** *Done when:* a how-to ticket gets an approved, KB-cited reply.

## Phase 5: Demo app + CDP reproduction

- [ ] **Demo customer portal scaffold.** A minimal web app (login, dashboard, form) with synthetic users. *Done when:* it runs locally on a fixed port.
- [ ] **Seeded bug #1.** For example, the "Save" button does nothing on the profile page and logs a console error. *Done when:* the bug is visible manually and documented in `demo_app/BUGS.md`.
- [ ] **Seeded bug #2.** For example, the export returns an empty file for date ranges over 30 days. *Done when:* the bug is visible and documented.
- [ ] **Seeded bug #3 (optional).** For example, a layout overlap hides the submit button at narrow widths. *Done when:* the bug is visible and documented.
- [ ] **CDP connection.** Launch Chrome with a dedicated `--user-data-dir` and connect over the DevTools websocket. *Done when:* the script opens the demo app and takes a screenshot.
- [ ] **Allow-list enforcement.** A request interceptor blocks non-allow-listed origins, and navigation targets are validated. *Done when:* a test shows an external URL is blocked and logged.
- [ ] **Repro action primitives.** navigate, click, type, wait_for, screenshot, collect console and network errors. *Done when:* each primitive has a test against the demo app.
- [ ] **`reproduce_in_browser` tool.** Takes structured steps and expected behavior, and returns reproduced status, observations, and evidence paths. *Done when:* it reproduces bug #1 with a screenshot in the trace.
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
- [ ] **Demo app Dockerfile.** *Done when:* the container serves the portal.
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

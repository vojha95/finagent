# Phase 9 plan: serving, deployment, dashboard, outside user, E6 and E7 (plan only)

Blueprint sections 15 (Phase 9), 11 (deployment and observability), 10.5 (E6), 10.7 (E7), 8.17
(trace), 4.2 (checklist) and BC7. Written 2026-11-26. **Nothing here is built, and no account
or spending has been created.** The decisions Vineet needs are numbered in section 9.

## Cut-down plan (2026-11-26; BC33): this section overrides the rest of the plan

Vineet's decision: about 8 hours remain for the whole project, so Phase 9 becomes a showcase of
what is already built. Where sections 1-10 below differ, this section applies; they are kept as
the record of the full plan.

- **Kept:**
  - **The Streamlit app,** `finagent/ui/app.py` (`streamlit run finagent/ui/app.py`). It is
    local and offline, calls the library directly, and serves the 8 validation companies only.
    It has three pages:
    - Ask: investigate on the selected company;
    - Company: statements with forecast, F1-F5 checks, credit and liquidity, the workbook and the
      insight cards, all from the latest pinned runs;
    - Results: the dashboard on real runs, with E7 and the limitations.
  - **A light E7** (`eval/e7_light.py`, e7-0001): 20 fixed E5 canonical questions, measuring
    p50/p95 latency, failures, and cold vs warm start.
  - **A Dockerfile, shipped unbuilt.** There is no Docker on the development Mac. It lints
    clean against hadolint's rules, checked by hand and by `tests/test_dockerfile.py`.
  - **The backup recording,** done by Vineet.
- **Cut,** each logged as a limitation (docs/limitations.md):

  | Cut | Issue |
  |---|---|
  | FastAPI | #316 |
  | E6 | #317 |
  | The full E7 | #318 |
  | The outside-user session | #319 |
  | Persistent traces | #320 |
  | The evidence-replay page | #321 |
  | The public deployment: Hugging Face now requires a paid PRO plan for Docker Spaces, and Vineet chose not to pay | #322 |

- **The Phase 9 exit is replaced by:** a local demo on real runs, e7-0001, the unbuilt
  Dockerfile and the limitations list.

**The 12 decisions of section 9, as answered by the cut-down:**

| # | Decision | Answer |
|---|---|---|
| D1 | Host | None: the demo runs locally. The public deployment was cut (#322) |
| D2 | Companies served | The 8 validation companies only. Held-out and train are refused |
| D3 | Publishing | Nothing is published: no Space. *Follows from D1; to confirm* |
| D4 | Image | The torch image (CPU torch; evidence v2 works) |
| D5 | API exposure | No public API. No FastAPI at all: the app calls the library directly |
| D6 | as_of in the UI | A date picker. The default is 2026-11-24 and the maximum is the evidence snapshot date. The manifest records no date, so the date is set in configs/ui.yaml (#323) |
| D7 | Traces | Exported on request (a download on the Ask page), never persisted |
| D8 | E6 | Cut |
| D9 | Outside user | None |
| D10 | E7 comparison (manual build time) | Not measured: the full E7 was cut. *Follows from the cut; to confirm* |
| D11 | Spending cap | No spending |
| D12 | Ownership | One builder. Vineet records the backup video. *To confirm* |

**Original Phase 9 exit (blueprint 15):** a public URL, a dashboard on real runs, and outside-user
feedback recorded.

**Calendar:**
- ML for Finance exam: 4 December. Final: 5 December.
- Phase 10 (freeze, recording, deck, viva) needs about a day.
- So Phase 9 has about five working days (26 November to 2 December).

## 1. What ships, and what does not

| Ships | Does not ship |
|---|---|
| M1 v3 (`m1-tfidf-lr-v3`, sha256 `88da60a9…`) with its shipped temperature: the document channel (#308) | M3, M2 and every calibrator: not wired in (#308; pre-registered rule) |
| Intent compiler v3 (`intent_v3.joblib`) | The E4 NLI judge (evaluation only, 281 MB) |
| Evidence v2: bge-small-en-v1.5 (dense) and ms-marco-MiniLM-L-6-v2 (reranker), pinned by revision and sha256 (configs/models.yaml), with the D-EV2 low-confidence display | The optional 4-bit Qwen LLM fallback: never built (cut order 15.1 item 1) |
| The pinned evidence store (78 MB, manifest sha256 `89ee4161…`), read-only | Held-out companies: refused by every tool (HeldOutError), as now |
| configs/, `datasets/rows_v3.meta.json` (the M1 loader checks it) | runs/, gold/, eval/, notebooks, the dataset parquet |

- **No network at run time.** Evidence is pinned and the models load offline
  (`HF_HUB_OFFLINE=1`). The service never contacts the SEC.
- A company outside the pinned store is answered as unsupported. Fetching new filings stays an
  offline, pinned step (blueprint 8.1).

## 2. API surface (FastAPI; `finagent/api/`)

**Rules for every endpoint:**
- `as_of` is required: there is no server default. It is checked by `check_as_of` and echoed in
  every response.
- Every response carries `git_commit`, `evidence_manifest_sha256` and the model versions.
- Request and response bodies are pydantic models of the blueprint 13 records.
- A held-out ticker gets 403 with the HeldOutError reason.
- An unknown ticker, metric or period gets the tool's own GAP or INVALID outcome, never an
  HTTP error.

**Every tool call passes the observation gate.** A tool endpoint builds the goal its arguments
imply and gates the outcome exactly as the planner does: pinned-chunk quote check #157, as_of
rule, classification and units. No value reaches a user ungated. The D-EV2 low-confidence
sentence is returned in its own field, `low_confidence`, never in `quotes`.

| Endpoint | Body or query (all with `as_of`) | Calls | Returns |
|---|---|---|---|
| `GET /health` | — | — | Liveness; git commit; manifest sha256; M1 v3, intent v3, embedder and reranker pins; signals.yaml and query-template sha256; evidence version |
| `GET /companies` | `as_of` | `pinned_tickers`, `filings` | Served tickers (section 9, D2), each with its fiscal years filed by `as_of` |
| `POST /investigate` | `question`, `as_of`, optional `issuer` (the selected company) | `investigate(question, as_of, selected_issuer=...)` | run_id, status, stop_reason, answer text, goals (status, reason, gated observations with provenance, quotes and low_confidence), trace steps. Written to trace.jsonl and trace.sqlite |
| `POST /tools/fact` | issuer, period, metric | `reported_verified`, then the planner's fallbacks (tag-only, document-only, BC25) | Outcome |
| `POST /tools/statements` | issuer, period | `reconcile` | Outcome (I9/I10 final check, BC22/BC24) |
| `POST /tools/forecast` | issuer, period, scenario, target | `project` | Outcome (CALCULATION_FROM_MODEL_ASSUMPTION) |
| `POST /tools/liquidity` | issuer, period, scenario, target | `liquidity` | Outcome |
| `POST /tools/credit` | issuer, period | `credit_ratios` | Outcome |
| `POST /tools/debt_service` | issuer, period, scenario, target | `debt_service_scoped` | Outcome (withheld measure shown, 8.12) |
| `POST /tools/risk` | issuer, period | `risk_signals` | Outcome (fired measures, quotes or EVIDENCE_GAP with low_confidence) |
| `POST /tools/explain` | issuer, period, metric | `explain_movement` | Outcome (never uses a low-confidence sentence) |
| `POST /tools/cards` | issuer, year | `insight_cards` | Signals and InsightCards (status, numbers, quotes, low_confidence) |
| `POST /analyze` | issuer | statement set, forecast for every scenario, credit and liquidity, cards for every year, workbook | run_id, at once; the work runs as a background job |
| `GET /runs/{id}` | — | — | Job status, then statements, verification report, forecast, scenarios, credit, insight cards and per-stage timings |
| `GET /runs/{id}/workbook` | — | `build_workbook` | The linked .xlsx |
| `GET /metrics` | optional `since` | trace.sqlite and the request log | The dashboard aggregates (section 3, page 7) |
| `POST /feedback` | run_id, session tag, correct (yes/no/unsure), useful (1-5), comment | — | Stored in a `feedback` table beside the traces (section 6) |

**Concurrency, at demo scale:**
- One uvicorn worker. Tool calls run behind one lock, because the tools' caches are not
  thread-safe.
- `/analyze` jobs run one at a time in a background thread, polled through `/runs/{id}`.
- Caches are keyed by (issuer, as_of), as now (8.17).
- Each request has a time limit. Questions are capped in length.

**Tests:** FastAPI TestClient over the synthetic fixtures already used by tests/test_tools.py,
plus one held-out refusal test, one missing-`as_of` test (422) and one gate test per tool
endpoint.

## 3. Streamlit app (`finagent/ui/`; it calls the API on localhost)

| Page | Contents |
|---|---|
| 1. Ask FinAgent | Company selector (`issuer`), as-of date picker (defaults to 2026-11-24, maximum the manifest's date), question box. Shows the answer text, a goal table (status, reason) and an observation table (value, unit, classification, flags, 10-K link with filing date). Quotes appear under their value. An EVIDENCE_GAP shows its gap line, then the low-confidence sentence (section 5). A trace expander shows steps, tools, eligible tools, decision reasons, gate decisions and latency. Thumbs and comment for feedback |
| 2. Statements and verification | Per company and year: the three statements with each line's status (VERIFIED, single-channel flag, GAP, CONFLICT), channels, Correlation Agent edits (CORRECTED, REVIEW), identity results I1-I11 and the final I9/I10 check |
| 3. Forecast and scenarios | Base, Moderate and Severe projections; drivers (MODEL_ASSUMPTION, with basis); model checks per year and scenario; liquidity headroom and scoped debt service with withheld measures |
| 4. Workbook | One button: `/runs/{id}/workbook`, the linked Excel model (BC21 layout), with its status banner |
| 5. Risk insights | Cards per year: severity, the fired numbers, status. A quoted card shows its quotes with section and chunk. An EVIDENCE_GAP card shows the gap text, then the low-confidence sentence. An R10 card is labelled model-derived (#180) |
| 6. Evidence replay (blueprint 11.2; optional, cut first) | Pick a condition (complete; tag primary hidden; both tag sources hidden; document hidden) and re-run one goal: the E8 path |
| 7. Operations dashboard | The 11.4 panels on real runs (E7 runs, the outside-user session and demo traffic): requests per day, outside-user sessions, p50/p95 latency per tool and per planner step, error and INVALID rates per tool, dual-channel agreement and CORRECTED/REVIEW shares, run outcomes, steps per investigation and step-limit stops. Also peak memory, hosting cost per month, and external API cost (0). The 11.4 alert banner shows when a tool's error rate is above 10% over 20 runs, or agreement is more than 5 points below the validation baseline |

**The low-confidence display (D-EV2), everywhere a card appears:**
- It is a grey, indented block under the EVIDENCE_GAP line, never in the quote style.
- It is headed by its label, "Low confidence: may not explain the movement", followed by
  "not evidence".
- It shows the passage's reranker score against the threshold (5.263).
- It never appears on a quoted card or a model-derived card.

## 4. Docker packaging

**Image:** one image, `python:3.11-slim`, linux/amd64 for the host and linux/arm64 for local
Macs. Two processes:
- uvicorn on 127.0.0.1:8000;
- Streamlit on the public port (7860 on Hugging Face Spaces).

A small entrypoint starts both. A `HEALTHCHECK` calls `/health`.

**Build steps:**
1. **Dependencies:** a Linux lock file with hashes (`pip-compile --generate-hashes`), installed
   with `--require-hashes`. torch comes from the CPU wheel index, pinned by version and hash.
   The default Linux torch wheel pulls about 2.5-3 GB of NVIDIA libraries the service never
   uses.
2. **Models:**
   - M1 v3 and intent v3, with their meta files, are copied in.
   - The image refuses to build unless their sha256 equals the values in their meta files and
     in docs/heldout_e1_prereg.md (M1 v3 `88da60a9…`).
   - bge-small and the reranker are downloaded at build time by
     `python -m finagent.retrieval.models download`, then `verify`. Each file is checked against
     configs/models.yaml (revision, size and sha256), and any mismatch fails the build.
3. **Evidence:** `evidence/` is copied in, made read-only (`chmod -R a-w`) and owned by root.
   The service runs as a non-root user. The store keeps checking hashes on every read (8.1), and
   `/health` reports the manifest sha256.
4. **Caches:** `derived/embeddings` (17 MB, keyed by model fingerprint and chunk sha256) is
   copied in, so chunks are not re-embedded at run time. Statement and forecast caches are built
   at run time per (issuer, as_of).
5. **Runtime settings:** `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. Traces go to a
   writable `/data` directory.

**Image size (uncompressed, estimated from this machine's measured installs):**

| Layer | Without torch | With torch (CPU wheel) |
|---|---|---|
| python:3.11-slim | ~0.13 GB | ~0.13 GB |
| Core, ML and serving packages: pandas 83 MB, pyarrow 131, scipy 100, scikit-learn 49, numpy 37, lxml 20, streamlit and its dependencies about 120, FastAPI, uvicorn, pydantic, openpyxl, rank-bm25, lightgbm | ~0.6 GB | ~0.6 GB |
| torch (631 MB measured), transformers (124), tokenizers, sentence-transformers, huggingface_hub | — | ~0.8 GB |
| Models: M1 v3 and intent v3 (6 MB); bge-small (128 MB) and MiniLM reranker (88 MB) | ~0.01 GB | ~0.22 GB |
| Evidence (78 MB), rows_v3 meta and embedding cache (17 MB) | ~0.1 GB | ~0.1 GB |
| **Total** | **~0.85 GB** (about 0.35 GB compressed) | **~1.85 GB** (about 0.75 GB compressed) |

**What changes without torch:**
- M1 v3 ships either way: it is scikit-learn.
- Evidence v2, the configured default, needs torch: dense retrieval embeds the query, and the
  reranker scores query-passage pairs. A slim image would therefore have to do one of these:
  - serve evidence v1 (BM25), which e4-0001 found unsupported on 19 of 27 quoted cards;
  - precompute v2 retrieval for the pinned filings. The query templates are frozen, so this is
    possible, but it is a design change.
- **Recommendation: the torch image.** About 1.9 GB fits every option in section 5.

**Peak memory:** not measured yet. Expected about 1.5-3 GB, with both small models loaded and
one company's statement set and indexes. E7 measures it (section 7).

**Fallback:** a local `docker compose up` with the same image. It needs no network, because the
evidence is pinned. This is the backup demo (blueprint 16, 17.3).

## 5. Deployment options and costs

Prices were checked on 2026-11-26 from the providers' pages and recent summaries (sources at the
end). Re-check them at sign-up. Nothing has been created.

| Option | Monthly cost | Fit | Vineet must |
|---|---|---|---|
| **A. Hugging Face Spaces, Docker, CPU Basic (recommended)** | Free | Public URL; Docker Spaces; port 7860. Resources: 2 vCPU and 16 GB RAM as last documented (to confirm). Sleeps when idle, so the first request after a sleep waits for a restart. The disk is not persistent: traces are lost on a restart (decision D7) | Create a Hugging Face account (or an organisation for the team) and the Space. A free Space is public, so its repo (code, configs, evidence snapshot, M1 v3 joblib) is public (decision D3). Add the team as members |
| A + persistent storage | Paid add-on; the current per-tier price was not confirmed (check the pricing page) | Keeps trace.sqlite and feedback across restarts | Approve the spend |
| A + PRO plan | $9 per month | Not needed for a public Space | — |
| B. Railway, Hobby | $5 per month including $5 of usage, then about $10 per GB of RAM and $20 per vCPU per month, by the minute. A 2 GB / 1 vCPU service always on is about $40 per month | Docker; volumes for traces | Create an account, add a card, approve the spend |
| C. Fly.io, shared-cpu-1x with 2 GB | About $12.70 per month, plus a small volume | Docker; volumes | Create an account, add a card, approve the spend |
| D. Render, Standard (1 CPU, 2 GB) | About $25 per month | Docker; disk add-on | Create an account, add a card, approve the spend |
| E. Local docker compose | Free | The offline backup; always kept | — |

- Every paid option costs well under the ₹40,000 team budget for one month.
- Option A costs nothing, matches blueprint 11.3 and D2, and has the most memory. Its costs are
  public code and data, ephemeral traces, and restarts after sleep.
- **The demo plan:**
  1. Wake the Space 10 minutes before the slot.
  2. Snapshot the traces after the E7 runs and the outside-user session (decision D7).
  3. Keep option E and the recording as backups.

## 6. The outside-user session, and how feedback is recorded

- **Who:** one or two finance professionals outside the team. Vineet recruits them (D9). Before
  the session they consent to what is recorded: a pseudonym only, their questions, their
  answers and their notes.
- **When:** after the deployment passes E7's smoke run, on the public URL.
- **Format:** about 35 minutes, in person or by screen share, thinking aloud, with one team
  member taking notes. The tasks are written beforehand and cover every page:
  1. Ask a fact for a company and year.
  2. Ask why a metric moved.
  3. Check one statement's verification.
  4. Run the Severe scenario and read liquidity headroom.
  5. Read the risk cards. Judge one quoted card and one low-confidence sentence: does the
     sentence explain the movement, and is the label clear?
  6. Download the workbook and open it.
  7. Ask one question the system does not support.
- **Recording:**
  - Each answer has a feedback widget: correct (yes / no / unsure), useful (1-5) and a comment.
    It is stored through `POST /feedback` in a `feedback` table keyed by run_id and a session
    tag (`?session=outside-1`), so the dashboard counts outside-user sessions apart from the
    team's.
  - A five-question form follows the session: the task that worked best, the one that failed,
    trust in the numbers, whether the evidence and gaps were clear, and what is missing.
  - Each session's traces are exported (D7).
- **Written up** in `docs/outside_user.md`:
  - tasks completed, with their run_ids;
  - every feedback row;
  - the form answers;
  - the observer's notes.
- Any problem met is logged in docs/issues_log.md. **Nothing is fixed during the session.** A
  fix afterwards is a normal change, with its own tests and runs.

## 7. E7: operations (blueprint 10.7)

- **Workload:** fixed and written down before the run.
  - (a) The E5 questions on validation companies, through `/investigate`. Answers are not
    scored here: E5 did that.
  - (b) `/analyze` for each of the 8 validation companies on cold caches: minutes per company.
  - (c) The same requests again, three times, warm.
- **Where:** the deployed Space (primary) and local Docker (reference). Results are recorded in
  experiments.csv as e7-0001 with commit and manifest hash.
- **Latency:** p50 and p95 per tool and per planner step (trace `latency_ms`, which exists), per
  `/analyze` stage (statement set, forecast per scenario, credit and liquidity, cards, workbook)
  and end to end per question. Cold-start time after a Space wake is measured too.
- **Failure rates:**
  - per tool and per stage: exceptions, HTTP 5xx and timeouts;
  - outcome shares: GAP, INVALID and CONFLICT per tool, and planner stop reasons, including
    step-limit stops.
  - A failure is logged with its error class, never hidden. The alert rules are those of
    blueprint 11.4.
- **Logging:**
  - trace.jsonl and trace.sqlite (8.17), as now;
  - a JSON-lines request log: request id, endpoint, issuer, as_of, latency, HTTP status, error
    class. No question text is kept unless the user is in a feedback session.
- **Resources:**
  - peak memory, from the cgroup's `memory.peak` in the container and `ru_maxrss` in the
    process;
  - image size; hosting cost per month (section 5);
  - external API cost 0. The container runs with no outbound network in the local reference
    run, which shows it.
- **Comparison:** manual build time. Vineet's own time to build one company's three-statement
  model from its 10-K, measured or estimated with its basis (D10), against FinAgent's cold
  minutes per company.

## 8. E6: point-in-time backtest (blueprint 10.5, BC7)

- **Question:** is the Base forecast one year ahead better than the naive last value?
- **Split:**
  - **The 8 validation companies**, as the blueprint says, for the reported run.
  - Train companies for building and debugging the harness only.
  - **Held-out: never** (#308, D-HO).
- **Origins:**
  - Each 10-K filing date d(t) of fiscal year t for which FY t+1's 10-K is pinned.
  - At least three fiscal years at or before t must be readable as of d(t). Earlier years come
    from Company Facts filtered by filing date at or before d(t) (BC7).
  - With FY2021-FY2025 pinned (FY2022-FY2026 for June and May year-ends), that is up to 4 origins
    per company and up to 32 company-origins per metric.
- **Point in time:**
  - Every tool call uses `as_of = d(t)`, so later filings are invisible (the store enforces it).
  - Drivers are fitted on years up to t only. Nothing is tuned on E6 results.
- **Forecast:** the shipped system (M1 v3 document channel, the validated statement set, Base
  scenario), one year ahead.
- **Actuals:**
  - The FY t+1 value as first reported, read as of FY t+1's own filing date. This avoids
    restatement lookahead.
  - VERIFIED values only. A missing actual is excluded and counted, never zero.
- **Metrics:** revenue, operating income, CFO, capex, and FCF (CFO − capex).
  - Filers with no operating-income line (#68) use the flagged EBIT proxy, or are excluded (D8).
  - Absolute error in USD and absolute percentage error per company-origin, for the model and
    for naive.
  - MAE and MAPE per metric. APE is undefined when the actual is 0; such points are excluded and
    counted. FCF near zero makes MAPE unstable, so the MAE ratio (model over naive) is reported
    too.
- **Decision rule, pre-registered before the validation run** (docs/e6_prereg.md, committed
  first, as for held-out E1):
  - For each metric, the model beats naive only if the 95% paired company-bootstrap interval of
    mean(naive APE − model APE) lies entirely above 0 (2,000 draws, seed 0).
  - Otherwise no accuracy claim is made for that metric (blueprint 10.5). Win counts are
    reported, whatever they show.
- **Known work:** forecasting from an origin whose pinned history is short needs the BC7 Company
  Facts history wired into the driver fit. This is the largest unknown in Phase 9 (section 10).

## 9. Decisions needed from Vineet

1. **Host:** A, Hugging Face Spaces CPU Basic (free; recommended), or B, C or D (paid; section 5).
   You create the account and the Space. I create nothing.
2. **Companies served:**
   - the 8 validation companies only (recommended: what E1, E2, E5 and E8 measured);
   - or validation plus train, with train labelled "in-sample for M1".
   - Held-out is refused either way.
3. **Publishing:** a free Space is public. Is it OK to publish the code, configs, the evidence
   snapshot (public SEC data) and the M1 v3 and intent v3 files?
4. **Torch image** (about 1.9 GB, evidence v2 works; recommended), or a slim image (about
   0.85 GB) with evidence v1 or precomputed v2 retrieval (a design change).
5. **API exposure:** Streamlit public with FastAPI internal only (recommended), or FastAPI also
   public behind a reverse proxy (about 2 more hours).
6. **as_of in the UI:** a free date picker with a maximum of the manifest's date and a default of
   2026-11-24 (recommended), or a fixed 2026-11-24.
7. **Traces on the Space:**
   - (a) ephemeral, with an export after each E7 run and session (free; recommended);
   - (b) persistent storage (paid; confirm the price);
   - (c) pushed to a private Hugging Face dataset (needs a token secret).
8. **E6:**
   - Approve the design in section 8, including the pre-registered rule.
   - Operating income where it is not reported: the flagged EBIT proxy, or exclude.
   - If time runs short: cut E6 to revenue and CFO only, or drop it. E6 is not on the never-cut
     list; E1 and E5 already give baselines.
9. **Outside user:** who, when, and in person or remote. Consent wording.
10. **E7 comparison:** your measured or estimated manual build time for one company's
    three-statement model, with its basis.
11. **Spending cap:** if any paid option is chosen, the monthly cap you approve.
12. **Ownership (D5):** which team members take the API, the UI, Docker and deployment, E6,
    E7, and the session. The order below assumes one builder.

## 10. Order of work and time estimates

| Step | Work | Estimate | Depends on |
|---|---|---|---|
| 0 | Your decisions (section 9) | 30 min of your time | — |
| 1 | API: schemas, endpoints, gate on tool endpoints, job runner, request log, feedback table; tests | 6-8 h | D2, D5, D6 |
| 2 | Streamlit pages 1-5 (page 6 last, optional) | 6-8 h | 1 |
| 3 | Docker: lock file with hashes, CPU torch, model download and verify, read-only evidence, entrypoint, healthcheck; local build; first memory reading | 3-5 h | 1, D4 |
| 4 | Deploy to the chosen host (after you create the Space); first remote build about 15-25 min; smoke run | 2-3 h | 3, D1, D3 |
| 5 | Dashboard page 7 and the `/metrics` aggregates; alert banner | 3-4 h | 1, 2 |
| 6 | E7 runs (local and deployed), e7-0001, report | 3-4 h | 4, 5, D10 |
| 7 | E6: pre-registration, harness on train (origin forecasts with BC7 history), one validation run, report | 8-12 h | D8; independent of 1-6 |
| 8 | Outside-user session: tasks and form (1 h), session (35 min), write-up (1 h) | 2.5-3 h | 4, D9 |
| 9 | Logs and docs: issues log, experiments.csv, model cards, blueprint_changes if any; default-path check | 1-2 h | all |
| **Total** | | **about 35-49 h, so 5 to 6 working days for one builder** | |

**Suggested days:**
- **26-27 Nov:** steps 1-2. E6 step 7 starts in parallel if a second member takes it.
- **28 Nov:** steps 3-4 (public URL).
- **29 Nov:** step 5, then step 6.
- **30 Nov - 1 Dec:** step 8 (outside user), and step 7 finishes.
- **2 Dec:** step 9; Phase 9 exit check.
- **3 Dec:** Phase 10 freeze and recording.

With one builder this is tight against the 4 December exam. The schedule fits only if E6 runs in
parallel or is cut down under D8.

**Cut order inside Phase 9** (the never-cut list keeps Docker deployment with the dashboard and
the backup recording):
1. Evidence-replay page.
2. Public FastAPI.
3. E6 down to revenue and CFO.
4. Persistent traces (export instead).

## Sources (prices, checked 2026-11-26)

- Hugging Face: [pricing](https://huggingface.co/pricing), [Spaces billing](https://huggingface.co/docs/hub/en/billing), [Spaces hardware](https://huggingface.co/docs/hub/main/spaces-gpus), [eesel.ai summary 2026](https://www.eesel.ai/en/blog/hugging-face-pricing)
- Railway: [plans](https://docs.railway.com/reference/pricing/plans), [pricing](https://railway.com/pricing)
- Fly.io: [resource pricing](https://fly.io/docs/pricing/), [2026 pricing update](https://fly.io/pricing-update/)
- Render: [pricing](https://render.com/pricing), [compute plans update](https://render.com/docs/compute-plans-update)

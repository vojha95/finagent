Known limitations, final pass (Phase 10), in the style of blueprint section 19: what each one is, why it matters, the issues behind it in docs/issues_log.md (and the blueprint change in docs/blueprint_changes.md, where there is one), and a fix path. The measured results are in docs/final_results.md.

**Scope and data**

| Limitation | Why it matters | Issues | Fix path |
|---|---|---|---|
| Trained and tested on US 10-K inline XBRL only (blueprint 19). | The document channel learned from rows labelled by the filings' own tags. How it transfers to documents without tags, such as Indian annual-report PDFs, is not measured. | Blueprint section 19 (no issue logged) | Hand-label Indian annual-report tables; measure transfer; fine-tune. |
| Extension concepts map to OTHER (blueprint 19). | A visible item tagged with a company-specific concept is labelled OTHER, so it trains M1 with a noisy label and E1 cannot score it. | #20, #43 | Map extension concepts through each filing's calculation linkbase. |
| Banks, insurers and REITs are excluded (blueprint 19). | The taxonomy, identities and forecast assume industrial and commercial filers; the 48 companies include none of these. | Blueprint section 19; configs/universe.yaml | Industry-specific taxonomies and identities. |
| The evidence is pinned and offline: US 10-Ks only, up to five fiscal years per company, snapshot 2026-11-24. The manifest records no date, so the snapshot date is configured. | No newer filing is ever read; answers are as of the snapshot. | BC1, #323 | Re-run the rate-limited fetch into staging, review it, and pin a new manifest that records its date. |
| Statements incorporated by reference from an exhibit are not read, for example IBM's. | Such a company has no statement set; it is a gap with a general reason code, never guessed. | BC10, #32 | Pin and read the referenced exhibit as a second document of the filing. |
| XBRL label noise caps measurable accuracy (blueprint 19). | The label audit found 5 label errors in 3,475 validation rows (0.0014), a lower bound: label errors the model agrees with are not seen. | #51, #43 | Keep the audited corrections; audit a sample of agreed rows as well. |

**Document channel (E1)**

| Limitation | Why it matters | Issues | Fix path |
|---|---|---|---|
| The 95% target on held-out is not met: M1 v3 0.913, M3 0.941 (e1-0009, e1-0010). | About one value in eleven read from a statement does not match its tag; dual-channel verification catches disagreements, but a document-only value is less certain. | #308 | A new document model chosen on validation, then a new pre-registered held-out run (held-out pre-registration, section 7). |
| M3 is not wired in. It has the higher point estimate on both splits, but the pre-registered rule needs the paired interval's lower bound strictly above 0, and it was exactly 0. | The shipped reader is the weaker point estimate, by design of the rule. | #297, #308 | As above; more validation companies would narrow the interval before the next held-out run. |
| GIS was scored on one of its five pinned years in the held-out run. The cause was not investigated, and held-out is never re-run. | 27 of the 869 held-out fields come from one GIS year, so GIS weighs less than the other companies. | #309, #311 | Investigate only on train or validation companies; re-measure on a new held-out group. |
| Train companies are in-sample: M1 was trained on their rows, and the app labels them "In-sample". | Their readings, and anything built on them, look better than an unseen company's would. Only validation and held-out accuracy are reported as measurements. | BC36, #351 | Serve train companies from a cross-fitted model that did not see them, or keep the label. |

**Statements, forecast and credit**

| Limitation | Why it matters | Issues | Fix path |
|---|---|---|---|
| Forecast drivers are simple historical rules, and new financing is off by default (blueprint 19). | The forecast does not beat the naive last value (E6). | #163, #350 | Driver learning across companies; refinancing scenarios. |
| E6 coverage and result. The Base forecast was VERIFIED at 28 of 32 origins. Operating income, CFO and FCF were scored at under half of the origins (15, 14 and 14 of 32), mostly because the next year's first-reported actual was not VERIFIED. No accuracy claim on any row; for CFO the naive value is clearly better. | No forecasting-accuracy claim can be made, and the scored origins may not be representative (the selection check is descriptive only). | BC34, BC35, #345, #350 | Raise actual-value verification coverage, improve the CFO drivers (working capital), then run a new pre-registered E6. |
| Train and held-out forecasts are GAP for many companies in serving-0001 (and serving-0002): VERIFIED in each scenario for validation 8 of 8, train 16 of 32, held-out 2 of 8. Reasons are listed per company: missing debt-principal schedules, I10 not computable or failing (statement cash, ±$1m residuals), inputs not reported, no identity check. MCD's workbook also fails the recalc check at two cells. | Half the train companies and most held-out ones show no projection in the app; the reason is shown, never filled. Held-out companies are never debugged (BC36). | #346, #347, #356, #357, #358 | Fix each general reason on train companies as a single change, re-checked on validation (for example debt maturities from the notes, statement-cash location, the ±$1m residual tolerance). Held-out stays untouched. |
| Comprehensive forward DSCR is withheld (blueprint 19). A scoped DSCR is reported with its basis instead. | The 10-K does not schedule short-term rollover or all obligations, so full debt service cannot be measured from the filing. | BC27, #163, #168, #169 | Add short-term borrowing and obligation schedules from the notes. |

**Correlation Agent (E2)**

| Limitation | Why it matters | Issues | Fix path |
|---|---|---|---|
| 29 of 40 injected errors are invisible: no evaluation-mode check fails. Only about 45% of scored fields enter a computed check. | The agent can only correct what the identity library can see; its detection and correction rates cover about a quarter of errors. | #112 | Face-block subtotal checks, cross-statement ties (D&A, capex against the PP&E movement), and document-cell operands for I10. |
| e2-0002 and e2-0003 are not blind, and the action prior is untested (priors 0.25 and 0.75 give identical results). | The gains after e2-0001 are partly fitted to the same 40 company-years. | #108-#111, #114 | A blind run on company-years not used in the design. |

**Evidence for insights (E3, E4)**

| Limitation | Why it matters | Issues | Fix path |
|---|---|---|---|
| E3 labels only pooled chunks, so recall is relative to the pool, not the whole filing. One better passage lay outside every variant's top 5. | Recall can be overstated, and the pool favours the variants that built it. | #237, #282 | Label exhaustively for a sample of queries. |
| Under evidence v1 (BM25 quotes), Vineet judged 0.704 of 27 quoted cards unsupported (e4-0001). | Quotes beside a risk card often did not explain it. | #301 | Evidence v2 (shipped); see the next row. |
| Evidence v2 trades coverage for precision: it quotes only passages above the reranker threshold (leave-one-company-out strict precision 0.625 at coverage 0.267), so 62 of 66 non-model cards are EVIDENCE_GAP. | Most cards show no evidence; a gap is honest but less useful. | #302, #305 | Better query templates and passage selection; re-choose the threshold on new labels. |
| e4-0002 is post-hoc and optimistic, and rests on 2 quoted slots. | It cannot show that v2's quotes are faithful. | #312 | Label v2 cards on companies not used to choose the threshold. |
| The D-EV2 low-confidence sentence is shown beside a gap, labelled. It is never evidence and never counted. | A reader could still take it as support despite the label. | BC32, #310 | Keep the label; test it with outside users. |
| Item 8 text after the last note (for example an auditor's report placed after the notes) is filed under the last note. | A quote's section label can be wrong in such filings. | #174 | Detect the end of the notes from headings. |

**Planner (E5, E8)**

| Limitation | Why it matters | Issues | Fix path |
|---|---|---|---|
| The planner supports a defined goal set, and no LLM fallback was built (blueprint 19). A question the compiler cannot parse gets a clarification; pass^3 was not run. | Questions outside the catalogue are asked back, not answered. | #121, #217 | Grow intents from logged CLARIFICATION questions; a local LLM fallback measured with pass^3. |
| The shipped compiler (intent v3) is measured only post-hoc (e5-0003), after e5-0002's errors were seen; the blind headline is intent v2. | The shipped compiler's figures are optimistic. | #234 | A new blind question set from another writer. |
| Issuer resolution has known false matches on common words: "targets" can be read as Target, "ups" as UPS and "pg" as PG. None occurs in the E5 set. | A question using such a word can be answered about the wrong company. | #252, #268 | Accept a possessive without an apostrophe only for names that are not common words, and add the false matches as tests. |
| In E8's single-channel views the forecast, credit and liquidity goals are mostly correct refusals. | Their fallback behaviour is lightly exercised. | #150 | Replays that hide one source for one item at a time. |

**Evaluation sets and people**

| Limitation | Why it matters | Issues | Fix path |
|---|---|---|---|
| Small hand-labelled sets with a single annotator for E3 to E5 (blueprint 19): E3 30 queries and E4 27 cards labelled by Vineet; E5 120 questions, Vineet's 70 blind, all labels written by the builder. E4's second rater is a local NLI judge, not a person. | Intervals are wide, and no agreement between people is measured. | #166, #207, #237 | A second annotator, agreement, larger sets. |
| The manual build time beside E7, about 20 hours, is Vineet's estimate from experience, not measured (D10). | The time saving is indicative only. | #353 | Time an analyst building one company's model from its 10-Ks. |
| The outside-user session is one person for 20 minutes (docs/outside_user_session.md); its write-up is pending (docs/outside_user.md). | One session shows usability problems but measures nothing. | BC34, #319, #336 | More participants on the same pack, with longer tasks. |

**Serving and operations (Phase 9 cut-down, BC33)**

| Limitation | Why it matters | Issues | Fix path |
|---|---|---|---|
| No public deployment: Hugging Face now requires a paid PRO plan for Docker Spaces, and Vineet chose not to pay. The demo runs locally. | Nobody can use it without a local working copy of the models and runs. | BC33, #322 | Host the image on a paid plan or an institutional server. |
| The Dockerfile ships unbuilt and untimed (there is no Docker on the development Mac). | The image is untested. | #326 | Build and smoke-test the image on a machine with Docker. |
| In the container Ask builds statement sets live: the serving snapshot's fingerprint was taken on the Mac (macOS on arm64, with those package builds), so it cannot match in the Linux image. First answers are as in e7-0001 (on the Mac: cold p50 0.25 s, p95 16.38 s, max 35.53 s). | A container user waits up to about half a minute for a company's first answer. | BC37, #371 | Build the serving run inside the image. |
| No FastAPI service and no operations dashboard: only the Results page and E7. Traces are not persisted (one can be downloaded from the Ask page), and there is no logging, error-rate or cost monitoring. | Latency and errors in use are not observed; a problem in use leaves no record. | BC33, #316, #318, #320 | A FastAPI service with persisted traces (blueprint 8.17) and a dashboard reading them. |
| E7 is light only: 20 questions on one machine, no load test. FinAgent's build time beside the manual estimate is serving-0001's full build of one company from the filings (median 36 s, max 92 s); 28 of the 139 cached passage-embedding files predate that build, so it is close to, not strictly, a build from nothing. serving-0002's 17 s / 48 s read cached embeddings and is not the build time. | The speed figures are an indication, not a service-level measure. | #318, #370 | The full E7 on the deployed service. |
| Fast first answers need a matching serving run. The Ask page reuses the serving run's statement sets only at its as-of date, and only while the code (all of finagent/ except finagent/ui), the configs (except ui.yaml), the model, evidence, platform and packages hash the same as at its build (e7-0002: cold p95 0.74 s, against 16.38 s in e7-0001). The models load at start, about 5.5 s. | After any change to those, or at another as-of date, a company's first answer builds live again (up to about 35 s) until a new serving run is built; the trace names the reason. | BC37, #366-#369 | Rebuild the serving run as part of each release. |
| The plain-language view is built from the answer's records, never from its text. The growth inputs of a growth-gap risk measure are recorded only in its basis text; they are read with a strict pattern and left out when it does not match. | A few plain answers omit those inputs. | #352 | Record the growth inputs as structured fields. |
| No evidence-replay page: E8 is reported on the Results page only. | A viewer cannot replay a hidden-evidence case in the app. | BC33, #321 | A replay page over the E8 hook. |

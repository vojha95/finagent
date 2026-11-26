Known limitations, each with the issue numbers in docs/issues_log.md (and the blueprint change,
where there is one, in docs/blueprint_changes.md).

**Scope and data**
- **Companies.** The demo serves all 48 pinned companies, each labelled by its split (D2b,
  BC36; #351). Only the 8 validation companies are measured. Train companies are in-sample for
  M1, so their accuracy is optimistic. Held-out companies were measured once (held-out E1) and are
  never debugged, tuned or evaluated: what looks wrong on them is logged, not fixed (#356, #358).
  Forecasts VERIFIED in serving-0001, in each scenario: validation 8 of 8, train 16 of 32,
  held-out 2 of 8; the others are GAP with their reasons shown, never filled.
- **Evidence.** The evidence is pinned and offline: US 10-Ks only, up to five fiscal years per
  company, snapshot 2026-11-24. No new filing is fetched. The manifest records no date, so the
  snapshot date is configured (#323).
- **Statements by reference.** Statements incorporated by reference from an exhibit are not
  read, for example IBM. Such a filing is a gap and is never guessed (BC10; #32).

**Document channel (E1)**
- **95% target not met.** The blueprint's starting target of 95% field accuracy on held-out was
  not met: M1 v3 scored 0.913 and M3 0.941 (#308).
- **M3 not wired in.** M3 has the higher point estimate on both splits. The pre-registered rule
  needs the paired interval's lower bound to be strictly above 0, and it was exactly 0 on
  held-out, so M1 v3 stays (#297, #308).
- **GIS.** In the held-out run, GIS was scored on one of its five pinned years. The cause was not
  investigated, and held-out is never re-run (#309, #311).

**Correlation Agent (E2)**
- **Most injected errors are invisible.** 29 of 40 injected errors are invisible, because no
  evaluation-mode check fails. Only about 45% of fields enter a computed check (#112).

**Evidence for insights (E3, E4)**
- **Recall is relative to the pool.** E3 labels only pooled chunks, so its recall is measured
  against the pool, not the whole filing (#237, #282).
- **Unsupported claims under v1.** Evidence v1 (BM25) quotes were judged unsupported for 0.704
  of 27 cards by Vineet's labels (e4-0001; #301).
- **Evidence v2 trades coverage for precision.** v2 quotes only passages above the reranker
  threshold, with leave-one-company-out strict precision 0.625 at coverage 0.267. As a result,
  62 of 66 non-model cards are EVIDENCE_GAP (#302, #305).
- **e4-0002 is post-hoc.** Its score is post-hoc and optimistic, and rests on 2 quoted slots
  (#312).
- **Low-confidence sentences.** The D-EV2 low-confidence sentence is shown beside a gap,
  labelled. It is never evidence and never counted (BC32; #310).
- **Section boundaries.** Item 8 text after the last note is filed under the last note (#174).

**Planner (E5, E8)**
- **Shipped compiler measured only post-hoc.** The blind E5 headline is intent v2 (e5-0002). The
  shipped intent v3 is measured only post-hoc (e5-0003), after e5-0002's errors were seen
  (#234).
- **Issuer false matches.** Issuer resolution has known false matches on common-word company
  names (#268).
- **No LLM fallback.** No LLM fallback was built. A question the compiler cannot parse gets a
  clarification (#121).

**Phase 9 cut-down (BC33; E6 and the outside-user session reinstated by BC34)**
- **No FastAPI service.** The app calls the library directly (#316).
- **No forecast accuracy claim.** E6 ran once on validation as pre-registered (e6-0001): 28 of
  32 origins scored, and no accuracy claim on any of its 7 rows (docs/e6_results.md; #350).
- **E7 is light only.** E7 is 20 questions on one machine: no load test, logging or cost
  measure (#318). The manual build time beside it (about 20 hours) is Vineet's estimate from
  experience, not measured (D10; #353).
- **Plain-language answers.** The plain view is built from the answer's records, never from its
  text. The growth inputs of a growth-gap risk measure are recorded only in its basis text; they
  are read with a strict pattern, and left out when it does not match (#352).
- **No outside-user session yet.** It was reinstated as a later step (BC34). No outside-user
  feedback is recorded so far (#319, #336).
- **Traces are not persisted.** The demo saves no trace; one can be downloaded from the Ask page
  (#320).
- **No evidence-replay page.** E8 is reported on the Results page only (#321).
- **No public deployment.** Hugging Face now requires a paid PRO plan for Docker Spaces, and
  Vineet chose not to pay, so the demo runs locally. The Dockerfile ships unbuilt (#322, #326).

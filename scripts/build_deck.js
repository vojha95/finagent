// FinAgent final deck: product, the course's three layers (intelligence, evaluation, deployment),
// problems faced, learnings. Numbers come from docs/final_results.md and docs/limitations.md.
// Build: PPTX_SKILL=<pptx skill dir> NODE_PATH=<node_modules with pptxgenjs> node scripts/build_deck.js out.pptx
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.PPTX_SKILL + "/scripts/apply_theme.js");

const OUT = process.argv[2] || "FinAgent_deck.pptx";
const THEME = {
  name: "FinAgent",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "17212F", lt1: "FFFFFF", dk2: "2C3E57", lt2: "EEF2F6",
    accent1: "1E7A68", // verified green / evaluation layer
    accent2: "C98A12", // gap amber / verification
    accent3: "B4473A", // negative red
    accent4: "3F6BA8", // trained models / intelligence layer
    accent5: "7D8A9C", // neutral
    accent6: "5E4B8B", // deployment layer
    hlink: "3F6BA8", folHlink: "5E4B8B",
  },
};
const HEX = THEME.colors;
const TINT = { blue: "DCE6F4", green: "DCEFE8", amber: "F7E9CF", violet: "E6E1F0", red: "F4DEDA", grey: HEX.lt2 };

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10 x 5.625 in
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "FinAgent: final presentation";
pres.author = "Vineet Ojha";
const C = pres.SchemeColor;

pres.defineSlideMaster({
  title: "DARK",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 1.55, w: 8.8, h: 1.1,
        fontSize: 48, bold: true, color: C.background1, fontFace: THEME.headFontFace, valign: "bottom",
        align: "left", margin: 0 }, text: "Title" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.6, y: 2.75, w: 8.8, h: 1.5,
        fontSize: 18, color: "CFD8E3", valign: "top", align: "left", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: C.background1 },
  margin: [0.5, 0.5, 0.5, 0.5],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.5, y: 0.3, w: 9.0, h: 0.75,
        fontSize: 26, bold: true, color: C.text1, fontFace: THEME.headFontFace, valign: "middle",
        align: "left", margin: 0 }, text: "Title" } },
    { text: { text: "FinAgent  ·  final presentation", options: { x: 0.5, y: 5.22, w: 5, h: 0.25,
        fontSize: 9, color: HEX.accent5, margin: 0 } } },
  ],
  slideNumber: { x: 9.1, y: 5.22, w: 0.4, h: 0.25, fontSize: 9, color: HEX.accent5, align: "right" },
});

const T = (slide, text, o) => slide.addText(text, Object.assign({ isTextBox: true, fontSize: 14,
  color: C.text1, margin: 0, valign: "top" }, o));
function card(slide, x, y, w, h, fill, name) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08,
    fill: { color: fill }, line: { color: fill }, objectName: name });
}
function chip(slide, x, y, w, label, color, h = 0.32, size = 11) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: h / 2,
    fill: { color }, line: { color } });
  T(slide, label, { x, y, w, h, fontSize: size, bold: true, color: C.background1, align: "center", valign: "middle" });
}
function stat(slide, x, y, w, big, label, color, bigSize = 40) {
  T(slide, big, { x, y, w, h: 0.75, fontSize: bigSize, bold: true, color, fontFace: THEME.headFontFace, valign: "bottom" });
  T(slide, label, { x, y: y + 0.8, w, h: 0.6, fontSize: 12, color: C.text2 });
}
// a titled card: heading in colour, body text below
function hcard(slide, x, y, w, h, fill, head, body, headColor, bodySize = 12) {
  card(slide, x, y, w, h, fill, head);
  T(slide, head, { x: x + 0.15, y: y + 0.12, w: w - 0.3, h: 0.35, fontSize: 14, bold: true, color: headColor });
  T(slide, body, { x: x + 0.15, y: y + 0.5, w: w - 0.3, h: h - 0.6, fontSize: bodySize });
}
// the course layer a content slide belongs to, top right
function layerTag(slide, label, color) { chip(slide, 7.6, 0.08, 1.9, label, color, 0.26, 9); }
function arrowLine(slide, x1, y1, x2, y2) {
  slide.addShape(pres.shapes.LINE, { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
    flipH: x2 < x1, flipV: y2 < y1, line: { color: HEX.accent5, width: 1.25, endArrowType: "triangle" } });
}
const hdr = { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 } };
function table(slide, rows, colW, y, opts = {}) {
  const fs = opts.fontSize || 11;
  const data = rows.map((r, i) => r.map((c, j) => ({ text: c, options: i === 0 ? Object.assign({ fontSize: fs }, hdr) :
    Object.assign({ fontSize: fs, color: HEX.dk1, bold: !!opts.boldFirst && j === 0,
      fill: { color: i % 2 ? "FFFFFF" : HEX.lt2 } }, (opts.cell && opts.cell(i, j, c)) || {}) })));
  slide.addTable(data, { x: 0.5, y, w: colW.reduce((a, b) => a + b, 0), colW, rowH: opts.rowH || 0.34,
    border: { type: "none" }, margin: [0.03, 0.08, 0.03, 0.08], valign: "middle" });
}
const LAYER = { intel: ["Intelligence layer", HEX.accent4], evalL: ["Evaluation layer", HEX.accent1],
  deploy: ["Deployment layer", HEX.accent6] };

pres.addSection({ title: "Opening" });
// =============== 1. Title ===============
let s = pres.addSlide({ masterName: "DARK", sectionTitle: "Opening" });
s.addText("FinAgent", { placeholder: "title" });
s.addText("An agentic analyst for US 10-K filings. It reads every statement two independent ways, " +
  "checks the numbers against the filer's own XBRL, builds a model that balances, and only says what the filing supports.",
  { placeholder: "body" });
T(s, "AI-ML in Practice  ·  final project  ·  5 December 2026", { x: 0.6, y: 4.6, w: 8.8, h: 0.35, fontSize: 13, color: "9FB0C4" });
s.addNotes("One-line pitch. FinAgent works offline on pinned 10-K filings for 48 US companies, and no external AI API is called anywhere.");

// =============== Team ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Opening" });
s.addText("Built by the team: who owns which part", { placeholder: "title" });
table(s, [
  ["Member", "Roll number", "Owns, presents and is examined on"],
  ["Vineet Ojha", "x2vio300", "Architecture; the document channel and trained readers M0–M3 (E1); dual-channel verification and the Correlation Agent (E2)"],
  ["Renuka Nanda (team representative)", "x2ren868", "Problem and product; the local app, serving and latency (E7); the live demo"],
  ["Tushar Gupta", "x2tug169", "Investigation planner, goal compiler, tool layer and observation gate (E5, E8)"],
  ["Shilpa Lakhwara", "x1shl507", "RAG for evidence: retrieval, risk signals and insight cards (E3, E4)"],
  ["Shivkumar Radheshyam Yadav", "x2shy999", "Integrated three-statement model, scenarios, credit and liquidity, the forecast backtest (E6)"],
], [2.6, 1.2, 5.2], 1.2, { fontSize: 11.5, rowH: 0.55, boldFirst: true });
T(s, "Shared by all: the evaluation design, the limitations and the experiments log (eval/experiments.csv).",
  { x: 0.5, y: 4.72, w: 9.0, h: 0.35, fontSize: 12, color: C.text2 });
s.addNotes("Stated up front, as the course asks. Each member presents their own part and takes viva questions on it.");

pres.addSection({ title: "The product" });
// =============== 3. Problem ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "The product" });
s.addText("An analyst's first model of a company takes days", { placeholder: "title" });
T(s, "Before any judgement, someone has to read the 10-K, re-key three statements, check they tie, and build a " +
  "forecast with downside cases. Most of that time is mechanical, and copying errors are easy to miss.",
  { x: 0.5, y: 1.3, w: 4.2, h: 1.6, fontSize: 15 });
T(s, "Users: credit and equity analysts, and anyone who needs a checked starting model quickly.",
  { x: 0.5, y: 3.05, w: 4.2, h: 0.8, fontSize: 14, color: C.text2 });
card(s, 5.1, 1.3, 2.05, 2.6, C.background2, "manual");
card(s, 7.35, 1.3, 2.15, 2.6, C.background2, "finagent");
stat(s, 5.3, 1.5, 1.7, "≈20 h", "by hand for one company's model (Vineet's estimate, not measured; D10)", C.accent3);
stat(s, 7.55, 1.5, 1.8, "36 s", "FinAgent's full build of one company, median (max 92 s; serving-0001)", C.accent1);
T(s, "First answer about a company: 0.74 s at p95 (e7-0002).", { x: 5.1, y: 4.15, w: 4.4, h: 0.4, fontSize: 13, color: C.text2 });
s.addNotes("The 20 hours is an estimate from experience, not a measurement. The 36 s covers statements, three-scenario model, credit, risk cards and the recalculated workbook.");

// =============== 4. What it does ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "The product" });
s.addText("One local app, three pages, 48 companies", { placeholder: "title" });
[["Ask", "A question in plain English. The planner runs only the tools it needs; every number passes the observation gate; the answer comes in plain words with a trace."],
 ["Company", "Verified statements, a three-year forecast under Base, Moderate and Severe, credit and liquidity, risk cards with quoted evidence, and a linked Excel workbook."],
 ["Results", "Every evaluation's headline with its interval and decision, latency (E7) and the limitations: the dashboard."],
].forEach(([h, b], i) => {
  const x = 0.5 + i * 3.05;
  card(s, x, 1.3, 2.85, 2.55, C.background2, "page-" + h);
  T(s, h, { x: x + 0.2, y: 1.45, w: 2.45, h: 0.45, fontSize: 20, bold: true, color: C.accent4, fontFace: THEME.headFontFace });
  T(s, b, { x: x + 0.2, y: 1.95, w: 2.45, h: 1.8, fontSize: 13 });
});
chip(s, 0.5, 4.2, 2.3, "8 Measured (validation)", C.accent1);
chip(s, 2.95, 4.2, 2.3, "32 In-sample (train)", C.accent5);
chip(s, 5.4, 4.2, 2.3, "8 Unseen (held-out)", C.accent4);
T(s, "Every company carries its label.", { x: 7.85, y: 4.2, w: 1.7, h: 0.32, fontSize: 12, color: C.text2, valign: "middle" });
s.addNotes("Served from the pinned run serving-0002. Held-out companies are served but were never used for debugging or tuning.");

// =============== 5. One question end to end ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "The product" });
s.addText("One question, end to end", { placeholder: "title" });
card(s, 0.5, 1.2, 9.0, 0.55, TINT.grey, "question");
T(s, "“Assess Colgate's liquidity under the Severe scenario; if there is a shortfall, check debt service.”",
  { x: 0.7, y: 1.2, w: 8.6, h: 0.55, fontSize: 14, italic: true, valign: "middle" });
[["Compile", "trained intent model + rule slots → typed goals, one conditional", TINT.blue],
 ["Plan", "registry picks tools by prerequisite and priority; step budget", TINT.green],
 ["Act", "statements → forecast → liquidity; shortfall, so the debt goal fires", TINT.grey],
 ["Gate", "every value checked: issuer, period, unit, accession, filed ≤ as-of", TINT.green],
 ["Answer", "plain words, badges and a trace; raw answer kept", TINT.grey],
].forEach(([h, b, f], i) => {
  const x = 0.5 + i * 1.84;
  hcard(s, x, 2.0, 1.66, 1.9, f, h, b, HEX.dk2, 11);
  if (i < 4) arrowLine(s, x + 1.66, 2.95, x + 1.84, 2.95);
});
T(s, "Result (Severe, from the FY2025 10-K): liquidity falls short by USD 1.5bn in FY2026; scheduled debt-service coverage 0.62x; comprehensive coverage withheld by design.",
  { x: 0.5, y: 4.15, w: 9.0, h: 0.7, fontSize: 13, color: C.text2 });
s.addNotes("Plan-and-execute in the course's terms: the plan is typed goals, execution is tool calls, and the gate is the guardrail before anything reaches the user. This is also the main demo question.");

pres.addSection({ title: "Intelligence layer" });
// =============== 6. Architecture ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Intelligence layer" });
s.addText("Architecture: two readings, one gate", { placeholder: "title" });
layerTag(s, ...LAYER.intel);
function box(x, y, w, h, label, sub, fill) {
  card(s, x, y, w, h, fill, label);
  T(s, [{ text: label, options: { bold: true, breakLine: true } }, { text: sub, options: { fontSize: 10 } }],
    { x: x + 0.08, y, w: w - 0.16, h, fontSize: 12, align: "center", valign: "middle" });
}
box(0.5, 1.2, 9.0, 0.55, "A  Investigation planner (agentic)", "trained goal compiler · action registry · conditional goals · step budget · trace", TINT.green);
box(0.5, 2.05, 1.6, 1.0, "Evidence store", "pinned 10-Ks · sha256 · as-of", TINT.grey);
box(2.4, 2.05, 1.9, 0.45, "Document channel", "M1 v3, trained", TINT.blue);
box(2.4, 2.6, 1.9, 0.45, "Tag channel", "inline XBRL + Company Facts", TINT.grey);
box(4.6, 2.05, 1.9, 1.0, "Verification", "agree → VERIFIED · else Correlation Agent", TINT.amber);
box(6.8, 2.05, 2.7, 1.0, "Validated statement set", "REPORTED_FACT · CALCULATION · provenance", TINT.grey);
box(0.5, 3.4, 2.9, 0.75, "Model + scenarios", "balances every year (F1–F5)", TINT.grey);
box(3.55, 3.4, 2.9, 0.75, "Credit and liquidity", "ratios · headroom · scoped DSCR", TINT.grey);
box(6.6, 3.4, 2.9, 0.75, "Risk cards + RAG", "R1–R11 · BM25 + bge + reranker", TINT.blue);
box(0.5, 4.45, 9.0, 0.55, "B  Observation gate (guardrail)", "issuer · period · metric · unit · classification · accession · filed ≤ as-of", TINT.green);
arrowLine(s, 2.1, 2.55, 2.4, 2.28); arrowLine(s, 2.1, 2.55, 2.4, 2.82);
arrowLine(s, 4.3, 2.28, 4.6, 2.45); arrowLine(s, 4.3, 2.82, 4.6, 2.65); arrowLine(s, 6.5, 2.55, 6.8, 2.55);
arrowLine(s, 8.15, 3.05, 8.15, 3.4);
s.addNotes("Blue: models we trained or host. Green: agentic control and guardrails. Amber: verification. Grey: deterministic code. No LLM at run time: numbers must be traceable, so the reasoning lives in typed goals, tools and checks.");

// =============== 7. Course toolkit map ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Intelligence layer" });
s.addText("Course topics, and where they live in FinAgent", { placeholder: "title" });
table(s, [
  ["Course topic (session)", "In FinAgent", "Depth"],
  ["Agentic workflows: ReAct, plan-and-execute, budgets (S7, S10)", "Investigation planner; Correlation Agent's observe-act-recheck loop", "Deep"],
  ["Tool calling and guardrails (S6)", "Typed tool layer with as_of; observation gate; held-out guard", "Deep"],
  ["Evaluation: gold set, baselines, judge, latency (S14)", "E1–E8, pre-registered rules, protected held-out set, local NLI judge", "Deep"],
  ["Embeddings; fine-tuning vs classical (S2, S3, S5)", "M1 TF-IDF + LR shipped; M3 fine-tuned bge-small; intent model", "Deep"],
  ["RAG: BM25, hybrid, reranker (S4)", "Evidence for risk cards: BM25 + dense + RRF + cross-encoder", "Supporting"],
  ["Memory and state (S9)", "Run state, call reuse, traces; fingerprinted serving snapshot", "Supporting"],
  ["MCP (S6)", "MCP-style typed tools; no MCP server built", "Not built"],
  ["Multi-agent; self-improving agents (S12, S13)", "One planner + one reconciliation agent; no self-improvement", "Not used"],
], [4.1, 3.9, 1.0], 1.15, { fontSize: 10.5, rowH: 0.4, cell: (i, j, c) => j === 2 ?
  { bold: true, color: c === "Deep" ? HEX.accent1 : c === "Supporting" ? HEX.accent4 : HEX.accent5 } : null });
s.addNotes("Depth labels are from blueprint Version 3 section 14. MCP and multi-agent were taught but not used; a single planner was enough for one company at a time.");

// =============== 9. Models ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Intelligence layer" });
s.addText("Fine-tuning vs classical: four readers trained", { placeholder: "title" });
layerTag(s, ...LAYER.intel);
s.addChart(pres.charts.BAR, [{ name: "Validation field accuracy",
  labels: ["M3 bge-small fine-tuned", "M2 LightGBM", "M1 v3 TF-IDF + LR", "M0 rules"], values: [0.929, 0.865, 0.905, 0.773] }], {
  x: 0.4, y: 1.2, w: 5.0, h: 3.7, barDir: "bar", chartColors: [HEX.accent5, HEX.accent5, HEX.accent4, HEX.accent5],
  showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0.000", dataLabelFontSize: 11,
  dataLabelFontFace: "+mn-lt", dataLabelColor: HEX.dk1,
  valAxisMinVal: 0.6, valAxisMaxVal: 1.0, valAxisHidden: true, valGridLine: { style: "none" },
  catAxisLabelFontSize: 11, catAxisLabelFontFace: "+mn-lt", catAxisLabelColor: HEX.dk2,
  showLegend: false, showTitle: true, title: "Validation field accuracy (e1-0005 to e1-0008)",
  titleFontSize: 12, titleFontFace: "+mn-lt", titleColor: HEX.dk2,
});
card(s, 5.7, 1.3, 3.8, 3.5, C.background2, "heldout");
T(s, "Held-out, measured once", { x: 5.9, y: 1.42, w: 3.4, h: 0.4, fontSize: 16, bold: true, color: C.text2, fontFace: THEME.headFontFace });
stat(s, 5.9, 1.75, 1.7, "0.913", "M1 v3, shipped [0.846, 0.961]", C.accent4, 30);
stat(s, 7.6, 1.75, 1.7, "0.941", "M3 [0.887, 0.985]", C.accent5, 30);
T(s, "Paired M3 − M1 v3: +0.029 [0.000, +0.055]. The pre-registered rule needed the lower bound above 0; it is exactly 0, so the cheaper M1 v3 ships.",
  { x: 5.9, y: 3.35, w: 3.4, h: 1.35, fontSize: 12 });
s.addNotes("M3 is the pinned bge-small encoder fine-tuned on the Mac's GPU (MPS) in about 34 minutes. Classical TF-IDF + logistic regression came within about 3 points on held-out, runs on a CPU and is easy to explain.");

// =============== 11. Agentic workflow 2: Planner ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Intelligence layer" });
s.addText("Agentic workflow 2: plan, act, gate", { placeholder: "title" });
layerTag(s, ...LAYER.intel);
hcard(s, 0.5, 1.2, 2.85, 2.4, TINT.blue, "Goal compiler (trained)",
  "TF-IDF + logistic regression classifies each clause into a goal kind; rules fill metric, period and scenario. Below 0.925 confidence: the regex grammar, then a clarification.", HEX.accent4, 11.5);
hcard(s, 3.575, 1.2, 2.85, 2.4, TINT.green, "Planner (plan-and-execute)",
  "Picks the next tool by prerequisite and priority, reuses identical calls, fires conditional goals, falls back to one-source values, stops at a 12-step budget.", HEX.accent1, 11.5);
hcard(s, 6.65, 1.2, 2.85, 2.4, TINT.green, "Observation gate",
  "A guardrail on every value: issuer, CIK, period, metric, unit, classification, accession, URL, filed ≤ as-of. If any check fails, the value is never shown.", HEX.accent1, 11.5);
stat(s, 0.5, 3.7, 2.7, "73.9%", "goal accuracy on 92 blind questions (grammar 26.1%; e5-0002)", C.accent4, 30);
stat(s, 3.575, 3.7, 2.7, "29 / 52", "on Vineet's own blind questions: the honest number", C.accent3, 30);
stat(s, 6.65, 3.7, 2.7, "0", "fabricated values with evidence hidden (e8-0004)", C.accent1, 30);
s.addNotes("Is it an agent if the rules are fixed? It observes tool outcomes and changes course (fallbacks, conditional goals, gaps); the learned part is the goal compiler. Every step is recorded in a trace: the run state.");

// =============== 12. RAG ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Intelligence layer" });
s.addText("RAG for evidence: quote only what clears the bar", { placeholder: "title" });
layerTag(s, ...LAYER.intel);
[["Chunk", "10-K sections into passages"], ["Retrieve", "BM25 + bge-small dense"], ["Fuse", "reciprocal rank fusion"],
 ["Rerank", "MiniLM cross-encoder"], ["Threshold", "score ≥ 5.263 → quote"],
].forEach(([h, b], i) => {
  const x = 0.5 + i * 1.84;
  hcard(s, x, 1.2, 1.66, 1.25, i === 4 ? TINT.amber : TINT.blue, h, b, HEX.dk2, 11);
  if (i < 4) arrowLine(s, x + 1.66, 1.83, x + 1.84, 1.83);
});
hcard(s, 0.5, 2.7, 4.35, 2.2, TINT.grey, "What the evaluations showed",
  "E3: no variant beat BM25 on MRR@5 (0.677; best change +0.048 [−0.049, +0.140]).\n\nE4: with v1 quotes, 0.704 of 27 cards were not supported by their quote (Vineet's labels).", HEX.dk2, 12);
hcard(s, 5.15, 2.7, 4.35, 2.2, TINT.amber, "So we changed the design (v2, post-hoc)",
  "Quote only above the reranker threshold. Otherwise EVIDENCE_GAP (“the filing does not explain this movement”), plus a labelled low-confidence sentence that never counts as evidence.", HEX.accent2, 12);
s.addNotes("Context precision over recall: for an analyst a wrong quote is worse than none. The threshold was chosen post-hoc, so e4-0002 is labelled optimistic. Our local NLI judge (the LLM-as-judge stand-in) agreed with Vineet only at kappa 0.411, well below the course's 85-90% guidance, so human labels decide.");

pres.addSection({ title: "Evaluation layer" });
// =============== 14. Evaluation design ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Evaluation layer" });
s.addText("Evaluation: eight questions, eight baselines", { placeholder: "title" });
layerTag(s, ...LAYER.evalL);
table(s, [
  ["", "Question", "Headline", "Baseline", "Decision"],
  ["E1", "Reads unseen companies?", "Held-out 0.913 [0.846, 0.961]", "M0 rules 0.773", "M3 not shipped"],
  ["E2", "Agent helps, safely?", "10/40 fixed; 0 wrongly changed", "No agent", "Safety net"],
  ["E3", "Finds the passage?", "MRR@5 0.677", "BM25", "BM25 stays"],
  ["E4", "Cards faithful?", "0.704 unsupported", "Human labels", "Evidence v2"],
  ["E5", "Understands questions?", "73.9% goal accuracy", "Grammar 26.1%", "Precision not kept"],
  ["E6", "Beats naive forecast?", "No claim; CFO naive better", "Last value", "No claim"],
  ["E7", "Fast enough?", "0.74 s p95 first answer", "≈20 h by hand", "Light run"],
  ["E8", "Gaps, not guesses?", "1150/1152; 0 fabricated", "Full evidence", "Target met"],
], [0.45, 2.15, 2.6, 1.7, 2.1], 1.15, { fontSize: 10.5, rowH: 0.37, boldFirst: true });
T(s, "Gold set protected: the 8 held-out companies were never opened during development and were scored once, under a pre-registered rule.",
  { x: 0.5, y: 4.6, w: 9.0, h: 0.45, fontSize: 12, color: C.text2 });
s.addNotes("Every number is in docs/final_results.md with its run id; a test checks all 403 of them against their sources. pass^k was not run: there is no LLM in the loop, so repeated runs give identical answers.");

// =============== 15. What worked / what didn't ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Evaluation layer" });
s.addText("What worked, and what did not", { placeholder: "title" });
layerTag(s, ...LAYER.evalL);
const bl = (arr) => arr.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < arr.length - 1 } }));
card(s, 0.5, 1.2, 4.35, 3.7, TINT.green, "worked");
T(s, "Worked", { x: 0.65, y: 1.32, w: 4.05, h: 0.35, fontSize: 14, bold: true, color: C.accent1 });
T(s, bl(["The trained reader beats rules by 13 points on validation (0.905 vs 0.773)",
  "The trained planner understands nearly 3x more questions than the grammar",
  "0 fabricated values when evidence is hidden (E8)",
  "The agent never changed a correct value (E2)",
  "Forecasts balance in every year and scenario, or show GAP",
  "First answer in under a second (E7)"]), { x: 0.65, y: 1.75, w: 4.05, h: 3.05, fontSize: 12, paraSpaceAfter: 4 });
card(s, 5.15, 1.2, 4.35, 3.7, TINT.red, "did-not");
T(s, "Did not", { x: 5.3, y: 1.32, w: 4.05, h: 0.35, fontSize: 14, bold: true, color: C.accent3 });
T(s, bl(["Held-out 0.913 misses the 95% target; M3's gain is not significant",
  "E6: the forecast does not beat \"same as last year\"; CFO is worse",
  "E4: most v1 quotes did not support their card",
  "E5: Vineet's blind questions 29/52; precision fell to 93.2%",
  "E2: 29 of 40 injected errors invisible to the agent",
  "E3: no retrieval variant beat BM25"]), { x: 5.3, y: 1.75, w: 4.05, h: 3.05, fontSize: 12, paraSpaceAfter: 4 });
s.addNotes("The course rewards documenting what works and what does not. Each negative is reported with its interval and run id, whatever it showed.");

pres.addSection({ title: "Deployment layer" });
// =============== 17. Deployment ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Deployment layer" });
s.addText("Deployment: a local app, made fast by a safe cache", { placeholder: "title" });
layerTag(s, ...LAYER.deploy);
s.addChart(pres.charts.BAR, [
  { name: "e7-0001", labels: ["Cold p50", "Cold p95", "Cold max"], values: [0.25, 16.38, 35.53] },
  { name: "e7-0002", labels: ["Cold p50", "Cold p95", "Cold max"], values: [0.22, 0.74, 0.79] },
], {
  x: 0.4, y: 1.2, w: 4.9, h: 3.7, barDir: "col", barGrouping: "clustered", chartColors: [HEX.accent5, HEX.accent6],
  showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0.00", dataLabelFontSize: 10,
  dataLabelFontFace: "+mn-lt", dataLabelColor: HEX.dk1, valAxisHidden: true, valGridLine: { style: "none" },
  catAxisLabelFontSize: 11, catAxisLabelFontFace: "+mn-lt", catAxisLabelColor: HEX.dk2,
  showLegend: true, legendPos: "b", legendFontSize: 11, legendFontFace: "+mn-lt", legendColor: HEX.dk2,
  showTitle: true, title: "First answer about a company, seconds", titleFontSize: 12, titleFontFace: "+mn-lt", titleColor: HEX.dk2,
});
T(s, bl(["Streamlit app over the library; no network at run time",
  "One pinned serving run builds all 48 companies; the app reads it",
  "The cache is reused only if a fingerprint of code, configs, models, evidence and packages matches; answers proved identical to a live build",
  "Observability: a per-call trace; the Results page as the dashboard; 0 of 40 calls failed",
  "Not built: public URL (paid plan), FastAPI, a built Docker image"]),
  { x: 5.6, y: 1.3, w: 3.9, h: 3.6, fontSize: 12.5, paraSpaceAfter: 6 });
s.addNotes("The course said one feature with deployment, serving and monitoring beats many unfinished ones. Our honest gap: local only, because Hugging Face Docker Spaces need a paid plan (#322).");

pres.addSection({ title: "Problems and learnings" });
// =============== 19. Problems (technical) ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Problems and learnings" });
s.addText("Problems we hit, and how we tackled them", { placeholder: "title" });
table(s, [
  ["Problem", "How we tackled it"],
  ["The reader could \"cheat\" by seeing XBRL tags", "A leakage check strips tags and concept names from the document channel"],
  ["Filers differ: signs, mislabelled fiscal years, statements in exhibits", "General rules in YAML configs, never company code; otherwise a GAP with a reason"],
  ["XBRL labels are noisy", "Label audit: 5 label errors in 3,475 validation rows; fixes as general rules"],
  ["The first E8 run returned 5 fabricated values", "Found before release, fixed, re-run: e8-0004 has 0 fabricated"],
  ["Quotes did not support risk cards (E4 0.704)", "Evidence v2: reranker threshold, EVIDENCE_GAP, labelled low-confidence text"],
  ["Fine-tuning M3 ran out of GPU memory on the Mac", "One fold per fresh process, in fp32; done in about 34 minutes"],
  ["First answer took up to 35 s", "Fingerprinted serving snapshot: 0.74 s p95, proven identical"],
  ["Answers were unreadable run-on lines", "Plain-language view built from observations; audit: 826 numbers, 0 new"],
], [4.3, 4.7], 1.12, { fontSize: 10.5, rowH: 0.42 });
s.addNotes("Every problem has an issue number in docs/issues_log.md with its cause and fix.");

// =============== 21. Learnings ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Problems and learnings" });
s.addText("What we learned", { placeholder: "title" });
[["Verify, don't generate", "Two independent readings caught errors and gave free labels; trust comes from checks, not model size."],
 ["Missing is not zero", "A visible GAP with a reason is a feature. E8's 0 fabricated values mattered more than any accuracy point."],
 ["Pre-register, then accept", "Rules fixed in advance stopped us shipping M3 or claiming forecast accuracy on noise."],
 ["Small models go far", "TF-IDF + LR came within 3 points of a fine-tuned encoder, on a CPU. Data and evaluation were the hard part."],
 ["Agents need guardrails", "Budgets, prerequisites and a gate made the agents safe; their reach is limited by what checks can see."],
 ["Evaluate before polishing", "E4 changed the evidence design; Vineet's blind questions exposed the planner's real coverage."],
].forEach(([h, b], i) => {
  const x = 0.5 + (i % 3) * 3.05, y = 1.2 + Math.floor(i / 3) * 1.85;
  hcard(s, x, y, 2.85, 1.7, i % 2 ? TINT.green : TINT.blue, h, b, i % 2 ? HEX.accent1 : HEX.accent4, 11.5);
});
s.addNotes("If we did it again: blind evaluation sets from more than one writer, an outside user early, and hosting budgeted from day one.");

// =============== 22. Limitations ===============
s = pres.addSlide({ masterName: "DARK", sectionTitle: "Problems and learnings" });
T(s, "Limitations and next steps", { x: 0.6, y: 0.4, w: 8.8, h: 0.7, fontSize: 32, bold: true, color: C.background1, fontFace: THEME.headFontFace });
[["Held-out 0.913 misses the 95% target", "a new reader chosen on validation, then a new pre-registered run"],
 ["Forecast does not beat naive (E6)", "better CFO drivers; raise actual-value verification coverage"],
 ["The agent sees 11 of 40 injected errors", "subtotal and cross-statement checks it can see"],
 ["Most risk cards are EVIDENCE_GAP", "better passage selection; re-label on new companies"],
 ["Local only; no MCP server, API or public URL", "wrap the tools as MCP; build the Dockerfile; host it"],
 ["One annotator; no outside user", "a second annotator; run the committed 20-minute user session"],
].forEach(([a, b], i) => {
  const y = 1.3 + i * 0.6;
  T(s, a, { x: 0.6, y, w: 4.3, h: 0.5, fontSize: 14, bold: true, color: C.background1, valign: "middle" });
  T(s, "→  " + b, { x: 5.0, y, w: 4.5, h: 0.5, fontSize: 13, color: "CFD8E3", valign: "middle" });
});
s.addNotes("Full list with issue numbers and fix paths: docs/limitations.md.");

// =============== 23. Contribution and close ===============
s = pres.addSlide({ masterName: "DARK", sectionTitle: "Problems and learnings" });
s.addText("Thank you", { placeholder: "title" });
s.addText("Next: the live demo, then questions.", { placeholder: "body" });
T(s, "In the repo: docs/final_results.md · docs/limitations.md · docs/blueprint.md (Version 3, as built) · eval/experiments.csv", { x: 0.6, y: 4.6, w: 8.8, h: 0.35, fontSize: 12, color: "9FB0C4" });
s.addNotes("Close, then hand over to the demo.");

pres.addSection({ title: "Backup" });
// =============== Backup divider ===============
s = pres.addSlide({ masterName: "DARK", sectionTitle: "Backup" });
s.addText("Backup slides", { placeholder: "title" });
s.addText("Detail for questions: two readings, the Correlation Agent, tools and MCP, E6, the engineering stack, process problems.", { placeholder: "body" });
// =============== 8. Two readings (data) ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("Two independent readings give us labels for free", { placeholder: "title" });
layerTag(s, ...LAYER.intel);
hcard(s, 0.5, 1.3, 4.3, 1.9, TINT.blue, "Document channel (trained)",
  "Reads the visible statement tables, as an analyst would. It never sees a tag, concept name or tagged value: a leakage check strips them.", HEX.accent4, 13);
hcard(s, 5.2, 1.3, 4.3, 1.9, TINT.grey, "Tag channel (parser)",
  "Reads the filer's machine tags from Company Facts, corroborated by the inline XBRL. The tags also label the training rows.", HEX.dk2, 13);
chip(s, 0.5, 3.75, 1.55, "VERIFIED", C.accent1);
T(s, "both agree", { x: 2.15, y: 3.75, w: 1.2, h: 0.32, fontSize: 12, valign: "middle" });
chip(s, 3.4, 3.75, 1.9, "SINGLE_CHANNEL", C.accent5);
T(s, "one source, flagged", { x: 5.4, y: 3.75, w: 1.5, h: 0.32, fontSize: 12, valign: "middle" });
chip(s, 7.0, 3.75, 0.9, "GAP", C.accent2);
T(s, "never zero", { x: 8.0, y: 3.75, w: 1.5, h: 0.32, fontSize: 12, valign: "middle" });
T(s, "Data: 48 companies split by company (32 train, 8 validation, 8 held-out), up to five 10-Ks each, sha256-pinned, an as-of date on every call.",
  { x: 0.5, y: 4.3, w: 9.0, h: 0.6, fontSize: 13, color: C.text2 });
s.addNotes("Why not just use XBRL? Most documents have no tags. Tags let us train and score the reader on 10-Ks, and two readings catch both reading and tagging errors. Split by company because rows repeat across a company's years.");

// =============== 10. Agentic workflow 1: Correlation Agent ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("Agentic workflow 1: the Correlation Agent", { placeholder: "title" });
layerTag(s, ...LAYER.intel);
[["Observe", "which accounting identities fail"], ["Diagnose", "which reading is the likely culprit"],
 ["Act", "re-pick a row, rescale or flip a sign"], ["Re-check", "fixed → CORRECTED; else REVIEW"],
].forEach(([h, b], i) => {
  const y = 1.25 + i * 0.85;
  s.addShape(pres.shapes.OVAL, { x: 0.5, y, w: 0.55, h: 0.55, fill: { color: HEX.accent2 }, line: { color: HEX.accent2 } });
  T(s, String(i + 1), { x: 0.5, y, w: 0.55, h: 0.55, fontSize: 16, bold: true, color: C.background1, align: "center", valign: "middle" });
  T(s, [{ text: h, options: { bold: true, breakLine: true } }, { text: b, options: { fontSize: 12, color: HEX.dk2 } }],
    { x: 1.2, y: y - 0.05, w: 3.0, h: 0.65, fontSize: 14, valign: "middle" });
});
T(s, "Guardrails: at most 5 actions per filing; it never inserts a figure; candidates scored by M1's probability × residual reduction.",
  { x: 0.5, y: 4.65, w: 4.0, h: 0.5, fontSize: 11, color: C.text2 });
stat(s, 4.7, 1.25, 2.2, "10 / 40", "injected errors corrected: 25.0% [12.5%, 35.0%]", C.accent1);
stat(s, 7.2, 1.25, 2.3, "0", "correct values wrongly changed", C.accent1);
stat(s, 4.7, 2.9, 2.2, "29 / 40", "injected errors invisible to every check", C.accent3);
stat(s, 7.2, 2.9, 2.3, "p = 0.25", "clean readings 90.5% → 90.8%: no significant gain", C.accent5, 32);
s.addNotes("A ReAct-style observe-act loop with an objective success test (the identities); its choice rule is fixed, not learned. e2-0003, not blind. Lesson: an agent can only fix what its checks can see.");

// =============== 13. Tools and MCP ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("Tool calling: a typed tool layer, MCP-ready", { placeholder: "title" });
layerTag(s, ...LAYER.intel);
table(s, [
  ["Tool (FinAgentTools)", "What it returns"],
  ["statement_set(ticker, as_of)", "verified three statements with provenance and statuses"],
  ["forecast, scenarios", "three-year model under Base, Moderate and Severe, or GAP with its reason"],
  ["credit, liquidity, debt service", "ratios, headroom and shortfall, scoped debt-service coverage"],
  ["insights, explain", "risk signals R1–R11 with quotes or EVIDENCE_GAP"],
  ["fact, compare, statements check", "one value or a comparison, with its basis"],
], [3.6, 5.4], 1.15, { fontSize: 11.5, rowH: 0.38 });
hcard(s, 0.5, 3.6, 4.35, 1.3, TINT.green, "Every call", "takes an as_of date, refuses later filings and held-out misuse, and returns typed records the gate can check.", HEX.accent1, 12);
hcard(s, 5.15, 3.6, 4.35, 1.3, TINT.grey, "MCP: not built", "The tools already have typed inputs and outputs; wrapping them as an MCP server is a small next step. No external LLM API is called.", HEX.dk2, 12);
s.addNotes("Be clear in the viva: tool calling is a typed Python interface, not the MCP protocol. The planner plays the host; an MCP server would let any MCP client use the same tools.");

// =============== 16. E6 detail ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("E6: a pre-registered backtest against naive", { placeholder: "title" });
layerTag(s, ...LAYER.evalL);
T(s, "8 validation companies × 4 filing-date origins; only data filed by each origin; first-reported actuals only. Run once at commit 698bb67.",
  { x: 0.5, y: 1.15, w: 9.0, h: 0.55, fontSize: 13 });
table(s, [
  ["Metric", "Coverage", "Naive APE − model APE [95% CI]", "Decision"],
  ["Revenue", "28 / 32", "−0.019 [−0.072, +0.027]", "no claim"],
  ["Operating income", "15 / 32", "−0.025 [−0.111, +0.058]", "no claim"],
  ["Operating cash flow", "14 / 32", "−0.100 [−0.166, −0.012]", "naive better"],
  ["Capex", "28 / 32", "−0.009 [−0.040, +0.034]", "no claim"],
  ["Free cash flow", "14 / 32", "+0.035 [−0.241, +0.310]", "no claim"],
], [2.2, 1.3, 3.6, 1.9], 1.8, { fontSize: 12, rowH: 0.36,
  cell: (i, j, c) => c === "naive better" ? { bold: true, color: HEX.accent3 } : null });
T(s, "Leakage tests fail the run if any input was filed after its origin. The claim we can make is integrity, not accuracy.",
  { x: 0.5, y: 4.35, w: 9.0, h: 0.5, fontSize: 12, color: C.text2 });
s.addNotes("Fix path: better CFO drivers (working capital) and more verified actuals, then a new pre-registered run. Point-in-time discipline is the course's leakage point applied to time series.");

// =============== 18. Engineering stack ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("How it was built: stack and discipline", { placeholder: "title" });
layerTag(s, ...LAYER.deploy);
[["Data and parsing", "Python 3.11, lxml, pandas, pyarrow, pydantic records, Decimal for reported amounts"],
 ["Models", "scikit-learn (M1, intent), LightGBM (M2), PyTorch on Apple MPS (M3), sentence-transformers"],
 ["Retrieval", "rank-bm25, bge-small-en-v1.5, MiniLM cross-encoder, nli-deberta-v3-xsmall judge; pinned, offline"],
 ["Model output", "openpyxl workbooks with live formulas, recalculated and checked in LibreOffice"],
 ["Serving", "Streamlit app; Dockerfile (unbuilt); pinned serving runs"],
 ["Quality", "1,573 pytest tests; experiments.csv; 376 logged issues; 37 approved design changes"],
 ["AI-assisted coding", "Claude Code wrote code to the team's blueprint and decisions; every change reviewed and tested"],
].forEach(([h, b], i) => {
  const y = 1.15 + i * 0.54;
  card(s, 0.5, y, 9.0, 0.46, i % 2 ? "FFFFFF" : TINT.grey, h);
  T(s, h, { x: 0.65, y, w: 2.1, h: 0.46, fontSize: 12, bold: true, color: C.accent6, valign: "middle" });
  T(s, b, { x: 2.8, y, w: 6.6, h: 0.46, fontSize: 12, valign: "middle" });
});
s.addNotes("The design, data split, labels and every decision are ours; the coding assistant was a tool, like the libraries. Project rules: no company-specific code, missing is never zero, never touch the pinned evidence.");

// =============== 20. Problems (process) ===============
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("Process problems: keeping the science honest", { placeholder: "title" });
hcard(s, 0.5, 1.2, 2.85, 2.6, TINT.blue, "Leakage risk",
  "Held-out companies were refused by every tool during development and opened once, for E1. E6 inputs are filtered by filing date, with tests that fail on any later input.", HEX.accent4, 12);
hcard(s, 3.575, 1.2, 2.85, 2.6, TINT.amber, "Temptation to tune",
  "Decision rules written before results: M3 ships only if its interval is above 0; E6 claims only if its interval is above 0. Both said no, and we kept the rule.", HEX.accent2, 12);
hcard(s, 6.65, 1.2, 2.85, 2.6, TINT.violet, "Time",
  "Phase 9 cut to essentials, keeping the never-cut list (held-out E1, verification, the gate, E2, E8). Hosting, also on it, needed a paid plan and was dropped; every cut is a logged limitation.", HEX.accent6, 12);
stat(s, 0.5, 3.8, 2.85, "376", "issue-log entries, each with its cause and fix (docs/issues_log.md)", C.accent4, 30);
stat(s, 3.575, 3.8, 2.85, "37", "design changes, each recorded and approved", C.accent2, 30);
stat(s, 6.65, 3.8, 2.85, "1", "held-out run, never repeated", C.accent6, 30);
s.addNotes("Judge vs human: our local NLI judge read 'supported' as entailment while Vineet required the reason; we logged the mismatch before labelling and report kappa 0.411.");

(async () => {
  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();

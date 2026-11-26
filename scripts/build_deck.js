// FinAgent final deck (12 slides, blueprint Version 3 section 17.2). Numbers from docs/final_results.md.
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.PPTX_SKILL + "/scripts/apply_theme.js");

const OUT = process.argv[2] || "FinAgent_deck.pptx";
const THEME = {
  name: "FinAgent",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "17212F", lt1: "FFFFFF", dk2: "2C3E57", lt2: "EEF2F6",
    accent1: "1E7A68", // verified green
    accent2: "C98A12", // gap amber
    accent3: "B4473A", // negative red
    accent4: "3F6BA8", // trained model blue
    accent5: "7D8A9C", // neutral
    accent6: "5E4B8B",
    hlink: "3F6BA8", folHlink: "5E4B8B",
  },
};
const HEX = THEME.colors;

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10 x 5.625
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "FinAgent: final presentation";
pres.author = "Vineet Ojha";
const C = pres.SchemeColor;

pres.defineSlideMaster({
  title: "DARK",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 1.55, w: 8.8, h: 1.1,
        fontSize: 48, bold: true, color: C.background1, fontFace: THEME.headFontFace, valign: "bottom", align: "left", margin: 0 },
        text: "Title" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.6, y: 2.75, w: 8.8, h: 1.2,
        fontSize: 18, color: "CFD8E3", valign: "top", align: "left", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: C.background1 },
  margin: [0.5, 0.5, 0.5, 0.5],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.5, y: 0.3, w: 9.0, h: 0.75,
        fontSize: 26, bold: true, color: C.text1, fontFace: THEME.headFontFace, valign: "middle", align: "left", margin: 0 },
        text: "Title" } },
    { text: { text: "FinAgent  ·  final presentation", options: { x: 0.5, y: 5.22, w: 5, h: 0.25,
        fontSize: 9, color: HEX.accent5, margin: 0 } } },
  ],
  slideNumber: { x: 9.1, y: 5.22, w: 0.4, h: 0.25, fontSize: 9, color: HEX.accent5, align: "right" },
});

const T = (slide, text, o) => slide.addText(text, Object.assign({ isTextBox: true, fontSize: 14,
  color: C.text1, margin: 0, valign: "top" }, o));

// a rounded card with a tinted fill
function card(slide, x, y, w, h, fill, name) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08,
    fill: { color: fill }, line: { color: fill }, objectName: name });
}
// a small status chip
function chip(slide, x, y, w, label, color) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h: 0.32, rectRadius: 0.16,
    fill: { color }, line: { color } });
  T(slide, label, { x, y, w, h: 0.32, fontSize: 11, bold: true, color: C.background1,
    align: "center", valign: "middle" });
}
// a big number with a label under it
function stat(slide, x, y, w, big, label, color, bigSize = 40) {
  T(slide, big, { x, y, w, h: 0.75, fontSize: bigSize, bold: true, color, fontFace: THEME.headFontFace,
    valign: "bottom" });
  T(slide, label, { x, y: y + 0.8, w, h: 0.6, fontSize: 12, color: C.text2 });
}

// ---------- 1. Title ----------
pres.addSection({ title: "Opening" });
let s = pres.addSlide({ masterName: "DARK", sectionTitle: "Opening" });
s.addText("FinAgent", { placeholder: "title" });
s.addText("Reads a 10-K two independent ways, checks its numbers against the filer's own XBRL, " +
  "builds a model that balances, and only says what the filing supports.", { placeholder: "body" });
T(s, "ML for Finance  ·  final  ·  5 December 2026", { x: 0.6, y: 4.6, w: 8.8, h: 0.35,
  fontSize: 13, color: "9FB0C4" });
s.addNotes("One-line pitch from blueprint Version 3 section 2. FinAgent works offline on pinned 10-K " +
  "filings for 48 US companies and never calls an external AI API.");

// ---------- 2. Problem ----------
pres.addSection({ title: "Problem and product" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Problem and product" });
s.addText("An analyst's first model of a company takes days", { placeholder: "title" });
T(s, "Before any judgement, someone has to read the 10-K, re-key three statements, check they tie, " +
  "and build a forecast with downside cases. Most of that time is mechanical, and copying errors " +
  "are easy to miss.", { x: 0.5, y: 1.3, w: 4.2, h: 1.6, fontSize: 15 });
T(s, "Users: credit and equity analysts, and anyone who needs a checked starting model quickly.",
  { x: 0.5, y: 3.05, w: 4.2, h: 0.8, fontSize: 14, color: C.text2 });
card(s, 5.1, 1.3, 2.05, 2.6, C.background2, "manual");
card(s, 7.35, 1.3, 2.15, 2.6, C.background2, "finagent");
stat(s, 5.3, 1.5, 1.7, "≈20 h", "by hand for one company's model (Vineet's estimate, not measured; D10)", C.accent3);
stat(s, 7.55, 1.5, 1.8, "36 s", "FinAgent's full build of one company, median (max 92 s; serving-0001)", C.accent1);
T(s, "First answer about a company: 0.74 s at p95 (e7-0002).", { x: 5.1, y: 4.15, w: 4.4, h: 0.4,
  fontSize: 13, color: C.text2 });
s.addNotes("The 20 hours is an estimate from experience, not a measurement; say so. The 36 s median covers " +
  "statements, three-scenario model, credit, risk cards and the recalculated workbook.");

// ---------- 3. What it does ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Problem and product" });
s.addText("One local app, three pages, 48 companies", { placeholder: "title" });
const pages = [
  ["Ask", "A question in plain English. The planner runs only the tools it needs; every number passes the observation gate; the answer comes in plain words with a trace."],
  ["Company", "Verified statements, a three-year forecast under Base, Moderate and Severe, credit and liquidity, risk cards with quoted evidence, and a linked Excel workbook."],
  ["Results", "Every evaluation's headline with its interval and decision, latency (E7) and the limitations: the dashboard."],
];
pages.forEach(([h, b], i) => {
  const x = 0.5 + i * 3.05;
  card(s, x, 1.3, 2.85, 2.55, C.background2, "page-" + h);
  T(s, h, { x: x + 0.2, y: 1.45, w: 2.45, h: 0.45, fontSize: 20, bold: true, color: C.accent4,
    fontFace: THEME.headFontFace });
  T(s, b, { x: x + 0.2, y: 1.95, w: 2.45, h: 1.8, fontSize: 13 });
});
chip(s, 0.5, 4.2, 2.3, "8 Measured (validation)", C.accent1);
chip(s, 2.95, 4.2, 2.3, "32 In-sample (train)", C.accent5);
chip(s, 5.4, 4.2, 2.3, "8 Unseen (held-out)", C.accent4);
T(s, "Every company carries its label.", { x: 7.85, y: 4.2, w: 1.7, h: 0.32, fontSize: 12,
  color: C.text2, valign: "middle" });
s.addNotes("Served from the pinned run serving-0002 (BC36, BC37). Held-out companies are served but were never " +
  "used for debugging or tuning. No public URL: Hugging Face Docker Spaces need a paid plan (#322).");

// ---------- 4. Architecture ----------
pres.addSection({ title: "How it works" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "How it works" });
s.addText("Architecture: two readings, one gate", { placeholder: "title" });
function box(x, y, w, h, label, sub, fill, txt = C.text1) {
  card(s, x, y, w, h, fill, label);
  T(s, [{ text: label, options: { bold: true, breakLine: true } }, { text: sub, options: { fontSize: 10 } }],
    { x: x + 0.08, y, w: w - 0.16, h, fontSize: 12, color: txt, align: "center", valign: "middle" });
}
const arrow = (x1, y1, x2, y2) => s.addShape(pres.shapes.LINE, { x: x1, y: y1, w: x2 - x1, h: y2 - y1,
  line: { color: HEX.accent5, width: 1.25, endArrowType: "triangle" } });
box(0.5, 1.2, 9.0, 0.55, "A  Investigation planner", "trained goal compiler · action registry · step budget · trace", "DCEFE8");
box(0.5, 2.05, 1.6, 1.0, "Evidence store", "pinned 10-Ks · sha256 · as-of", HEX.lt2);
box(2.4, 2.05, 1.9, 0.45, "Document channel", "M1 v3, trained", "DCE6F4");
box(2.4, 2.6, 1.9, 0.45, "Tag channel", "inline XBRL + Company Facts", HEX.lt2);
box(4.6, 2.05, 1.9, 1.0, "Verification", "agree → VERIFIED · else Correlation Agent", "F7E9CF");
box(6.8, 2.05, 2.7, 1.0, "Validated statement set", "REPORTED_FACT · CALCULATION · provenance", HEX.lt2);
box(0.5, 3.4, 2.9, 0.75, "Model + scenarios", "balances every year (F1–F5)", HEX.lt2);
box(3.55, 3.4, 2.9, 0.75, "Credit and liquidity", "ratios · headroom · scoped DSCR", HEX.lt2);
box(6.6, 3.4, 2.9, 0.75, "Risk cards + retrieval", "R1–R11 · BM25 + bge + reranker", "DCE6F4");
box(0.5, 4.45, 9.0, 0.55, "B  Observation gate", "issuer · period · metric · unit · classification · accession · filed ≤ as-of", "DCEFE8");
arrow(2.1, 2.55, 2.4, 2.28); arrow(2.1, 2.55, 2.4, 2.82);
arrow(4.3, 2.28, 4.6, 2.45); arrow(4.3, 2.82, 4.6, 2.65); arrow(6.5, 2.55, 6.8, 2.55);
arrow(8.15, 3.05, 8.15, 3.4);
s.addNotes("Blue: models the team trained or hosts. Green: agentic control. Amber: verification. Grey: deterministic " +
  "code. Differences from the Version 2 figure: no HTTP API; a Streamlit app; the Results page is the dashboard.");

// ---------- 5. Evidence and two readings ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "How it works" });
s.addText("Every number is read twice, independently", { placeholder: "title" });
card(s, 0.5, 1.3, 4.3, 2.3, "DCE6F4", "doc");
T(s, "Document channel", { x: 0.7, y: 1.42, w: 3.9, h: 0.4, fontSize: 18, bold: true, color: C.accent4, fontFace: THEME.headFontFace });
T(s, "Reads the visible statement tables, as an analyst would. It never sees a tag, concept name or tagged value (leakage-checked).",
  { x: 0.7, y: 1.9, w: 3.9, h: 1.6, fontSize: 13 });
card(s, 5.2, 1.3, 4.3, 2.3, C.background2, "tag");
T(s, "Tag channel", { x: 5.4, y: 1.42, w: 3.9, h: 0.4, fontSize: 18, bold: true, color: C.text2, fontFace: THEME.headFontFace });
T(s, "Reads the filer's machine tags from Company Facts, corroborated by the filing's inline XBRL.",
  { x: 5.4, y: 1.9, w: 3.9, h: 1.6, fontSize: 13 });
chip(s, 0.5, 3.85, 1.55, "VERIFIED", C.accent1);
T(s, "both agree", { x: 2.15, y: 3.85, w: 1.2, h: 0.32, fontSize: 12, valign: "middle" });
chip(s, 3.4, 3.85, 1.9, "SINGLE_CHANNEL", C.accent5);
T(s, "one source, flagged", { x: 5.4, y: 3.85, w: 1.5, h: 0.32, fontSize: 12, valign: "middle" });
chip(s, 7.0, 3.85, 0.9, "GAP", C.accent2);
T(s, "never zero", { x: 8.0, y: 3.85, w: 1.5, h: 0.32, fontSize: 12, valign: "middle" });
T(s, "Pinned evidence: up to five 10-Ks per company, sha256 manifest, an as-of date on every call, no network at run time.",
  { x: 0.5, y: 4.4, w: 9.0, h: 0.5, fontSize: 13, color: C.text2 });
s.addNotes("Why not just use XBRL? Most documents have no tags. Tags let us train and score the reader on 10-Ks, " +
  "and two readings catch both reading errors and tagging errors.");

// ---------- 6. Model ladder ----------
pres.addSection({ title: "Results" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Results" });
s.addText("E1: the trained reader, and why M3 is not shipped", { placeholder: "title" });
s.addChart(pres.charts.BAR, [{ name: "Validation field accuracy",
  labels: ["M3 bge-small", "M2 LightGBM", "M1 v3 TF-IDF+LR", "M0 rules"], values: [0.929, 0.865, 0.905, 0.773] }], {
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
T(s, "Paired M3 − M1 v3: +0.029 [0.000, +0.055]. The pre-registered rule needed the lower bound above 0; it is exactly 0, so M1 v3 stays.",
  { x: 5.9, y: 3.35, w: 3.4, h: 1.35, fontSize: 12 });
s.addNotes("e1-0009 and e1-0010. The 95% target is not met. Labels come free from the held-back XBRL tags; " +
  "the split is by company because rows repeat across a company's years.");

// ---------- 7. Correlation Agent ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Results" });
s.addText("E2: the Correlation Agent is a safe but narrow net", { placeholder: "title" });
const steps = ["Check identities", "Diagnose", "Act: re-pick, rescale, flip sign", "Re-check, else REVIEW"];
steps.forEach((t, i) => {
  const y = 1.3 + i * 0.85;
  s.addShape(pres.shapes.OVAL, { x: 0.5, y, w: 0.55, h: 0.55, fill: { color: HEX.accent2 }, line: { color: HEX.accent2 } });
  T(s, String(i + 1), { x: 0.5, y, w: 0.55, h: 0.55, fontSize: 16, bold: true, color: C.background1, align: "center", valign: "middle" });
  T(s, t, { x: 1.2, y, w: 3.0, h: 0.55, fontSize: 14, valign: "middle" });
});
stat(s, 4.7, 1.3, 2.2, "10 / 40", "injected errors corrected: 25.0% [12.5%, 35.0%]", C.accent1);
stat(s, 7.2, 1.3, 2.3, "0", "correct values wrongly changed", C.accent1);
stat(s, 4.7, 2.95, 2.2, "29 / 40", "injected errors invisible to every check", C.accent3);
stat(s, 7.2, 2.95, 2.3, "p = 0.25", "clean readings 90.5% → 90.8%: no significant gain", C.accent5, 32);
s.addNotes("e2-0003; not blind (design changed after e2-0001). It never inserts a figure; at most 5 actions per filing. " +
  "Fix path: more checks the agent can see (subtotals, cross-statement ties).");

// ---------- 8. Model and E6 ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Results" });
s.addText("E6: the forecast balances, but does not beat naive", { placeholder: "title" });
T(s, "A forecast is shown only when checks F1–F5 pass in every projected year and scenario; otherwise it is GAP with its reason.",
  { x: 0.5, y: 1.2, w: 9.0, h: 0.55, fontSize: 14 });
const hdr = { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 }, fontSize: 12 };
const rows = [
  ["Metric", "Coverage", "Naive APE − model APE [95% CI]", "Decision"],
  ["Revenue", "28 / 32", "−0.019 [−0.072, +0.027]", "no claim"],
  ["Operating income", "15 / 32", "−0.025 [−0.111, +0.058]", "no claim"],
  ["Operating cash flow", "14 / 32", "−0.100 [−0.166, −0.012]", "naive better"],
  ["Capex", "28 / 32", "−0.009 [−0.040, +0.034]", "no claim"],
  ["Free cash flow", "14 / 32", "+0.035 [−0.241, +0.310]", "no claim"],
].map((r, i) => r.map((c, j) => ({ text: c, options: i === 0 ? hdr :
  { fontSize: 12, color: (j === 3 && c === "naive better") ? HEX.accent3 : HEX.dk1,
    bold: j === 3 && c === "naive better", fill: { color: i % 2 ? "FFFFFF" : HEX.lt2 } } })));
s.addTable(rows, { x: 0.5, y: 1.9, w: 9.0, colW: [2.2, 1.3, 3.6, 1.9], rowH: 0.36, border: { type: "none" }, margin: [0, 0.1, 0, 0.1] });
T(s, "Pre-registered at 698bb67, run once (e6-0001): 8 validation companies × 4 filing-date origins, first-reported actuals only.",
  { x: 0.5, y: 4.3, w: 9.0, h: 0.5, fontSize: 12, color: C.text2 });
s.addNotes("Honest negative: drivers are simple historical rules. The claim we can make is integrity (balances, gaps " +
  "instead of guesses), not accuracy. Fix path: CFO drivers (working capital), then a new pre-registered E6.");

// ---------- 9. Planner ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Results" });
s.addText("E5: the trained planner beats the grammar", { placeholder: "title" });
s.addChart(pres.charts.BAR, [{ name: "Goal accuracy", labels: ["Regex grammar", "Trained compiler"], values: [26.1, 73.9] }], {
  x: 0.4, y: 1.2, w: 4.4, h: 3.6, barDir: "col", chartColors: [HEX.accent5, HEX.accent4],
  showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: '0.0"%"', dataLabelFontSize: 12,
  dataLabelFontFace: "+mn-lt", dataLabelColor: HEX.dk1, valAxisHidden: true, valAxisMaxVal: 100,
  valGridLine: { style: "none" }, catAxisLabelFontSize: 12, catAxisLabelFontFace: "+mn-lt", catAxisLabelColor: HEX.dk2,
  showLegend: false, showTitle: true, title: "Goal accuracy, 92 blind questions (e5-0001, e5-0002)",
  titleFontSize: 12, titleFontFace: "+mn-lt", titleColor: HEX.dk2,
});
card(s, 5.1, 1.3, 4.4, 3.5, C.background2, "planner");
T(s, [
  { text: "68 / 92 goals correct, against 24 / 92 for the grammar", options: { bullet: true, breakLine: true } },
  { text: "Precision fell from 100.0% to 93.2%: the expectation is not met", options: { bullet: true, breakLine: true } },
  { text: "Vineet's blind questions: 29 / 52, far below the builder's 39 / 40", options: { bullet: true, breakLine: true } },
  { text: "The gate checks issuer, period, metric, unit, classification, accession and filed ≤ as-of", options: { bullet: true } },
], { x: 5.3, y: 1.5, w: 4.0, h: 3.2, fontSize: 13, paraSpaceAfter: 8 });
s.addNotes("Intent v3 is TF-IDF + logistic regression; slots are rule-based. Below threshold 0.925 the grammar takes " +
  "over, then a clarification. The shipped v3 is measured only post-hoc (e5-0003).");

// ---------- 10. Summary table ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Results" });
s.addText("All eight evaluations, with their baselines", { placeholder: "title" });
const sum = [
  ["", "Headline", "Baseline", "Decision"],
  ["E1", "Held-out M1 v3 0.913 [0.846, 0.961]", "M0 rules 0.773 (validation)", "M3 not shipped"],
  ["E2", "10/40 injected errors fixed; 0 wrongly changed", "No agent", "Safety net"],
  ["E3", "MRR@5 BM25 0.677; best change +0.048 [−0.049, +0.140]", "BM25", "BM25 stays"],
  ["E4", "Unsupported 0.704 [0.519, 0.852] of 27 cards", "Vineet's labels", "Evidence v2 (post-hoc)"],
  ["E5", "68/92 = 73.9% goal accuracy", "Grammar 26.1%", "Precision not kept"],
  ["E6", "No claim on any row; CFO naive better", "Naive last value", "No claim"],
  ["E7", "First answer 0.74 s p95 (was 16.38 s)", "≈20 h by hand", "Light run"],
  ["E8", "1150/1152 correct; 0 fabricated values", "Full evidence", "Target met"],
].map((r, i) => r.map((c, j) => ({ text: c, options: i === 0 ? hdr :
  { fontSize: 11, bold: j === 0, color: HEX.dk1, fill: { color: i % 2 ? "FFFFFF" : HEX.lt2 } } })));
s.addTable(sum, { x: 0.5, y: 1.2, w: 9.0, colW: [0.55, 4.1, 2.2, 2.15], rowH: 0.38, border: { type: "none" }, margin: [0, 0.08, 0, 0.08] });
s.addNotes("Every number is in docs/final_results.md with its run id; a test checks all 403 of them against their sources.");

// ---------- 11. Speed and safety ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Results" });
s.addText("E7, E8: fast, and gaps instead of guesses", { placeholder: "title" });
s.addChart(pres.charts.BAR, [
  { name: "e7-0001", labels: ["Cold p50", "Cold p95", "Cold max"], values: [0.25, 16.38, 35.53] },
  { name: "e7-0002", labels: ["Cold p50", "Cold p95", "Cold max"], values: [0.22, 0.74, 0.79] },
], {
  x: 0.4, y: 1.2, w: 5.2, h: 3.7, barDir: "col", barGrouping: "clustered", chartColors: [HEX.accent5, HEX.accent1],
  showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0.00", dataLabelFontSize: 10,
  dataLabelFontFace: "+mn-lt", dataLabelColor: HEX.dk1, valAxisHidden: true, valGridLine: { style: "none" },
  catAxisLabelFontSize: 11, catAxisLabelFontFace: "+mn-lt", catAxisLabelColor: HEX.dk2,
  showLegend: true, legendPos: "b", legendFontSize: 11, legendFontFace: "+mn-lt", legendColor: HEX.dk2,
  showTitle: true, title: "First answer about a company, seconds", titleFontSize: 12, titleFontFace: "+mn-lt", titleColor: HEX.dk2,
});
card(s, 5.9, 1.3, 3.6, 3.5, C.background2, "e8");
stat(s, 6.1, 1.45, 3.2, "0", "fabricated values when evidence is hidden", C.accent1, 44);
stat(s, 6.1, 2.85, 3.2, "99.8%", "of 1,152 goals answered correctly with evidence removed (e8-0004)", C.accent1, 32);
s.addNotes("E7 speed-up: Ask reuses the serving run's statement sets when the build fingerprint matches (BC37); answers " +
  "proved identical to a live build. E8 is not blind: e8-0001 found 5 fabricated values, fixed before e8-0003.");

// ---------- 12. Limitations ----------
pres.addSection({ title: "Close" });
s = pres.addSlide({ masterName: "DARK", sectionTitle: "Close" });
T(s, "Limitations and next steps", { x: 0.6, y: 0.4, w: 8.8, h: 0.7, fontSize: 32, bold: true,
  color: C.background1, fontFace: THEME.headFontFace });
const lim = [
  ["Held-out 0.913 misses the 95% target", "a new reader chosen on validation, then a new pre-registered run"],
  ["Forecast does not beat naive (E6)", "better CFO drivers; raise actual-value verification coverage"],
  ["The agent sees 11 of 40 injected errors", "subtotal and cross-statement checks it can see"],
  ["Most risk cards are EVIDENCE_GAP", "better passage selection; re-label on new companies"],
  ["Local only; no API or public URL", "build the Dockerfile; host it when funded"],
  ["One annotator; no outside user yet", "a second annotator and user sessions"],
];
lim.forEach(([a, b], i) => {
  const y = 1.3 + i * 0.6;
  T(s, a, { x: 0.6, y, w: 4.3, h: 0.5, fontSize: 14, bold: true, color: C.background1, valign: "middle" });
  T(s, "→  " + b, { x: 5.0, y, w: 4.5, h: 0.5, fontSize: 13, color: "CFD8E3", valign: "middle" });
});
s.addNotes("Full list with issue numbers and fix paths: docs/limitations.md. What we can stand behind: every number is " +
  "traced to the filing, gaps are shown not filled, and every evaluation was reported whatever it showed.");

(async () => {
  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();

# FinAgent: installation and user guide

**Who this is for:** team members, graders and anyone trying FinAgent for the first time. No knowledge of the code is needed. For the design, see `docs/blueprint.md`; for measured results, see `docs/final_results.md`.

## 1. What FinAgent is

FinAgent is a local web app for analysing the annual 10-K filings of 48 large US companies. It reads each company's financial statements in two independent ways: a trained model reads the tables as an analyst would, and a parser reads the filer's machine-readable XBRL tags. A figure is marked **Verified** only when the two agree.

From the verified figures it builds:
- the three financial statements;
- a three-year forecast under three scenarios (Base, Moderate, Severe);
- credit ratios, liquidity and debt service;
- risk signals with quoted evidence from the filing;
- a linked Excel workbook.

You can ask questions in plain English, and every number in an answer is checked against its source filing before it is shown. Everything runs on your own computer from pinned copies of the filings. Nothing is sent to the internet, and no external AI service is used.

**What it is not:** it does not give investment advice, read quarterly reports or news, or cover banks, insurers or REITs.

## 2. What you need

| Item | Requirement |
|---|---|
| Computer | macOS (tested on Apple Silicon), Linux or Windows; about 2 GB free disk; 4 GB RAM or more |
| Python | Version 3.11 |
| Browser | Any modern browser (Chrome, Safari, Edge, Firefox) |
| The working copy | The repository **plus** the large files that are not in git (section 3.2) |
| Internet | Only to install Python packages; the app itself runs offline |

## 3. Installation

### 3.1 Get the code and install the packages

Open a terminal (on Windows, PowerShell) and run these commands one per line:

```bash
git clone https://github.com/vojha95/finagent.git
cd finagent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ml,dl,retrieval,excel,serve]"
```

On Windows, activate the environment with `.venv\Scripts\activate` instead of the `source` line.

The repository is public. It holds the code, configs, pinned SEC evidence, frozen labels and docs; the large files in section 3.2 are not in it.

### 3.2 Add the large files that are not in git

The trained models, the search cache and the pre-built company data are too large for git (about 820 MB together). Without them the app cannot start. Get a copy of these four folders from the team (for example as a zip from the machine that built them) and place them in the `finagent` folder:

| Folder | What it holds | Size (approx.) |
|---|---|---|
| `models/` | The trained reader, the question classifier and the pinned search models | 670 MB |
| `derived/` | The passage-embedding cache used for evidence search | 90 MB |
| `runs/` | The pre-built data for all 48 companies (`serving-0002`) and the evaluation runs the Results page shows | 60 MB+ |
| `datasets/` | Training data (only needed to retrain, not to use the app) | — |

### 3.3 Check the installation

```bash
python -m finagent.ui.verify
python -m finagent.ui.snapshot check --serving-run serving-0002
```

- The first must print **OK** with every file matching its sha256 fingerprint.
- The second should say **usable**. If it does not, the app still works, but the first answer about each company is slower (15 to 35 seconds instead of under one second).

**macOS only:** if Python crashes with "segmentation fault" when the app loads its models, the LightGBM and PyTorch libraries are using two different copies of the OpenMP runtime. Install one copy (`brew install libomp`, or `conda install llvm-openmp`) and point PyTorch's bundled `torch/lib/libomp.dylib` at it. The README has the details.

## 4. Starting and stopping the app

Start it from the `finagent` folder, with the environment active. Type the command **on one line**:

```bash
streamlit run finagent/ui/app.py
```

Your browser opens at **http://localhost:8501**; if it does not, paste that address into the browser. The models load once at start, which takes a few seconds; wait for the title and tagline to appear.

To stop the app, press **Ctrl+C** in the terminal. If port 8501 is already in use (an older copy is still running), stop that one first, or use the address the new one prints (usually 8502).

## 5. The screen at a glance

The **sidebar** on the left has three controls:

| Control | What it does |
|---|---|
| **Page** | Switches between Ask, Company and Results |
| **Company** | Picks one of the 48 companies. Each carries a label, shown below |
| **As of** | The date the analysis is "as of": only filings published by that date are used. Leave it at the default, **24 Nov 2026**, unless you want to see what was known earlier |

**Company labels:**

| Label | Meaning |
|---|---|
| **Measured** (8 companies) | Validation companies: the accuracy figures in Results were measured on these |
| **In-sample** (32 companies) | The reader was trained on these companies' rows, so its accuracy here is optimistic |
| **Unseen** (8 companies) | Held-out companies, measured once in the final test and never used to tune anything |

## 6. Ask page: questions in plain English

Type a question in the box and click **Ask**, or click one of the example buttons, which fill in the question and ask it straight away. Questions are about the company selected in the sidebar.

### 6.1 What you can ask

| Kind | Example |
|---|---|
| A reported figure | What were total assets in the latest reported year? |
| A comparison | Compare Amazon and Colgate-Palmolive net income in FY2022 |
| A statements check | Reconcile the three statements for Amazon for FY2021 |
| A forecast | Forecast revenue under the Moderate scenario |
| Credit | Show the current ratio for Abbott for the latest reported year |
| Liquidity, with a conditional debt check | Assess severe liquidity; if there is a shortfall, check debt service |
| An explanation | Why did revenue change in the latest year? |
| Risk signals | What are the main risk signals? |

Tips:
- Ask about **one fiscal year** at a time ("the latest year" or "FY2024").
- Name a **scenario** for forecasts and liquidity: Base, Moderate or Severe.
- To compare two companies, start the question with **Compare** and name both.

### 6.2 Reading an answer

An answer has three layers:

1. **The headline:** one or two plain sentences, for example "Microsoft, FY2026 (10-K filed 29 Jul 2026): 5 of 11 risk signals fired."
2. **The findings:** one line per figure or signal, each with a small **badge** (hover over it for a plain explanation). Risk answers list the signals that fired first; those that did not are folded under "Checked, not flagged".
3. **Two expandable sections:**
   - **How this was computed:** the technical detail, including the source filing, the formula or rule, thresholds and the gate's checks.
   - **Raw answer and trace:** the full original answer, every tool the planner called, and a download of the trace.

**Badges:**

| Badge | Meaning |
|---|---|
| Verified | Read from the filing text and the XBRL tags, and they agree |
| One source | Only one source could be read, so it is not cross-checked |
| Corrected | The text was first misread; the Correlation Agent corrected it so the statements tie out |
| Calculated | Computed from verified reported figures and recomputed a second way |
| Model forecast | A projection of the three-statement model; its five consistency checks pass |
| Quoted | Supported by a sentence quoted word for word from the filing |
| Not explained in filing | No sentence in the filing explains the movement clearly enough to quote |
| Sources disagree | The two readings differ, so no value is given |
| Not available | Not reported or not readable. FinAgent never fills a missing value with zero |
| Withheld by design | Not computed on purpose, because its inputs cannot be established reliably |
| Rejected | The value failed a check and is not shown |

A grey box labelled **"Possibly related (low confidence, not evidence)"** may appear under "Not explained in filing". It is the closest sentence the search found. It is shown for context only and never counts as evidence.

### 6.3 When FinAgent asks you back

If a question is ambiguous or outside what FinAgent covers, it asks a short question back, often with a suggested rephrasing you can click. Examples:
- two years in one question: "Which fiscal year do you mean? Each answer covers one year.";
- advice ("Should I buy…?"): FinAgent explains that it does not give investment advice, and offers the risk signals or a forecast instead;
- a company outside the 48: "That company is not one FinAgent covers."

## 7. Company page: the full analysis

Pick a company in the sidebar and open **Company**. The top row shows headline figures (revenue, operating income, net income, operating cash flow, total assets and more) as cards. Below them are five tabs:

| Tab | What you see |
|---|---|
| **Statements** | Income statement, balance sheet and cash flow statement. Columns marked **A** are actual (reported) years, **E** are forecast years. Hover over an "n/a" cell to see why it is missing |
| **Forecast & checks** | The forecast under the scenario you pick (Base, Moderate, Severe), the five model checks (F1–F5) and the assumptions behind the forecast. If the forecast is not available for a company, the reason is shown instead of numbers |
| **Credit & liquidity** | Credit ratios, liquidity headroom or shortfall, and debt-service coverage under each scenario |
| **Risk cards** | The risk signals for the latest year. Signals that fired come first, each with a quote from the filing or "Not explained in filing" |
| **Download** | The company's Excel workbook with live formulas, and the result of its recalculation check |

The Company page always uses the default as-of date (24 Nov 2026); it does not accept another date.

## 8. Results page: how well it works

The Results page shows every evaluation (E1 to E8) with its baseline, its 95% interval and the decision taken. Headline cards come first, then the full table, the interval charts, the latency results and the limitations. Each measure has a one-line plain explanation. The same figures, with their run ids, are in `docs/final_results.md`.

## 9. Troubleshooting

| Problem | What to do |
|---|---|
| `streamlit: command not found` | Activate the environment first: `source .venv/bin/activate` |
| The command fails after pasting | Make sure `streamlit run finagent/ui/app.py` is on one line, with no line break |
| The app says a model or run file is missing | The large folders are missing or incomplete (section 3.2); run `python -m finagent.ui.verify` to see which |
| The first answer about a company takes 15–35 s | The pre-built snapshot is not being used. Run the snapshot check (section 3.3). This happens after any change to the code under `finagent/` (other than `finagent/ui/`), the configs or the models, until the serving run is rebuilt |
| Port 8501 is busy | Stop the other copy with Ctrl+C, or open the port the new one prints |
| Segmentation fault on start (macOS) | See the OpenMP note in section 3.3 |
| A question keeps getting a clarification | Use one of the example questions or the suggested rephrasing, and ask about one year and one company at a time |

## 10. Good to know

- **Offline and reproducible.** The filings are pinned copies with sha256 fingerprints; the app never contacts the SEC or any AI service.
- **Point in time.** Every answer uses only filings published by the as-of date.
- **Gaps are honest.** A missing or conflicting figure is shown as not available, with its reason, never as zero or a guess.
- **Docker.** A `Dockerfile` is included but has not been built or tested. In a container the pre-built snapshot does not match, so first answers are slower.
- **Limitations.** The full list, each with a fix path, is in `docs/limitations.md`.

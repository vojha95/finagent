# Outside-user session (20 minutes): pack

Phase 9, BC34 (#319, #336); design: docs/phase9_plan.md section 6, cut to 20 minutes (Vineet's
decision). Local app, `streamlit run finagent/ui/app.py`, at commit ______ (fill in before the
session; nothing is changed in the repo during the session).

## Before the session (Vineet, 5 minutes)

- Start the app; open Ask with **Colgate-Palmolive (CL)** selected, as_of left at the default.
- Close every other window. Have this file open to take notes; give the participant only the
  task sheet below.
- Read the consent line aloud and record the answer:

> "I'm testing a student project that reads company annual reports. I'll note your questions,
> what the app answers, and your comments, under a pseudonym only, not your name. You can stop at
> any time. Is that OK?"

Consent given: yes / no. Pseudonym: **outside-1**. Date and time: ______. Role and finance
experience (one line): ______.

**Rules for the observer:** do not help, explain or point. If the participant is stuck for more
than 1 minute, say "let's move on" and note it as not completed. Nothing is fixed during the
session (problems are logged afterwards in docs/issues_log.md).

## Task sheet (give this part to the participant)

Think aloud as you go: say what you expect, what you see, and what surprises you.

1. **(2 min) A fact.** In *Ask*, ask: what were Colgate-Palmolive's total revenues in its latest
   year? Can you tell where the number comes from?
2. **(3 min) A "why".** Ask why Colgate-Palmolive's operating income changed in the latest year.
   Does the answer explain it? Is it clear what is evidence and what is not?
3. **(3 min) Statements.** Go to *Company*, still Colgate-Palmolive. Pick one number in the
   statements. Can you tell whether it was checked, and how?
4. **(3 min) A stress test.** In *Company*, look at *Credit & liquidity* under the **Severe**
   scenario. In your own words: would the company run short of cash, and by how much?
5. **(3 min) Risks.** Open *Risk cards* (or ask "What are the main risk signals?" for Microsoft).
   Pick one flagged signal. Do you believe it? Is the supporting evidence convincing?
6. **(2 min) The limits.** Ask something the app should not answer, for example a company it
   does not cover, or "Will the share price go up next year?". Is the refusal clear?

## Observer notes (Vineet fills in)

| # | Completed (yes / partly / no) | Time | What they asked or clicked | Correct? (yes / no / unsure) | Useful (1-5) | Comments, quotes, confusion |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |
| 6 | | | | | | |

Export the trace (Ask page, download) after each Ask task and note the file name in the row.

## Five questions at the end (4 minutes; ask aloud, write the answers verbatim)

1. Which task worked best, and why?
2. Which task failed or confused you most?
3. How much would you trust these numbers in your own work? (1 = not at all, 5 = fully) Why?
4. Was it clear what is evidence, what is a model estimate, and what is missing? (1-5) Why?
5. What is missing that you would need to use this for real?

## After the session

- Hand this file (filled in) and the exported traces to Claude; they are written up in
  `docs/outside_user.md` (tasks completed, the trace files, every row above, the five answers,
  the observer notes), and every problem met is logged in docs/issues_log.md.
- Fixes, if any, come afterwards as normal changes, labelled as following the session.

"""Retrieval test helpers: a synthetic 10-K in XHTML and filing records (no evidence read)."""

from datetime import date

from finagent.evidence.store import FilingRecord

DOC = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Form</title><style>p {}</style></head>
<body>
<div>Annual report of the registrant for the fiscal year.</div>
<table>
<tr><td>Item 1.</td><td>Business</td><td>3</td></tr>
<tr><td>Item 1A.</td><td>Risk Factors</td><td>5</td></tr>
<tr><td>Item 7.</td><td>Management's Discussion</td><td>20</td></tr>
<tr><td>Item 8.</td><td>Financial Statements</td><td>30</td></tr>
</table>
<div>Item 1. Business</div>
<p>We sell widgets. We sell many widgets to U.S. customers, e.g. retailers.</p>
<div>Item 1</div>
<div>12</div>
<table><tr><td>Item 1A.</td><td>Risk Factors</td></tr></table>
<p>Demand for widgets may fall.</p>
<div>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS</div>
<p>Revenue increased 10% compared with the prior year primarily due to higher unit sales.</p>
<table><tr><td>Revenue</td><td>$</td><td>1,234</td></tr><tr><td>Cost</td><td>(567)</td></tr></table>
<table><tr><td>&#8226;</td><td>Liquidity remains sufficient for our cash requirements.</td></tr></table>
<div>Lead text <span>with inline words</span><div>A nested block.</div> tail text</div>
<div>Item 8. Financial Statements and Supplementary Data</div>
<p>Consolidated statements of operations.</p>
<div>Note 1 &#8212; Accounting Policies</div>
<p>Our policies are described here.</p>
<div>Note 3 &#8212; Out of Sequence</div>
<div>Note 2 &#8212; Debt</div>
<p>Borrowings increased as we issued senior notes due 2030.</p>
<div>Item 9. Changes in and Disagreements with Accountants</div>
<p>None.</p>
</body></html>
"""

TEXT_TOC = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<div>Item 1. Business</div><div>3</div>
<div>Item 1A. Risk Factors</div><div>5</div>
<div>Item 7. Management's Discussion</div><div>20</div>
<div>Item 8. Financial Statements</div><div>30</div>
<div>Item 1. Business</div><p>We sell widgets across many markets to many customers today.</p>
<div>Item 7. Management's Discussion</div><p>Revenue rose because more widgets were sold.</p>
</body></html>
"""

# Items 7 and 8 refer to a financial section placed after Item 14 (#171); bare "NOTE n" headings
# with their title in the next block, a numbered title with "Co." (#172); zero-width fillers; a
# list entry with a page reference that is no heading (it ends with a full stop).
POINTERS = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<div>Item 1. Business</div><p>We sell widgets in many markets to many customers in many places.</p>
<div>Item 7. Management's Discussion and Analysis</div>
<p>The discussion is presented in the Financial Section.</p>
<div>Item 8. Financial Statements and Supplementary Data</div>
<p>See the Financial Section.</p>
<div>Item 9A. Controls and Procedures</div>
<div>Report of Independent Registered Public Accounting Firm</div>
<p>We audited internal control over financial reporting and found it effective in all respects.</p>
<div>Item 14. Principal Accountant Fees and Services</div>
<p>Incorporated by reference to the proxy statement.</p>
<div>&#8203;</div>
<div>Consolidated Balance Sheet (page 44).</div>
<div>MANAGEMENT&#8217;S DISCUSSION AND ANALYSIS</div>
<p>Revenue increased because more widgets were sold to retailers in the year.</p>
<div>REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM</div>
<p>We have audited the consolidated financial statements of the company.</p>
<div>CONSOLIDATED B ALANCE SHEETS</div>
<div>NOTES TO CONSOLIDATED FINANCIAL STATEMENTS</div>
<div>NOTE 1</div><div>SUMMARY OF ACCOUNTING POLICIES</div>
<p>Policies are described here.</p>
<div>2. Spin-Off of Widget &amp; Co.</div>
<p>The spin-off completed in the year.</p>
<div>Item 15. Exhibits</div><p>Exhibits are listed in the index.</p>
</body></html>
"""

NO_HEADINGS = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Just some text. And more.</p></body></html>
"""


def filing(year: int = 2025, filed: date = date(2026, 2, 1),
           accession: str = "0000000001-26-000001") -> FilingRecord:
    return FilingRecord("SYNT", "0000000001", "10-K", year, date(year, 1, 1), "CALCULATION",
                        date(year, 12, 31), filed, accession, f"synt-{year}.htm",
                        f"https://example/synt-{year}.htm")

# A FY2025 10-K for quotes (7.3): Item 7 sentences that pass or break each quote rule, a note
# in Item 8, and a sentence in Item 1 (not a quotable section).
EVIDENCE = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<div>Item 1. Business</div>
<p>Our customers pay accounts receivable within thirty days of the invoice date.</p>
<div>Item 7. Management's Discussion and Analysis</div>
<p>Accounts receivable increased 33% in 2025, primarily due to longer customer payment terms and the timing of billings.</p>
<p>Accounts receivable increased in 2024 compared with 2023 due to the timing of collections from customers.</p>
<p>Revenue increased 11% in 2025 compared with the prior year primarily due to higher unit sales.</p>
<p>Capital expenditures increased to expand capacity at our plants and distribution centers.</p>
<p>Accounts receivable and customer payment terms are affected by """ + " ".join(
    ["many"] * 60) + """ factors.</p>
<p>Receivable</p>
<div>Item 8. Financial Statements and Supplementary Data</div>
<div>Note 1 &#8212; Receivables</div>
<p>Receivables are stated net of allowances for expected credit losses on customer accounts.</p>
<div>Item 9. Changes in and Disagreements with Accountants</div>
<p>None.</p>
</body></html>
"""

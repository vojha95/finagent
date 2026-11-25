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

NO_HEADINGS = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Just some text. And more.</p></body></html>
"""


def filing(year: int = 2025, filed: date = date(2026, 2, 1),
           accession: str = "0000000001-26-000001") -> FilingRecord:
    return FilingRecord("SYNT", "0000000001", "10-K", year, date(year, 1, 1), "CALCULATION",
                        date(year, 12, 31), filed, accession, f"synt-{year}.htm",
                        f"https://example/synt-{year}.htm")

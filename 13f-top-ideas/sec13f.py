#!/usr/bin/env python3
"""
13F Top Ideas -SEC EDGAR 13F Filing Analyzer
Fetches 13F-HR filings directly from SEC EDGAR and generates a PDF report
of a fund's top 20 holdings with quarter-over-quarter analysis.

Usage:
    python sec13f.py viking
    python sec13f.py "pershing square"
    python sec13f.py --cik 1103804
    python sec13f.py viking -o ~/Desktop/viking_report.pdf
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from fpdf import FPDF
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENT = "13FTopIdeas research@13ftopideas.com"
SEC_RATE_LIMIT_SLEEP = 0.15  # SEC allows 10 req/sec

FUND_ALIASES = {
    "viking": "VIKING GLOBAL INVESTORS",
    "pershing": "PERSHING SQUARE CAPITAL MANAGEMENT",
    "third point": "THIRD POINT",
    "appaloosa": "APPALOOSA",
    "baupost": "BAUPOST GROUP",
    "elliott": "ELLIOTT INVESTMENT MANAGEMENT",
    "citadel": "CITADEL ADVISORS",
    "bridgewater": "BRIDGEWATER ASSOCIATES",
    "renaissance": "RENAISSANCE TECHNOLOGIES",
    "tiger": "TIGER GLOBAL MANAGEMENT",
    "millennium": "MILLENNIUM MANAGEMENT",
    "coatue": "COATUE MANAGEMENT",
    "lone pine": "LONE PINE CAPITAL",
    "d1": "D1 CAPITAL PARTNERS",
    "dragoneer": "DRAGONEER INVESTMENT GROUP",
    "greenlight": "GREENLIGHT CAPITAL",
    "icahn": "ICAHN CARL",
    "jana": "JANA PARTNERS",
    "maverick": "MAVERICK CAPITAL",
    "point72": "POINT72 ASSET MANAGEMENT",
    "two sigma": "TWO SIGMA INVESTMENTS",
    "valor": "VALOR EQUITY PARTNERS",
    "whale rock": "WHALE ROCK CAPITAL MANAGEMENT",
    "berkshire": "BERKSHIRE HATHAWAY",
    "soros": "SOROS FUND MANAGEMENT",
}

NS_13F = {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}

# ---------------------------------------------------------------------------
# SEC EDGAR API helpers
# ---------------------------------------------------------------------------

def make_session():
    """Create a requests session with required SEC headers."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    })
    return s

def sec_get(session, url):
    """GET with rate limiting and error handling."""
    time.sleep(SEC_RATE_LIMIT_SLEEP)
    resp = session.get(url)
    if resp.status_code == 403:
        print(f"ERROR: SEC returned 403 Forbidden for {url}")
        print("This usually means the User-Agent header is being rejected.")
        sys.exit(1)
    resp.raise_for_status()
    return resp

# ---------------------------------------------------------------------------
# Stage 1: Resolve fund name -> CIK
# ---------------------------------------------------------------------------

def resolve_fund_name(name):
    """Map common short names to official SEC filer names."""
    key = name.strip().lower()
    return FUND_ALIASES.get(key, name.upper())

def lookup_cik(session, fund_name):
    """Look up a fund's CIK number via the SEC EDGAR full-text search API."""
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": f'"{fund_name}"',
        "forms": "13F-HR",
        "_source": "ciks,display_names",
        "size": 5,
    }
    resp = sec_get(session, f"{url}?{requests.compat.urlencode(params)}")
    data = resp.json()

    hits = data.get("hits", {}).get("hits", [])
    if not hits:
        return None, None

    # Try to find the best match
    for hit in hits:
        src = hit.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        if ciks:
            return ciks[0], names[0] if names else fund_name

    return None, None

def lookup_cik_company_search(session, fund_name):
    """Fallback: use the company tickers JSON to look up CIK."""
    url = "https://www.sec.gov/files/company_tickers.json"
    # This won't work well for funds. Use EDGAR company search instead.
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{requests.utils.quote(fund_name)}%22&forms=13F-HR&_source=ciks,display_names&size=5"
    resp = sec_get(session, url)
    data = resp.json()
    hits = data.get("hits", {}).get("hits", [])
    if hits:
        src = hits[0].get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        if ciks:
            return ciks[0], names[0] if names else fund_name
    return None, None

# ---------------------------------------------------------------------------
# Stage 2: Fetch 13F-HR filings
# ---------------------------------------------------------------------------

def get_filing_list(session, cik, num_quarters=4):
    """Fetch the submissions JSON and return the last N 13F-HR filings."""
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    resp = sec_get(session, url)
    data = resp.json()

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []
    for i, form in enumerate(forms):
        if form in ("13F-HR", "13F-HR/A"):
            filings.append({
                "form": form,
                "accession": accessions[i],
                "filing_date": filing_dates[i],
                "report_date": report_dates[i],
                "primary_doc": primary_docs[i],
            })
            if len(filings) >= num_quarters:
                break

    display_name = data.get("name", "Unknown Fund")
    return filings, display_name, cik

def find_info_table_url(session, cik, accession):
    """Find the information table XML URL from a filing's index page."""
    acc_no_dashes = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{accession}-index.htm"

    resp = sec_get(session, index_url)
    html = resp.text

    # Parse the index table -look for rows with "INFORMATION TABLE" type
    rows = re.findall(r'<tr>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
    for row in rows:
        if 'INFORMATION TABLE' in row.upper():
            links = re.findall(r'<a\s+href="([^"]+)"', row, re.IGNORECASE)
            for link in links:
                # Extract the raw XML filename, stripping any xslForm13F prefix
                # Links look like: /Archives/edgar/data/CIK/ACC/xslForm13F_X02/FILE.xml
                xml_match = re.search(r'/xslForm13F[^/]*/([^"]+\.xml)', link)
                if xml_match:
                    filename = xml_match.group(1)
                    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{filename}"
                # Direct XML link without xsl prefix
                if link.endswith('.xml'):
                    filename = link.split('/')[-1]
                    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{filename}"

    # Fallback: look for any XML link that's not primary_doc
    all_xml = re.findall(r'<a\s+href="([^"]+\.xml)"', html, re.IGNORECASE)
    for link in all_xml:
        fname = link.split('/')[-1].lower()
        if 'primary' in fname:
            continue
        xml_match = re.search(r'/xslForm13F[^/]*/([^"]+\.xml)', link)
        if xml_match:
            filename = xml_match.group(1)
            return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{filename}"
        filename = link.split('/')[-1]
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{filename}"

    return None

# ---------------------------------------------------------------------------
# Stage 3: Parse 13F XML info tables
# ---------------------------------------------------------------------------

def parse_info_table(xml_text):
    """Parse a 13F information table XML and return holdings dict keyed by CUSIP."""
    holdings = {}

    root = ET.fromstring(xml_text)

    # Try multiple namespace patterns since SEC filings vary
    namespaces = [
        {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"},
        {"ns": "http://www.sec.gov/edgar/thirteenf/informationtable"},
    ]

    info_tables = []
    for ns in namespaces:
        info_tables = root.findall(".//ns:infoTable", ns)
        if info_tables:
            active_ns = ns
            break

    if not info_tables:
        # Try without namespace
        info_tables = root.findall(".//infoTable")
        active_ns = {}

    if not info_tables:
        # Try finding any element that looks like an info table entry
        for elem in root.iter():
            if 'infotable' in elem.tag.lower():
                info_tables = [elem]
                break

    prefix = "ns:" if active_ns else ""

    for entry in (root.findall(f".//{prefix}infoTable", active_ns) if active_ns else root.findall(".//infoTable")):
        try:
            name = _find_text(entry, "nameOfIssuer", active_ns).strip()
            title = _find_text(entry, "titleOfClass", active_ns).strip()
            cusip = _find_text(entry, "cusip", active_ns).strip()
            value = int(_find_text(entry, "value", active_ns))  # in whole dollars
            shares_el = entry.find(f"{_ns_prefix(active_ns)}shrsOrPrnAmt", active_ns) if active_ns else entry.find("shrsOrPrnAmt")
            shares = 0
            share_type = "SH"
            if shares_el is not None:
                sh = shares_el.find(f"{_ns_prefix(active_ns)}sshPrnamt", active_ns) if active_ns else shares_el.find("sshPrnamt")
                st = shares_el.find(f"{_ns_prefix(active_ns)}sshPrnamtType", active_ns) if active_ns else shares_el.find("sshPrnamtType")
                if sh is not None and sh.text:
                    shares = int(sh.text)
                if st is not None and st.text:
                    share_type = st.text.strip()

            discretion = _find_text(entry, "investmentDiscretion", active_ns)

            # Aggregate multiple entries for the same CUSIP
            if cusip in holdings:
                holdings[cusip]["value"] += value
                holdings[cusip]["shares"] += shares
            else:
                holdings[cusip] = {
                    "name": name,
                    "title": title,
                    "cusip": cusip,
                    "value": value,  # in whole dollars
                    "shares": shares,
                    "share_type": share_type,
                    "discretion": discretion,
                }
        except Exception as e:
            continue  # Skip malformed entries

    return holdings

def _ns_prefix(ns_dict):
    if ns_dict:
        return "ns:"
    return ""

def _find_text(element, tag, ns_dict):
    prefix = _ns_prefix(ns_dict)
    el = element.find(f"{prefix}{tag}", ns_dict) if ns_dict else element.find(tag)
    if el is not None and el.text:
        return el.text
    return ""

# ---------------------------------------------------------------------------
# Stage 4: Compute rankings and deltas
# ---------------------------------------------------------------------------

def compute_top_holdings(quarters_data, num_top=20):
    """Rank positions and compute quarter-over-quarter deltas."""
    if not quarters_data or not quarters_data[0]:
        print("ERROR: No holdings data found in the most recent filing.")
        sys.exit(1)

    current = quarters_data[0]

    # Sort by value descending
    sorted_cusips = sorted(current.keys(), key=lambda c: current[c]["value"], reverse=True)
    top_cusips = sorted_cusips[:num_top]

    total_value = sum(h["value"] for h in current.values())

    results = []
    for rank, cusip in enumerate(top_cusips, 1):
        pos = current[cusip]
        value_millions = pos["value"] / 1_000_000  # value is in whole dollars, convert to $M
        pct_portfolio = (pos["value"] / total_value * 100) if total_value > 0 else 0

        # Quarter-over-quarter deltas
        deltas = []
        for q_idx in range(1, len(quarters_data)):
            prev = quarters_data[q_idx]
            if prev is None:
                deltas.append("--")
            elif cusip not in prev:
                deltas.append("NEW")
            elif prev[cusip]["shares"] == 0:
                deltas.append("NEW")
            else:
                prev_shares = prev[cusip]["shares"]
                # Compare current quarter of that delta pair
                if q_idx == 1:
                    curr_shares = pos["shares"]
                else:
                    # For Q-2 delta, compare Q-1 vs Q-2
                    prev_q = quarters_data[q_idx - 1]
                    curr_shares = prev_q.get(cusip, {}).get("shares", 0) if prev_q else 0

                if curr_shares == 0 and prev_shares == 0:
                    deltas.append("0.0%")
                elif prev_shares == 0:
                    deltas.append("NEW")
                else:
                    change_pct = (curr_shares - prev_shares) / prev_shares * 100
                    deltas.append(f"{change_pct:+.1f}%")

        # Pad deltas if fewer than 3 quarters of history
        while len(deltas) < 3:
            deltas.append("--")

        results.append({
            "rank": rank,
            "name": pos["name"],
            "title": pos["title"],
            "cusip": cusip,
            "value_m": value_millions,
            "shares": pos["shares"],
            "pct_portfolio": pct_portfolio,
            "delta_q1": deltas[0],
            "delta_q2": deltas[1],
            "delta_q3": deltas[2],
            "ticker": "",  # filled by enrichment
            "pct_shares_outstanding": "N/A",
            "pct_3m_adv": "N/A",
        })

    return results, total_value

# ---------------------------------------------------------------------------
# Stage 5a: CUSIP -> Ticker enrichment via OpenFIGI
# ---------------------------------------------------------------------------

def enrich_tickers_openfigi(holdings):
    """Batch look up tickers from CUSIPs using the OpenFIGI API (batches of 10)."""
    batch_size = 10
    for start in range(0, len(holdings), batch_size):
        batch = holdings[start:start + batch_size]
        mapping_request = [{"idType": "ID_CUSIP", "idValue": h["cusip"]} for h in batch]

        try:
            resp = requests.post(
                "https://api.openfigi.com/v3/mapping",
                json=mapping_request,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                results = resp.json()
                for i, result in enumerate(results):
                    if "data" in result and result["data"]:
                        for item in result["data"]:
                            if item.get("marketSector") == "Equity" or item.get("exchCode") in ("US", "UN", "UW", "UA", "UR"):
                                holdings[start + i]["ticker"] = item.get("ticker", "")
                                break
                        else:
                            holdings[start + i]["ticker"] = result["data"][0].get("ticker", "")
            else:
                print(f"  OpenFIGI batch returned status {resp.status_code}")
            time.sleep(1)  # Rate limit for anonymous tier
        except Exception as e:
            print(f"  OpenFIGI lookup failed: {e}")

    return holdings

def enrich_market_data(holdings):
    """Enrich holdings with shares outstanding and 3-month ADV from yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance not installed, skipping market data enrichment.")
        return holdings

    for h in holdings:
        ticker = h.get("ticker", "")
        if not ticker:
            continue

        try:
            t = yf.Ticker(ticker)
            info = t.info

            shares_out = info.get("sharesOutstanding")
            if shares_out and shares_out > 0:
                pct_so = h["shares"] / shares_out * 100
                h["pct_shares_outstanding"] = f"{pct_so:.1f}%"

            # 3-month average daily volume
            hist = t.history(period="3mo")
            if not hist.empty and "Volume" in hist.columns:
                avg_daily_vol = hist["Volume"].mean()
                if avg_daily_vol > 0:
                    days_of_volume = h["shares"] / avg_daily_vol
                    h["pct_3m_adv"] = f"{days_of_volume:.1f}d"

        except Exception:
            continue  # Keep N/A defaults

    return holdings

# ---------------------------------------------------------------------------
# Stage 5b: PDF Report Generation
# ---------------------------------------------------------------------------

class ReportPDF(FPDF):
    def __init__(self, fund_name, report_date, filing_date, total_value, num_positions):
        super().__init__(orientation="L", unit="mm", format="Letter")
        self.fund_name = fund_name
        self.report_date = report_date
        self.filing_date = filing_date
        self.total_value_b = total_value / 1_000_000_000  # whole dollars -> $B
        self.num_positions = num_positions
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 6, f"{self.fund_name} -13F Top 20 Holdings Analysis", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 7)
        header_info = (
            f"Report Date: {self.report_date}  |  Filing Date: {self.filing_date}  |  "
            f"Total 13F Value: ${self.total_value_b:.1f}B  |  Positions: {self.num_positions}"
        )
        self.cell(0, 4, header_info, new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(2)

    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", "I", 6)
        self.cell(0, 4, f"Page {self.page_no()} | 13F Top Ideas | Data from SEC EDGAR | Not investment advice", align="C")

def generate_pdf(holdings, fund_name, report_date, filing_date, total_value, num_positions, quarters_data, output_path):
    """Generate the full PDF report."""
    pdf = ReportPDF(fund_name, report_date, filing_date, total_value, num_positions)
    pdf.add_page()

    # Column definitions: (header, width, align)
    columns = [
        ("#", 8, "C"),
        ("Ticker", 18, "C"),
        ("Company", 52, "L"),
        ("Position ($M)", 25, "R"),
        ("Shares", 25, "R"),
        ("% Port", 16, "R"),
        ("% S/O", 16, "R"),
        ("3M ADV", 16, "R"),
        ("Q-1 Chg", 22, "R"),
        ("Q-2 Chg", 22, "R"),
        ("Q-3 Chg", 22, "R"),
    ]

    total_width = sum(c[1] for c in columns)
    start_x = (pdf.w - total_width) / 2

    # Table header
    pdf.set_x(start_x)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255, 255, 255)
    for header, width, align in columns:
        pdf.cell(width, 6, header, border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)

    # Table rows
    for i, h in enumerate(holdings):
        pdf.set_x(start_x)
        if i % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
        else:
            pdf.set_fill_color(255, 255, 255)

        pdf.set_font("Helvetica", "", 6.5)

        ticker = h["ticker"] if h["ticker"] else h["cusip"][:6]
        company = h["name"][:28]
        value_str = f"${h['value_m']:,.1f}"
        shares_str = format_shares(h["shares"])
        pct_port = f"{h['pct_portfolio']:.1f}%"

        row_data = [
            (str(h["rank"]), columns[0][1], "C"),
            (ticker, columns[1][1], "C"),
            (company, columns[2][1], "L"),
            (value_str, columns[3][1], "R"),
            (shares_str, columns[4][1], "R"),
            (pct_port, columns[5][1], "R"),
            (h["pct_shares_outstanding"], columns[6][1], "R"),
            (h["pct_3m_adv"], columns[7][1], "R"),
        ]

        for text, width, align in row_data:
            pdf.cell(width, 5.5, text, border=1, align=align, fill=True)

        # Delta columns with color
        for delta_key, col_idx in [("delta_q1", 8), ("delta_q2", 9), ("delta_q3", 10)]:
            delta = h[delta_key]
            _set_delta_color(pdf, delta)
            pdf.cell(columns[col_idx][1], 5.5, delta, border=1, align="R", fill=True)
            pdf.set_text_color(0, 0, 0)

        pdf.ln()

    # Footnotes
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 6)
    pdf.set_x(start_x)
    pdf.cell(0, 4, "Notes: 13F filings have a 45-day delay. Only long equity positions shown. Market values as of quarter-end.")
    pdf.ln()
    pdf.set_x(start_x)
    pdf.cell(0, 4, "% S/O = % of Shares Outstanding | 3M ADV = Position size in days of avg daily volume | Chg = Quarter-over-quarter share change")

    # Pages 2+: Investment thesis for each position
    for i in range(0, len(holdings), 2):
        pdf.add_page(orientation="P")
        pdf.set_font("Helvetica", "", 8)

        for j in range(2):
            if i + j >= len(holdings):
                break
            h = holdings[i + j]
            _write_thesis_section(pdf, h, quarters_data)
            pdf.ln(5)

    pdf.output(str(output_path))
    return output_path

def _set_delta_color(pdf, delta):
    if delta == "NEW":
        pdf.set_text_color(41, 128, 185)  # Blue
        pdf.set_font("Helvetica", "B", 6.5)
    elif delta.startswith("+"):
        pdf.set_text_color(39, 174, 96)  # Green
        pdf.set_font("Helvetica", "", 6.5)
    elif delta.startswith("-"):
        pdf.set_text_color(192, 57, 43)  # Red
        pdf.set_font("Helvetica", "", 6.5)
    else:
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 6.5)

def _write_thesis_section(pdf, holding, quarters_data):
    """Write a thesis analysis section for a single holding."""
    ticker = holding["ticker"] if holding["ticker"] else "N/A"
    name = holding["name"]

    # Header
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 8, f"  {holding['rank']}. {name} ({ticker})", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_text_color(0, 0, 0)

    # Position summary line
    pdf.set_font("Helvetica", "B", 8)
    status = holding["delta_q1"]
    if status == "NEW":
        status_text = "NEW POSITION"
    elif status.startswith("+"):
        status_text = f"Increased {status}"
    elif status.startswith("-"):
        status_text = f"Decreased {status}"
    else:
        status_text = "Unchanged"

    pdf.cell(0, 6, f"Position: ${holding['value_m']:,.1f}M  |  {holding['pct_portfolio']:.1f}% of Portfolio  |  {status_text}", new_x="LMARGIN", new_y="NEXT")

    # Generate data-driven thesis
    pdf.set_font("Helvetica", "", 8)

    thesis_parts = []

    # Position sizing context
    if holding["pct_portfolio"] >= 5:
        thesis_parts.append(f"This is a high-conviction position at {holding['pct_portfolio']:.1f}% of the portfolio, ranking #{holding['rank']} by market value.")
    elif holding["pct_portfolio"] >= 2:
        thesis_parts.append(f"A meaningful position at {holding['pct_portfolio']:.1f}% of the portfolio, ranking #{holding['rank']} by market value.")
    else:
        thesis_parts.append(f"A smaller position at {holding['pct_portfolio']:.1f}% of the portfolio, ranking #{holding['rank']} by market value.")

    # Trend analysis
    if holding["delta_q1"] == "NEW":
        thesis_parts.append("This is a brand new position initiated this quarter, suggesting the fund has identified a new opportunity or catalyst.")
    elif holding["delta_q1"].startswith("+"):
        pct = holding["delta_q1"]
        thesis_parts.append(f"The fund increased its position by {pct} this quarter, signaling continued conviction.")
        if holding["delta_q2"].startswith("+"):
            thesis_parts.append(f"This follows a {holding['delta_q2']} increase the prior quarter, indicating a sustained building pattern.")
    elif holding["delta_q1"].startswith("-"):
        pct = holding["delta_q1"]
        thesis_parts.append(f"The fund trimmed its position by {pct} this quarter.")
        if holding["delta_q2"].startswith("-"):
            thesis_parts.append(f"This follows a {holding['delta_q2']} decrease the prior quarter, suggesting a gradual exit or profit-taking.")
    else:
        thesis_parts.append("The position was held steady this quarter with no change in share count.")

    # Ownership context
    if holding["pct_shares_outstanding"] != "N/A":
        thesis_parts.append(f"The fund holds approximately {holding['pct_shares_outstanding']} of the company's total shares outstanding.")

    # Liquidity context
    if holding["pct_3m_adv"] != "N/A":
        thesis_parts.append(f"Based on 3-month average daily volume, the position represents approximately {holding['pct_3m_adv']} of trading volume, providing context on potential liquidity constraints.")

    # Share count
    thesis_parts.append(f"The fund currently holds {holding['shares']:,} shares valued at ${holding['value_m']:,.1f}M as of the filing date.")

    thesis_text = " ".join(thesis_parts)
    pdf.multi_cell(0, 4.5, thesis_text)

def format_shares(shares):
    """Format share counts for display."""
    if shares >= 1_000_000:
        return f"{shares / 1_000_000:.1f}M"
    elif shares >= 1_000:
        return f"{shares / 1_000:.0f}K"
    return str(shares)

# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="13F Top Ideas -Analyze SEC 13F filings and generate PDF reports"
    )
    parser.add_argument("fund_name", nargs="?", help="Fund name (e.g., 'viking', 'pershing square')")
    parser.add_argument("--cik", type=str, help="Direct CIK number (skip name lookup)")
    parser.add_argument("-o", "--output", type=str, help="Output PDF path (default: ~/Desktop/)")
    parser.add_argument("-q", "--quarters", type=int, default=4, help="Number of quarters to analyze (default: 4)")
    args = parser.parse_args()

    if not args.fund_name and not args.cik:
        parser.print_help()
        sys.exit(1)

    session = make_session()

    # Stage 1: Resolve fund name to CIK
    if args.cik:
        cik = args.cik
        fund_display_name = args.fund_name or f"CIK {cik}"
        print(f"Using CIK: {cik}")
    else:
        resolved_name = resolve_fund_name(args.fund_name)
        print(f"Resolving '{args.fund_name}' → '{resolved_name}'...")
        cik, fund_display_name = lookup_cik(session, resolved_name)
        if not cik:
            print(f"ERROR: Could not find CIK for '{resolved_name}'. Try using --cik directly.")
            sys.exit(1)
        print(f"Found: {fund_display_name} (CIK: {cik})")

    # Stage 2: Fetch filings
    print(f"\nFetching filing list...")
    filings, display_name, cik = get_filing_list(session, cik, args.quarters)
    if not filings:
        print("ERROR: No 13F-HR filings found.")
        sys.exit(1)

    fund_display_name = display_name or fund_display_name
    print(f"Found {len(filings)} 13F-HR filings:")
    for f in filings:
        print(f"  {f['form']} -Filed: {f['filing_date']}  Report Date: {f['report_date']}")

    # Stage 3: Parse each filing
    quarters_data = []
    for i, filing in enumerate(filings):
        q_label = f"Q{i}" if i == 0 else f"Q-{i}"
        print(f"\nParsing {q_label} ({filing['report_date']})...")

        info_url = find_info_table_url(session, cik, filing["accession"])
        if not info_url:
            print(f"  WARNING: Could not find information table for {filing['accession']}")
            quarters_data.append(None)
            continue

        resp = sec_get(session, info_url)
        holdings = parse_info_table(resp.text)
        print(f"  Found {len(holdings)} positions")
        quarters_data.append(holdings)

    # Stage 4: Compute top holdings and deltas
    print("\nComputing top 20 holdings and deltas...")
    top_holdings, total_value = compute_top_holdings(quarters_data)
    num_positions = len(quarters_data[0]) if quarters_data[0] else 0

    # Stage 5a: Enrich with tickers and market data
    print("Looking up tickers via OpenFIGI...")
    top_holdings = enrich_tickers_openfigi(top_holdings)

    found_tickers = sum(1 for h in top_holdings if h["ticker"])
    print(f"  Resolved {found_tickers}/{len(top_holdings)} tickers")

    print("Fetching market data (shares outstanding, volume)...")
    top_holdings = enrich_market_data(top_holdings)

    # Stage 5b: Generate PDF
    if args.output:
        output_path = Path(args.output)
    else:
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', fund_display_name).strip('_')
        output_path = Path.home() / "Desktop" / f"{safe_name}_13F_Report_{datetime.now().strftime('%Y%m%d')}.pdf"

    print(f"\nGenerating PDF report...")
    report_date = filings[0]["report_date"]
    filing_date = filings[0]["filing_date"]

    generate_pdf(
        top_holdings,
        fund_display_name,
        report_date,
        filing_date,
        total_value,
        num_positions,
        quarters_data,
        output_path,
    )

    print(f"\n✅ Report saved to: {output_path}")

    # Print quick summary
    print(f"\n--- {fund_display_name} Top 5 ---")
    for h in top_holdings[:5]:
        ticker = h["ticker"] if h["ticker"] else h["cusip"]
        print(f"  {h['rank']}. {ticker:8s} ${h['value_m']:>10,.1f}M  {h['pct_portfolio']:.1f}%  {h['delta_q1']}")


if __name__ == "__main__":
    main()

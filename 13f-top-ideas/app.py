#!/usr/bin/env python3
"""
13F Top Ideas - Interactive SEC 13F Filing Analyzer
An interactive terminal app that fetches 13F data from SEC EDGAR,
displays everything in the terminal, uses a local LLM (Ollama) for
investment thesis generation, and produces PDF reports.

Usage:
    python app.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from textwrap import wrap

import requests
from fpdf import FPDF
import xml.etree.ElementTree as ET
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm

# ---------------------------------------------------------------------------
# Terminal colors & formatting
# ---------------------------------------------------------------------------

class C:
    """ANSI color codes for terminal output."""
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BG_DARK = "\033[48;5;235m"
    BG_HEADER = "\033[48;5;24m"

def banner():
    print(f"""
{C.BOLD}{C.CYAN}{'='*65}
   13F TOP IDEAS - SEC EDGAR Filing Analyzer
   Powered by Ollama (Local LLM) + SEC EDGAR API
{'='*65}{C.RESET}
{C.DIM}  Data sourced directly from sec.gov | Not investment advice
  Type 'help' for commands | 'quit' to exit{C.RESET}
""")

def print_header(text):
    print(f"\n{C.BOLD}{C.CYAN}>>> {text}{C.RESET}")

def print_success(text):
    print(f"{C.GREEN}  [OK] {text}{C.RESET}")

def print_warn(text):
    print(f"{C.YELLOW}  [!] {text}{C.RESET}")

def print_error(text):
    print(f"{C.RED}  [ERROR] {text}{C.RESET}")

def print_info(text):
    print(f"{C.DIM}  {text}{C.RESET}")

def print_step(step, text):
    print(f"\n{C.BOLD}{C.WHITE}  [{step}] {text}{C.RESET}")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENT = "13FTopIdeas research@13ftopideas.com"
SEC_RATE_LIMIT_SLEEP = 0.15
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.1:8b"

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
    "whale rock": "WHALE ROCK CAPITAL MANAGEMENT",
    "berkshire": "BERKSHIRE HATHAWAY",
    "soros": "SOROS FUND MANAGEMENT",
}

# ---------------------------------------------------------------------------
# Ollama LLM
# ---------------------------------------------------------------------------

def check_ollama():
    """Check if Ollama is running and the model is available."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            if any(OLLAMA_MODEL in m for m in models):
                return True
            print_warn(f"Model '{OLLAMA_MODEL}' not found. Available: {', '.join(models)}")
            if models:
                return True  # Use whatever is available
            return False
        return False
    except Exception:
        return False

def get_available_model():
    """Get the first available Ollama model."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            if models:
                for m in models:
                    if OLLAMA_MODEL in m["name"]:
                        return m["name"]
                return models[0]["name"]
    except Exception:
        pass
    return OLLAMA_MODEL

def llm_generate(prompt, system_prompt=None, stream=True):
    """Generate text using Ollama. Streams to terminal if stream=True."""
    model = get_available_model()
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": {"temperature": 0.7, "num_predict": 500},
    }
    if system_prompt:
        payload["system"] = system_prompt

    try:
        if stream:
            full_text = ""
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json=payload,
                stream=True,
                timeout=120,
            )
            for line in resp.iter_lines():
                if line:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    print(token, end="", flush=True)
                    full_text += token
                    if chunk.get("done", False):
                        break
            print()  # newline after streaming
            return full_text
        else:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json=payload,
                timeout=120,
            )
            return resp.json().get("response", "")
    except Exception as e:
        print_error(f"LLM generation failed: {e}")
        return ""

def llm_generate_thesis(holding, fund_name):
    """Generate a 150-200 word investment thesis for a holding."""
    system_prompt = (
        "You are a senior equity research analyst writing concise investment thesis notes. "
        "Write as a thoughtful analyst would - not promotional. "
        "Acknowledge uncertainty with words like 'likely,' 'appears to be,' 'may reflect.' "
        "Be specific about the data provided. Keep it 150-200 words."
    )

    ticker = holding["ticker"] if holding["ticker"] else holding["name"]
    status = holding["delta_q1"]
    if status == "NEW":
        status_text = "NEW POSITION this quarter"
    elif status.startswith("+"):
        status_text = f"INCREASED {status} this quarter"
    elif status.startswith("-"):
        status_text = f"DECREASED {status} this quarter"
    else:
        status_text = "UNCHANGED this quarter"

    prompt = f"""Write an investment thesis for why {fund_name} holds {holding['name']} ({ticker}).

Key data:
- Position size: ${holding['value_m']:,.1f}M ({holding['pct_portfolio']:.1f}% of portfolio)
- Rank: #{holding['rank']} in portfolio
- Shares held: {holding['shares']:,}
- Status: {status_text}
- Q-1 change: {holding['delta_q1']}, Q-2 change: {holding['delta_q2']}, Q-3 change: {holding['delta_q3']}
- % of shares outstanding: {holding['pct_shares_outstanding']}
- Liquidity (days of ADV): {holding['pct_3m_adv']}

Cover: (1) Why this position makes sense given the fund's style, (2) sector/company dynamics, (3) position sizing context (conviction level, building/trimming/maintaining). 150-200 words."""

    return llm_generate(prompt, system_prompt, stream=False)

# ---------------------------------------------------------------------------
# SEC EDGAR API
# ---------------------------------------------------------------------------

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    })
    return s

def sec_get(session, url):
    time.sleep(SEC_RATE_LIMIT_SLEEP)
    resp = session.get(url)
    if resp.status_code == 403:
        print_error(f"SEC returned 403 for {url}")
        return None
    resp.raise_for_status()
    return resp

def resolve_fund_name(name):
    key = name.strip().lower()
    return FUND_ALIASES.get(key, name.upper())

def lookup_cik(session, fund_name):
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": f'"{fund_name}"',
        "forms": "13F-HR",
        "_source": "ciks,display_names",
        "size": 5,
    }
    resp = sec_get(session, f"{url}?{requests.compat.urlencode(params)}")
    if not resp:
        return None, None
    data = resp.json()
    hits = data.get("hits", {}).get("hits", [])
    if not hits:
        return None, None
    for hit in hits:
        src = hit.get("_source", {})
        ciks = src.get("ciks", [])
        names = src.get("display_names", [])
        if ciks:
            return ciks[0], names[0] if names else fund_name
    return None, None

def get_filing_list(session, cik, num_quarters=4):
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    resp = sec_get(session, url)
    if not resp:
        return [], None, cik
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
    acc_no_dashes = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{accession}-index.htm"

    resp = sec_get(session, index_url)
    if not resp:
        return None
    html = resp.text

    rows = re.findall(r'<tr>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
    for row in rows:
        if 'INFORMATION TABLE' in row.upper():
            links = re.findall(r'<a\s+href="([^"]+)"', row, re.IGNORECASE)
            for link in links:
                xml_match = re.search(r'/xslForm13F[^/]*/([^"]+\.xml)', link)
                if xml_match:
                    filename = xml_match.group(1)
                    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{filename}"
                if link.endswith('.xml'):
                    filename = link.split('/')[-1]
                    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{filename}"

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
# XML Parsing
# ---------------------------------------------------------------------------

def parse_info_table(xml_text):
    holdings = {}
    root = ET.fromstring(xml_text)

    namespaces = [
        {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"},
        {"ns": "http://www.sec.gov/edgar/thirteenf/informationtable"},
    ]

    active_ns = {}
    for ns in namespaces:
        info_tables = root.findall(".//ns:infoTable", ns)
        if info_tables:
            active_ns = ns
            break

    if not active_ns:
        info_tables = root.findall(".//infoTable")

    prefix = "ns:" if active_ns else ""

    for entry in (root.findall(f".//{prefix}infoTable", active_ns) if active_ns else root.findall(".//infoTable")):
        try:
            name = _find_text(entry, "nameOfIssuer", active_ns).strip()
            title = _find_text(entry, "titleOfClass", active_ns).strip()
            cusip = _find_text(entry, "cusip", active_ns).strip()
            value = int(_find_text(entry, "value", active_ns))

            shares_el = entry.find(f"{prefix}shrsOrPrnAmt", active_ns) if active_ns else entry.find("shrsOrPrnAmt")
            shares = 0
            share_type = "SH"
            if shares_el is not None:
                sh = shares_el.find(f"{prefix}sshPrnamt", active_ns) if active_ns else shares_el.find("sshPrnamt")
                st = shares_el.find(f"{prefix}sshPrnamtType", active_ns) if active_ns else shares_el.find("sshPrnamtType")
                if sh is not None and sh.text:
                    shares = int(sh.text)
                if st is not None and st.text:
                    share_type = st.text.strip()

            discretion = _find_text(entry, "investmentDiscretion", active_ns)

            if cusip in holdings:
                holdings[cusip]["value"] += value
                holdings[cusip]["shares"] += shares
            else:
                holdings[cusip] = {
                    "name": name, "title": title, "cusip": cusip,
                    "value": value, "shares": shares,
                    "share_type": share_type, "discretion": discretion,
                }
        except Exception:
            continue

    return holdings

def _find_text(element, tag, ns_dict):
    prefix = "ns:" if ns_dict else ""
    el = element.find(f"{prefix}{tag}", ns_dict) if ns_dict else element.find(tag)
    if el is not None and el.text:
        return el.text
    return ""

# ---------------------------------------------------------------------------
# Compute rankings & deltas
# ---------------------------------------------------------------------------

def compute_top_holdings(quarters_data, num_top=20):
    if not quarters_data or not quarters_data[0]:
        return [], 0

    current = quarters_data[0]
    sorted_cusips = sorted(current.keys(), key=lambda c: current[c]["value"], reverse=True)
    top_cusips = sorted_cusips[:num_top]
    total_value = sum(h["value"] for h in current.values())

    results = []
    for rank, cusip in enumerate(top_cusips, 1):
        pos = current[cusip]
        value_millions = pos["value"] / 1_000_000
        pct_portfolio = (pos["value"] / total_value * 100) if total_value > 0 else 0

        deltas = []
        for q_idx in range(1, len(quarters_data)):
            prev = quarters_data[q_idx]
            if prev is None:
                deltas.append("--")
            elif cusip not in prev or prev[cusip]["shares"] == 0:
                deltas.append("NEW")
            else:
                prev_shares = prev[cusip]["shares"]
                if q_idx == 1:
                    curr_shares = pos["shares"]
                else:
                    prev_q = quarters_data[q_idx - 1]
                    curr_shares = prev_q.get(cusip, {}).get("shares", 0) if prev_q else 0

                if prev_shares == 0:
                    deltas.append("NEW")
                else:
                    change_pct = (curr_shares - prev_shares) / prev_shares * 100
                    deltas.append(f"{change_pct:+.1f}%")

        while len(deltas) < 3:
            deltas.append("--")

        results.append({
            "rank": rank, "name": pos["name"], "title": pos["title"],
            "cusip": cusip, "value_m": value_millions, "shares": pos["shares"],
            "pct_portfolio": pct_portfolio,
            "delta_q1": deltas[0], "delta_q2": deltas[1], "delta_q3": deltas[2],
            "ticker": "", "pct_shares_outstanding": "N/A", "pct_3m_adv": "N/A",
        })

    return results, total_value

# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def enrich_tickers(holdings):
    """Look up tickers via OpenFIGI."""
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
            time.sleep(1)
        except Exception:
            pass
    return holdings

def enrich_market_data(holdings):
    """Enrich with shares outstanding and ADV from yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print_warn("yfinance not installed, skipping market data enrichment.")
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
                h["pct_shares_outstanding"] = f"{h['shares'] / shares_out * 100:.1f}%"

            hist = t.history(period="3mo")
            if not hist.empty and "Volume" in hist.columns:
                avg_daily_vol = hist["Volume"].mean()
                if avg_daily_vol > 0:
                    h["pct_3m_adv"] = f"{h['shares'] / avg_daily_vol:.1f}d"
        except Exception:
            continue
    return holdings

# ---------------------------------------------------------------------------
# Terminal display
# ---------------------------------------------------------------------------

def format_shares(shares):
    if shares >= 1_000_000:
        return f"{shares / 1_000_000:.1f}M"
    elif shares >= 1_000:
        return f"{shares / 1_000:.0f}K"
    return str(shares)

def colorize_delta(delta):
    if delta == "NEW":
        return f"{C.BOLD}{C.BLUE}NEW{C.RESET}"
    elif delta.startswith("+"):
        return f"{C.GREEN}{delta}{C.RESET}"
    elif delta.startswith("-"):
        return f"{C.RED}{delta}{C.RESET}"
    return delta

def display_holdings_table(holdings, fund_name, total_value, filings):
    """Print a full holdings table to the terminal."""
    total_b = total_value / 1_000_000_000

    print(f"\n{C.BG_HEADER}{C.WHITE}{C.BOLD}{'':^80}{C.RESET}")
    print(f"{C.BG_HEADER}{C.WHITE}{C.BOLD}   {fund_name} - 13F Top {len(holdings)} Holdings{'':<40}{C.RESET}")
    print(f"{C.BG_HEADER}{C.WHITE}   Filing: {filings[0]['filing_date']}  |  Period: {filings[0]['report_date']}  |  Total: ${total_b:.1f}B{'':<20}{C.RESET}")
    print(f"{C.BG_HEADER}{C.WHITE}{C.BOLD}{'':^80}{C.RESET}")

    # Table header
    print(f"\n{C.BOLD}{C.UNDERLINE}{'#':>3}  {'Ticker':<8} {'Company':<26} {'Value ($M)':>12} {'Shares':>10} {'% Port':>7} {'% S/O':>7} {'3M ADV':>7} {'Q-1':>8} {'Q-2':>8} {'Q-3':>8}{C.RESET}")

    for h in holdings:
        ticker = h["ticker"] if h["ticker"] else h["cusip"][:6]
        company = h["name"][:25]
        delta1 = colorize_delta(h["delta_q1"])
        delta2 = colorize_delta(h["delta_q2"])
        delta3 = colorize_delta(h["delta_q3"])

        # Build the base string (without color codes for alignment)
        base = f"{h['rank']:>3}  {ticker:<8} {company:<26} ${h['value_m']:>10,.1f} {format_shares(h['shares']):>10} {h['pct_portfolio']:>6.1f}% {h['pct_shares_outstanding']:>7} {h['pct_3m_adv']:>7}"

        # Alternate row shading
        if h["rank"] % 2 == 0:
            print(f"{C.DIM}{base}{C.RESET} {delta1:>17} {delta2:>17} {delta3:>17}")
        else:
            print(f"{base} {delta1:>17} {delta2:>17} {delta3:>17}")

    print(f"\n{C.DIM}  Notes: 13F filings have a 45-day delay. Only long equity positions shown.{C.RESET}")
    print(f"{C.DIM}  Market values as of quarter-end. This is analysis, not investment advice.{C.RESET}")

def display_all_positions(quarters_data, quarter_idx=0):
    """Display ALL positions from a given quarter."""
    if not quarters_data or quarters_data[quarter_idx] is None:
        print_error("No data for this quarter.")
        return

    data = quarters_data[quarter_idx]
    sorted_holdings = sorted(data.values(), key=lambda h: h["value"], reverse=True)

    print(f"\n{C.BOLD}All {len(sorted_holdings)} positions (Quarter {quarter_idx}):{C.RESET}")
    print(f"{C.UNDERLINE}{'#':>4}  {'Security':<30} {'CUSIP':<12} {'Value ($M)':>12} {'Shares':>12} {'Type':<6} {'Discr':<6}{C.RESET}")

    for i, h in enumerate(sorted_holdings, 1):
        val_m = h["value"] / 1_000_000
        print(f"{i:>4}  {h['name'][:29]:<30} {h['cusip']:<12} ${val_m:>10,.1f} {h['shares']:>12,} {h['share_type']:<6} {h['discretion']:<6}")

# ---------------------------------------------------------------------------
# PDF Report Generation
# ---------------------------------------------------------------------------

class ReportPDF(FPDF):
    def __init__(self, fund_name, report_date, filing_date, total_value, num_positions):
        super().__init__(orientation="L", unit="mm", format="Letter")
        self.fund_name = fund_name
        self.report_date = report_date
        self.filing_date = filing_date
        self.total_value_b = total_value / 1_000_000_000
        self.num_positions = num_positions
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 6, f"{self.fund_name} - 13F Top 20 Holdings Analysis", new_x="LMARGIN", new_y="NEXT", align="C")
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


def generate_pdf(holdings, fund_name, report_date, filing_date, total_value, num_positions, theses, output_path):
    """Generate the PDF report with LLM-generated theses."""
    pdf = ReportPDF(fund_name, report_date, filing_date, total_value, num_positions)
    pdf.add_page()

    columns = [
        ("#", 8, "C"), ("Ticker", 18, "C"), ("Company", 52, "L"),
        ("Position ($M)", 25, "R"), ("Shares", 25, "R"), ("% Port", 16, "R"),
        ("% S/O", 16, "R"), ("3M ADV", 16, "R"),
        ("Q-1 Chg", 22, "R"), ("Q-2 Chg", 22, "R"), ("Q-3 Chg", 22, "R"),
    ]

    total_width = sum(c[1] for c in columns)
    start_x = (pdf.w - total_width) / 2

    # Header row
    pdf.set_x(start_x)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255, 255, 255)
    for header, width, align in columns:
        pdf.cell(width, 6, header, border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)

    # Data rows
    for i, h in enumerate(holdings):
        pdf.set_x(start_x)
        pdf.set_fill_color(245, 245, 245) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
        pdf.set_font("Helvetica", "", 6.5)

        ticker = h["ticker"] if h["ticker"] else h["cusip"][:6]
        row_data = [
            (str(h["rank"]), 8, "C"), (ticker, 18, "C"), (h["name"][:28], 52, "L"),
            (f"${h['value_m']:,.1f}", 25, "R"), (format_shares(h["shares"]), 25, "R"),
            (f"{h['pct_portfolio']:.1f}%", 16, "R"),
            (h["pct_shares_outstanding"], 16, "R"), (h["pct_3m_adv"], 16, "R"),
        ]

        for text, width, align in row_data:
            pdf.cell(width, 5.5, text, border=1, align=align, fill=True)

        for delta_key, col_idx in [("delta_q1", 8), ("delta_q2", 9), ("delta_q3", 10)]:
            delta = h[delta_key]
            if delta == "NEW":
                pdf.set_text_color(41, 128, 185)
                pdf.set_font("Helvetica", "B", 6.5)
            elif delta.startswith("+"):
                pdf.set_text_color(39, 174, 96)
                pdf.set_font("Helvetica", "", 6.5)
            elif delta.startswith("-"):
                pdf.set_text_color(192, 57, 43)
                pdf.set_font("Helvetica", "", 6.5)
            else:
                pdf.set_text_color(0, 0, 0)
                pdf.set_font("Helvetica", "", 6.5)
            pdf.cell(columns[col_idx][1], 5.5, delta, border=1, align="R", fill=True)
            pdf.set_text_color(0, 0, 0)
        pdf.ln()

    # Footnotes
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 6)
    pdf.set_x(start_x)
    pdf.cell(0, 4, "Notes: 13F filings have a 45-day delay. Only long equity positions shown. Market values as of quarter-end.")

    # Thesis pages
    for i in range(0, len(holdings), 2):
        pdf.add_page(orientation="P")
        for j in range(2):
            if i + j >= len(holdings):
                break
            h = holdings[i + j]
            ticker = h["ticker"] if h["ticker"] else "N/A"

            # Thesis header
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_fill_color(44, 62, 80)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 8, f"  {h['rank']}. {h['name']} ({ticker})", new_x="LMARGIN", new_y="NEXT", fill=True)
            pdf.set_text_color(0, 0, 0)

            # Status line
            pdf.set_font("Helvetica", "B", 8)
            status = h["delta_q1"]
            if status == "NEW": s = "NEW POSITION"
            elif status.startswith("+"): s = f"Increased {status}"
            elif status.startswith("-"): s = f"Decreased {status}"
            else: s = "Unchanged"
            pdf.cell(0, 6, f"Position: ${h['value_m']:,.1f}M  |  {h['pct_portfolio']:.1f}% of Portfolio  |  {s}", new_x="LMARGIN", new_y="NEXT")

            # Thesis text
            pdf.set_font("Helvetica", "", 8)
            thesis = theses.get(h["cusip"], "Thesis analysis not available.")
            # Clean non-latin1 characters for fpdf
            thesis = thesis.encode('latin-1', errors='replace').decode('latin-1')
            pdf.multi_cell(0, 4.5, thesis)
            pdf.ln(5)

    pdf.output(str(output_path))
    return output_path

# ---------------------------------------------------------------------------
# Sector / Industry classification
# ---------------------------------------------------------------------------

def fetch_sector_data(holdings):
    """Look up sector and industry for each holding via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print_warn("yfinance not installed, cannot fetch sector data.")
        return {}

    sector_data = {}
    for h in holdings:
        ticker = h.get("ticker", "")
        if not ticker:
            continue
        try:
            t = yf.Ticker(ticker)
            info = t.info
            sector_data[ticker] = {
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
            }
            print_info(f"  {ticker}: {sector_data[ticker]['sector']} / {sector_data[ticker]['industry']}")
        except Exception:
            sector_data[ticker] = {"sector": "Unknown", "industry": "Unknown"}
    return sector_data


def display_sector_table(holdings, sector_data):
    """Display a table of holdings with sector/industry info and sector aggregation."""
    print(f"\n{C.BOLD}{C.UNDERLINE}{'#':>3}  {'Ticker':<8} {'Company':<26} {'Sector':<22} {'Industry':<28} {'% Port':>7}{C.RESET}")

    sector_totals = {}
    for h in holdings:
        ticker = h["ticker"] if h["ticker"] else "N/A"
        sd = sector_data.get(ticker, {"sector": "Unknown", "industry": "Unknown"})
        sector = sd["sector"][:21]
        industry = sd["industry"][:27]

        print(f"{h['rank']:>3}  {ticker:<8} {h['name'][:25]:<26} {sector:<22} {industry:<28} {h['pct_portfolio']:>6.1f}%")

        full_sector = sd["sector"]
        sector_totals[full_sector] = sector_totals.get(full_sector, 0) + h["pct_portfolio"]

    print(f"\n{C.BOLD}Sector Allocation (Top 20 Holdings):{C.RESET}")
    print(f"{C.UNDERLINE}{'Sector':<30} {'% of Portfolio':>14}{C.RESET}")
    for sector, pct in sorted(sector_totals.items(), key=lambda x: -x[1]):
        bar = "#" * int(pct)
        print(f"  {sector:<28} {pct:>6.1f}%  {C.CYAN}{bar}{C.RESET}")


# ---------------------------------------------------------------------------
# Stacked bar chart generation
# ---------------------------------------------------------------------------

def generate_holdings_chart(fund_data):
    """Generate a stacked bar chart of top 10 holdings % across quarters."""
    quarters_data = fund_data.quarters_data
    filings = fund_data.filings
    top_holdings = fund_data.top_holdings

    # Get quarter labels (report dates)
    quarter_labels = [f["report_date"] for f in filings]

    # Use top 10 holdings from current quarter
    top10_cusips = [(h["cusip"], h["ticker"] or h["name"][:12]) for h in top_holdings[:10]]

    # Build data: for each quarter, compute % of portfolio for each top10 holding
    chart_data = {label: [] for label in top10_cusips}
    other_pcts = []

    for qi, qd in enumerate(quarters_data):
        if qd is None:
            for cusip, label in top10_cusips:
                chart_data[(cusip, label)].append(0)
            other_pcts.append(0)
            continue

        total_val = sum(h["value"] for h in qd.values())
        if total_val == 0:
            for cusip, label in top10_cusips:
                chart_data[(cusip, label)].append(0)
            other_pcts.append(0)
            continue

        top10_total = 0
        for cusip, label in top10_cusips:
            if cusip in qd:
                pct = qd[cusip]["value"] / total_val * 100
                chart_data[(cusip, label)].append(pct)
                top10_total += pct
            else:
                chart_data[(cusip, label)].append(0)

        other_pcts.append(max(0, 100 - top10_total))

    # Reverse so oldest quarter is on the left
    quarter_labels = list(reversed(quarter_labels))
    other_pcts = list(reversed(other_pcts))
    for key in chart_data:
        chart_data[key] = list(reversed(chart_data[key]))

    # Plot
    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(quarter_labels))
    colors = cm.tab20(range(11))

    bottom = [0] * len(quarter_labels)
    bars = []
    labels = []

    for i, (key, pcts) in enumerate(chart_data.items()):
        cusip, label = key
        bar = ax.bar(x, pcts, bottom=bottom, color=colors[i], label=label, width=0.6)
        bars.append(bar)
        labels.append(label)
        bottom = [b + p for b, p in zip(bottom, pcts)]

    # Add "Other" on top
    ax.bar(x, other_pcts, bottom=bottom, color=colors[10], label="Other", width=0.6)

    ax.set_xlabel("Quarter End", fontsize=12)
    ax.set_ylabel("% of Portfolio", fontsize=12)
    ax.set_title(f"{fund_data.fund_name} - Top 10 Holdings by Quarter", fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(quarter_labels, rotation=45, ha="right")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), fontsize=8)
    ax.set_ylim(0, 105)
    plt.tight_layout()

    # Save
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', fund_data.fund_name).strip('_')
    out_path = Path.home() / "Desktop" / f"{safe_name}_holdings_chart_{datetime.now().strftime('%Y%m%d')}.png"
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print_success(f"Chart saved to: {out_path}")
    if sys.platform == "darwin":
        os.system(f'open "{out_path}"')
    return out_path


def generate_sector_chart(fund_data):
    """Generate a stacked bar chart at the SECTOR level across quarters."""
    quarters_data = fund_data.quarters_data
    filings = fund_data.filings
    sector_data = fund_data.sector_data

    # Build a cusip -> ticker map from all quarters
    cusip_ticker = {}
    for h in fund_data.top_holdings:
        if h["ticker"]:
            cusip_ticker[h["cusip"]] = h["ticker"]

    quarter_labels = [f["report_date"] for f in filings]

    # For each quarter, compute sector % allocation
    all_sectors = set()
    quarter_sector_pcts = []

    for qi, qd in enumerate(quarters_data):
        sector_pcts = {}
        if qd is None:
            quarter_sector_pcts.append({})
            continue

        total_val = sum(h["value"] for h in qd.values())
        if total_val == 0:
            quarter_sector_pcts.append({})
            continue

        for cusip, holding in qd.items():
            ticker = cusip_ticker.get(cusip, "")
            sd = sector_data.get(ticker, {"sector": "Unknown"})
            sector = sd["sector"]
            pct = holding["value"] / total_val * 100
            sector_pcts[sector] = sector_pcts.get(sector, 0) + pct
            all_sectors.add(sector)

        quarter_sector_pcts.append(sector_pcts)

    # Sort sectors by current quarter allocation
    current_pcts = quarter_sector_pcts[0] if quarter_sector_pcts else {}
    sorted_sectors = sorted(all_sectors, key=lambda s: current_pcts.get(s, 0), reverse=True)
    top_sectors = sorted_sectors[:10]
    if len(sorted_sectors) > 10:
        top_sectors.append("Other Sectors")

    # Reverse for chronological order
    quarter_labels = list(reversed(quarter_labels))
    quarter_sector_pcts = list(reversed(quarter_sector_pcts))

    # Plot
    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(quarter_labels))
    colors = cm.Set3(range(len(top_sectors)))

    bottom = [0] * len(quarter_labels)
    for i, sector in enumerate(top_sectors):
        pcts = []
        for qsp in quarter_sector_pcts:
            if sector == "Other Sectors":
                other_total = sum(v for k, v in qsp.items() if k not in sorted_sectors[:10])
                pcts.append(other_total)
            else:
                pcts.append(qsp.get(sector, 0))
        ax.bar(x, pcts, bottom=bottom, color=colors[i], label=sector, width=0.6)
        bottom = [b + p for b, p in zip(bottom, pcts)]

    ax.set_xlabel("Quarter End", fontsize=12)
    ax.set_ylabel("% of Portfolio", fontsize=12)
    ax.set_title(f"{fund_data.fund_name} - Sector Allocation by Quarter", fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(quarter_labels, rotation=45, ha="right")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), fontsize=8)
    ax.set_ylim(0, 105)
    plt.tight_layout()

    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', fund_data.fund_name).strip('_')
    out_path = Path.home() / "Desktop" / f"{safe_name}_sector_chart_{datetime.now().strftime('%Y%m%d')}.png"
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print_success(f"Sector chart saved to: {out_path}")
    if sys.platform == "darwin":
        os.system(f'open "{out_path}"')
    return out_path


# ---------------------------------------------------------------------------
# Core data fetching workflow
# ---------------------------------------------------------------------------

class FundData:
    """Holds all fetched data for the current fund."""
    def __init__(self):
        self.fund_name = ""
        self.cik = ""
        self.filings = []
        self.quarters_data = []
        self.top_holdings = []
        self.total_value = 0
        self.num_positions = 0
        self.theses = {}
        self.sector_data = {}  # ticker -> {"sector": ..., "industry": ...}


def fetch_fund_data(fund_input, session):
    """Full pipeline: resolve name -> fetch filings -> parse -> enrich."""
    data = FundData()

    # Step 1: Resolve name
    print_step("1/6", "Resolving fund name...")
    resolved_name = resolve_fund_name(fund_input)
    print_info(f"'{fund_input}' -> '{resolved_name}'")

    cik, display_name = lookup_cik(session, resolved_name)
    if not cik:
        print_error(f"Could not find CIK for '{resolved_name}'. Try a different name.")
        return None

    data.cik = cik
    data.fund_name = display_name
    print_success(f"{display_name} (CIK: {cik})")

    # Step 2: Fetch filing list
    print_step("2/6", "Fetching 13F-HR filings from SEC EDGAR...")
    filings, display_name_2, cik = get_filing_list(session, cik, 4)
    if not filings:
        print_error("No 13F-HR filings found for this fund.")
        return None

    data.filings = filings
    data.fund_name = display_name_2 or data.fund_name

    for f in filings:
        print_info(f"{f['form']}  Filed: {f['filing_date']}  Period: {f['report_date']}")

    # Step 3: Parse XML
    print_step("3/6", "Downloading and parsing info tables...")
    quarters_data = []
    for i, filing in enumerate(filings):
        q_label = f"Q0 (current)" if i == 0 else f"Q-{i}"
        info_url = find_info_table_url(session, cik, filing["accession"])
        if not info_url:
            print_warn(f"  {q_label}: Could not find info table")
            quarters_data.append(None)
            continue
        resp = sec_get(session, info_url)
        if not resp:
            quarters_data.append(None)
            continue
        holdings = parse_info_table(resp.text)
        print_success(f"{q_label}: {len(holdings)} positions parsed")
        quarters_data.append(holdings)

    data.quarters_data = quarters_data

    # Step 4: Compute top 20
    print_step("4/6", "Computing top 20 holdings and quarter-over-quarter changes...")
    top_holdings, total_value = compute_top_holdings(quarters_data)
    if not top_holdings:
        print_error("Could not compute holdings.")
        return None

    data.total_value = total_value
    data.num_positions = len(quarters_data[0]) if quarters_data[0] else 0
    print_success(f"Top {len(top_holdings)} positions ranked. Total 13F value: ${total_value/1e9:.1f}B")

    # Step 5: Enrich tickers
    print_step("5/6", "Resolving tickers (OpenFIGI)...")
    top_holdings = enrich_tickers(top_holdings)
    found = sum(1 for h in top_holdings if h["ticker"])
    print_success(f"Resolved {found}/{len(top_holdings)} tickers")

    # Step 6: Market data
    print_step("6/6", "Fetching market data (shares outstanding, volume)...")
    top_holdings = enrich_market_data(top_holdings)
    print_success("Market data enrichment complete")

    data.top_holdings = top_holdings
    return data


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------

def show_help():
    print(f"""
{C.BOLD}Available Commands:{C.RESET}
  {C.CYAN}<fund name>{C.RESET}       Fetch and analyze a fund (e.g., 'viking', 'pershing')
  {C.CYAN}show{C.RESET}              Show top 20 holdings table again
  {C.CYAN}show all{C.RESET}          Show ALL positions (not just top 20)
  {C.CYAN}detail <#>{C.RESET}        Show detailed info for position # (e.g., 'detail 3')
  {C.CYAN}chart{C.RESET}             Stacked bar chart of top 10 holdings across quarters
  {C.CYAN}sectors{C.RESET}           Show sector/industry breakdown for top holdings
  {C.CYAN}sector chart{C.RESET}      Stacked bar chart of sector allocation across quarters
  {C.CYAN}thesis{C.RESET}            Generate LLM investment theses for all top 20
  {C.CYAN}thesis <#>{C.RESET}        Generate LLM thesis for a specific position
  {C.CYAN}report{C.RESET}            Generate PDF report (with theses)
  {C.CYAN}ask <question>{C.RESET}    Ask the LLM anything about the current data
  {C.CYAN}compare <fund>{C.RESET}    Compare current fund with another (coming soon)
  {C.CYAN}help{C.RESET}              Show this help
  {C.CYAN}quit{C.RESET}              Exit
""")

def interactive_loop():
    """Main interactive terminal loop."""
    banner()

    # Check Ollama
    if check_ollama():
        print_success(f"Ollama connected (model: {get_available_model()})")
    else:
        print_warn("Ollama not running. LLM features disabled. Start with: ollama serve")
        print_info("Data fetching and display will still work fine.")

    session = make_session()
    current_data = None

    while True:
        try:
            prompt_text = f"\n{C.BOLD}{C.CYAN}13F>{C.RESET} "
            user_input = input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C.DIM}Goodbye!{C.RESET}")
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        # --- EXIT ---
        if cmd in ("quit", "exit", "q"):
            print(f"{C.DIM}Goodbye!{C.RESET}")
            break

        # --- HELP ---
        elif cmd == "help":
            show_help()

        # --- SHOW TABLE ---
        elif cmd == "show":
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
            else:
                display_holdings_table(
                    current_data.top_holdings, current_data.fund_name,
                    current_data.total_value, current_data.filings
                )

        # --- SHOW ALL ---
        elif cmd == "show all":
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
            else:
                display_all_positions(current_data.quarters_data, 0)

        # --- DETAIL ---
        elif cmd.startswith("detail"):
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
                continue
            parts = cmd.split()
            if len(parts) < 2 or not parts[1].isdigit():
                print_warn("Usage: detail <rank number>  (e.g., 'detail 3')")
                continue
            rank = int(parts[1])
            matches = [h for h in current_data.top_holdings if h["rank"] == rank]
            if not matches:
                print_warn(f"Position #{rank} not found.")
                continue
            h = matches[0]
            ticker = h["ticker"] if h["ticker"] else "N/A"
            print(f"""
{C.BOLD}{C.CYAN}{'='*60}
  #{h['rank']}. {h['name']} ({ticker})
{'='*60}{C.RESET}
  CUSIP:               {h['cusip']}
  Title of Class:      {h['title']}
  Market Value:        ${h['value_m']:,.1f}M
  Shares Held:         {h['shares']:,}
  % of Portfolio:      {h['pct_portfolio']:.1f}%
  % Shares Outstanding:{h['pct_shares_outstanding']:>8}
  3-Month ADV:         {h['pct_3m_adv']:>8}

  {C.BOLD}Quarter-over-Quarter Changes:{C.RESET}
  Q-1 (most recent):   {colorize_delta(h['delta_q1'])}
  Q-2:                 {colorize_delta(h['delta_q2'])}
  Q-3:                 {colorize_delta(h['delta_q3'])}
""")
            # Show share counts across quarters
            cusip = h["cusip"]
            print(f"  {C.BOLD}Share History:{C.RESET}")
            for qi, qd in enumerate(current_data.quarters_data):
                if qd and cusip in qd:
                    qlabel = "Current " if qi == 0 else f"Q-{qi}     "
                    print(f"    {qlabel}: {qd[cusip]['shares']:>12,} shares  (${qd[cusip]['value']/1e6:>10,.1f}M)")
                else:
                    qlabel = "Current " if qi == 0 else f"Q-{qi}     "
                    print(f"    {qlabel}: {'--':>12}")

        # --- CHART (stacked bar of top holdings) ---
        elif cmd == "chart":
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
            else:
                print_header("Generating stacked bar chart of top 10 holdings...")
                generate_holdings_chart(current_data)

        # --- SECTORS ---
        elif cmd == "sectors":
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
            else:
                if not current_data.sector_data:
                    print_header("Fetching sector/industry data via Yahoo Finance...")
                    current_data.sector_data = fetch_sector_data(current_data.top_holdings)
                display_sector_table(current_data.top_holdings, current_data.sector_data)

        # --- SECTOR CHART ---
        elif cmd == "sector chart":
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
            else:
                if not current_data.sector_data:
                    print_header("Fetching sector/industry data via Yahoo Finance...")
                    current_data.sector_data = fetch_sector_data(current_data.top_holdings)
                print_header("Generating sector allocation chart...")
                generate_sector_chart(current_data)

        # --- THESIS for all ---
        elif cmd == "thesis":
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
                continue
            if not check_ollama():
                print_error("Ollama not running. Start with: ollama serve")
                continue

            print_header(f"Generating investment theses for {current_data.fund_name} top {len(current_data.top_holdings)} holdings...")
            print_info("This may take a few minutes (each thesis ~5-10 seconds)...\n")

            for h in current_data.top_holdings:
                ticker = h["ticker"] if h["ticker"] else h["name"]
                print(f"{C.BOLD}{C.CYAN}  [{h['rank']}/{len(current_data.top_holdings)}] {h['name']} ({ticker}){C.RESET}")
                print(f"  {C.DIM}", end="")
                thesis = llm_generate_thesis(h, current_data.fund_name)
                print(f"{C.RESET}")
                current_data.theses[h["cusip"]] = thesis

            print_success(f"Generated {len(current_data.theses)} theses.")

        # --- THESIS for specific position ---
        elif cmd.startswith("thesis "):
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
                continue
            if not check_ollama():
                print_error("Ollama not running. Start with: ollama serve")
                continue
            parts = cmd.split()
            if len(parts) < 2 or not parts[1].isdigit():
                print_warn("Usage: thesis <rank number>")
                continue
            rank = int(parts[1])
            matches = [h for h in current_data.top_holdings if h["rank"] == rank]
            if not matches:
                print_warn(f"Position #{rank} not found.")
                continue
            h = matches[0]
            ticker = h["ticker"] if h["ticker"] else h["name"]
            print(f"\n{C.BOLD}{h['rank']}. {h['name']} ({ticker}){C.RESET}")
            print(f"Position: ${h['value_m']:,.1f}M | {h['pct_portfolio']:.1f}% of Portfolio\n")
            thesis = llm_generate_thesis(h, current_data.fund_name)
            current_data.theses[h["cusip"]] = thesis

        # --- REPORT ---
        elif cmd == "report":
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
                continue

            # Generate theses if not already done
            if not current_data.theses and check_ollama():
                print_header("Generating investment theses before building PDF...")
                print_info("This may take a few minutes...\n")
                for h in current_data.top_holdings:
                    ticker = h["ticker"] if h["ticker"] else h["name"]
                    print(f"  [{h['rank']}/{len(current_data.top_holdings)}] {h['name'][:30]}...", end=" ", flush=True)
                    thesis = llm_generate_thesis(h, current_data.fund_name)
                    current_data.theses[h["cusip"]] = thesis
                    print(f"{C.GREEN}done{C.RESET}")

            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', current_data.fund_name).strip('_')
            output_path = Path.home() / "Desktop" / f"{safe_name}_13F_Report_{datetime.now().strftime('%Y%m%d')}.pdf"

            print_step("PDF", "Generating report...")
            generate_pdf(
                current_data.top_holdings,
                current_data.fund_name,
                current_data.filings[0]["report_date"],
                current_data.filings[0]["filing_date"],
                current_data.total_value,
                current_data.num_positions,
                current_data.theses,
                output_path,
            )
            print_success(f"Report saved to: {output_path}")

            # Open it
            if sys.platform == "darwin":
                os.system(f'open "{output_path}"')

        # --- ASK (free-form question about data) ---
        elif cmd.startswith("ask "):
            if not current_data:
                print_warn("No fund loaded. Enter a fund name first.")
                continue
            if not check_ollama():
                print_error("Ollama not running. Start with: ollama serve")
                continue

            question = user_input[4:].strip()
            # Build context from current data
            holdings_summary = "\n".join([
                f"#{h['rank']} {h['ticker'] or h['name']}: ${h['value_m']:,.1f}M, {h['pct_portfolio']:.1f}% of portfolio, "
                f"Q-1: {h['delta_q1']}, Q-2: {h['delta_q2']}, Q-3: {h['delta_q3']}, "
                f"Shares: {h['shares']:,}, %S/O: {h['pct_shares_outstanding']}, ADV: {h['pct_3m_adv']}"
                for h in current_data.top_holdings
            ])

            system = (
                f"You are a financial research analyst. The user is analyzing {current_data.fund_name}'s "
                f"13F filing (as of {current_data.filings[0]['report_date']}). "
                f"Total 13F value: ${current_data.total_value/1e9:.1f}B across {current_data.num_positions} positions. "
                f"Answer based on the data provided. Be concise and analytical."
            )
            prompt = f"Here are the top 20 holdings:\n{holdings_summary}\n\nUser question: {question}"

            print()
            llm_generate(prompt, system, stream=True)

        # --- Assume it's a fund name ---
        else:
            # Check if it looks like a known command typo
            if cmd in ("show all positions", "all", "list"):
                if current_data:
                    display_all_positions(current_data.quarters_data, 0)
                else:
                    print_warn("No fund loaded. Enter a fund name first.")
                continue

            # Treat as fund name
            print_header(f"Fetching data for: {user_input}")
            current_data = fetch_fund_data(user_input, session)

            if current_data:
                # Display the data
                display_holdings_table(
                    current_data.top_holdings, current_data.fund_name,
                    current_data.total_value, current_data.filings
                )

                print(f"\n{C.BOLD}What would you like to do?{C.RESET}")
                print(f"  {C.CYAN}show all{C.RESET}      - See all {current_data.num_positions} positions")
                print(f"  {C.CYAN}detail <#>{C.RESET}    - Deep dive on a position")
                print(f"  {C.CYAN}chart{C.RESET}         - Stacked bar chart of top holdings across quarters")
                print(f"  {C.CYAN}sectors{C.RESET}       - Sector/industry breakdown")
                print(f"  {C.CYAN}sector chart{C.RESET}  - Sector allocation chart across quarters")
                print(f"  {C.CYAN}thesis{C.RESET}        - Generate LLM investment theses")
                print(f"  {C.CYAN}report{C.RESET}        - Generate PDF report")
                print(f"  {C.CYAN}ask <question>{C.RESET} - Ask anything about this data")


if __name__ == "__main__":
    interactive_loop()

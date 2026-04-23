#!/usr/bin/env python3
"""
Shared data-fetching functions for 13F SEC Filing Analyzer.
No terminal UI, no print statements — pure data in, data out.
"""

import json
import re
import time
from datetime import datetime
from urllib.parse import urlparse

import requests
import xml.etree.ElementTree as ET

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

# Wayback Machine
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_WEB_URL = "https://web.archive.org/web"
REQUEST_DELAY = 1.5
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3

TEAM_URL_KEYWORDS = [
    "team", "people", "about", "leadership", "staff", "who-we-are",
    "our-team", "professionals", "partners", "bios", "executives",
    "about-us", "management", "firm", "advisors", "principals",
    "directors", "founders", "our-people", "who-we-are", "members",
    "bio", "personnel", "colleagues", "humans", "crew",
]

# Extra keywords to match in link *text* on the homepage (not just URL paths)
TEAM_LINK_TEXT_KEYWORDS = [
    "team", "people", "leadership", "staff", "our team", "the team",
    "our people", "meet the team", "meet us", "who we are", "about us",
    "our firm", "the firm", "professionals", "our professionals",
    "our partners", "our advisors", "management team",
]

PORTFOLIO_URL_KEYWORDS = [
    "portfolio", "companies", "investments", "our-companies",
    "holdings", "ventures", "startups", "brands", "fund",
]


# ---------------------------------------------------------------------------
# Ollama LLM
# ---------------------------------------------------------------------------

def check_ollama():
    """Check if Ollama is running and a model is available."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            if any(OLLAMA_MODEL in m for m in models):
                return True
            if models:
                return True
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


def llm_generate(prompt, system_prompt=None):
    """Generate text using Ollama. Returns full text (no streaming)."""
    model = get_available_model()
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 500},
    }
    if system_prompt:
        payload["system"] = system_prompt

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=120,
        )
        return resp.json().get("response", "")
    except Exception:
        return ""


def llm_generate_stream(prompt, system_prompt=None):
    """Generate text using Ollama with streaming. Yields tokens."""
    model = get_available_model()
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.7, "num_predict": 500},
    }
    if system_prompt:
        payload["system"] = system_prompt

    try:
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
                if token:
                    yield token
                if chunk.get("done", False):
                    break
    except Exception:
        yield ""


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

    return llm_generate(prompt, system_prompt)


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

def _find_text(element, tag, ns_dict):
    prefix = "ns:" if ns_dict else ""
    el = element.find(f"{prefix}{tag}", ns_dict) if ns_dict else element.find(tag)
    if el is not None and el.text:
        return el.text
    return ""


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


def fetch_sector_data(holdings):
    """Look up sector and industry for each holding via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
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
        except Exception:
            sector_data[ticker] = {"sector": "Unknown", "industry": "Unknown"}
    return sector_data


def format_shares(shares):
    if shares >= 1_000_000:
        return f"{shares / 1_000_000:.1f}M"
    elif shares >= 1_000:
        return f"{shares / 1_000:.0f}K"
    return str(shares)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def fetch_fund_data(fund_input, session=None, progress_callback=None):
    """Full pipeline: resolve name -> fetch filings -> parse -> enrich.

    Args:
        fund_input: Fund name or alias
        session: Optional requests session (created if None)
        progress_callback: Optional callable(step, message) for progress updates

    Returns:
        dict with fund data, or None on failure
    """
    if session is None:
        session = make_session()

    def report(step, msg):
        if progress_callback:
            progress_callback(step, msg)

    # Step 1: Resolve name
    report("1/6", "Resolving fund name...")
    resolved_name = resolve_fund_name(fund_input)

    cik, display_name = lookup_cik(session, resolved_name)
    if not cik:
        return None

    report("1/6", f"Found: {display_name} (CIK: {cik})")

    # Step 2: Fetch filing list
    report("2/6", "Fetching 13F-HR filings from SEC EDGAR...")
    filings, display_name_2, cik = get_filing_list(session, cik, 4)
    if not filings:
        return None

    fund_name = display_name_2 or display_name

    # Step 3: Parse XML
    report("3/6", "Downloading and parsing info tables...")
    quarters_data = []
    for i, filing in enumerate(filings):
        info_url = find_info_table_url(session, cik, filing["accession"])
        if not info_url:
            quarters_data.append(None)
            continue
        resp = sec_get(session, info_url)
        if not resp:
            quarters_data.append(None)
            continue
        holdings = parse_info_table(resp.text)
        quarters_data.append(holdings)

    # Step 4: Compute top 20
    report("4/6", "Computing top 20 holdings...")
    top_holdings, total_value = compute_top_holdings(quarters_data)
    if not top_holdings:
        return None

    num_positions = len(quarters_data[0]) if quarters_data[0] else 0

    # Step 5: Enrich tickers
    report("5/6", "Resolving tickers (OpenFIGI)...")
    top_holdings = enrich_tickers(top_holdings)

    # Step 6: Market data
    report("6/6", "Fetching market data...")
    top_holdings = enrich_market_data(top_holdings)

    return {
        "fund_name": fund_name,
        "cik": cik,
        "filings": filings,
        "quarters_data": quarters_data,
        "top_holdings": top_holdings,
        "total_value": total_value,
        "num_positions": num_positions,
    }


# ---------------------------------------------------------------------------
# Wayback Machine
# ---------------------------------------------------------------------------

_wayback_session = requests.Session()
_wayback_session.headers.update({
    "User-Agent": "13FTopIdeas-WaybackScraper/1.0 (research@13ftopideas.com)",
})


def _get_with_retries(url, params=None, timeout=REQUEST_TIMEOUT):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            resp = _wayback_session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(attempt * 5)
            else:
                raise


def get_snapshots(url, from_year=None, to_year=None):
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "collapse": "timestamp:8",
    }
    if from_year:
        params["from"] = f"{from_year}0101"
    if to_year:
        params["to"] = f"{to_year}1231"

    try:
        resp = _get_with_retries(WAYBACK_CDX_URL, params=params)
        data = resp.json()
        if len(data) < 2:
            return []
        return [{"timestamp": row[0], "url": row[1], "status": row[2]} for row in data[1:]]
    except Exception:
        return []


def find_closest_snapshot(snapshots, target_date):
    if not snapshots:
        return None

    target = int(target_date)
    best = None
    best_diff = float('inf')

    for snap in snapshots:
        ts = int(snap["timestamp"][:8])
        diff = abs(ts - target)
        if diff < best_diff:
            best_diff = diff
            best = snap

    if best and best_diff <= 900000:
        return best
    return None


def fetch_snapshot_html(timestamp, url):
    snapshot_url = f"{WAYBACK_WEB_URL}/{timestamp}id_/{url}"
    try:
        resp = _get_with_retries(snapshot_url)
        return resp.text
    except Exception:
        return None


def fetch_snapshot_html_replay(timestamp, url):
    snapshot_url = f"{WAYBACK_WEB_URL}/{timestamp}/{url}"
    try:
        resp = _get_with_retries(snapshot_url)
        return resp.text
    except Exception:
        return None


def _score_candidate(url, mode):
    """Score a candidate URL by how likely it is to be the right page for the mode.
    Higher score = better match. Negative score = wrong mode, should be excluded."""
    path = urlparse(url).path.lower().rstrip("/")
    segments = [s for s in path.split("/") if s]
    last_segment = segments[-1] if segments else ""

    # Penalize URLs from the WRONG mode (e.g., /portfolio when looking for team)
    wrong_keywords = PORTFOLIO_URL_KEYWORDS if mode == "team" else TEAM_URL_KEYWORDS
    for wk in wrong_keywords:
        if wk in path:
            return -10  # Exclude this candidate

    # Penalize deep paths (likely sub-sub-pages, individual bios, blog posts)
    if len(segments) > 3:
        return -5

    # Score based on how specific the match is
    if mode == "team":
        # Exact team pages score highest
        strong = ["our-team", "the-team", "ourteam", "our-people", "team", "people"]
        medium = ["leadership", "staff", "professionals", "advisors", "principals",
                  "directors", "founders", "members", "firm"]
        weak = ["about-us", "who-we-are", "about"]
    else:
        strong = ["our-companies", "our-portfolio", "portfolio", "companies", "investments"]
        medium = ["holdings", "ventures", "startups", "brands"]
        weak = ["fund", "about"]

    for kw in strong:
        if last_segment == kw or path.endswith(f"/{kw}"):
            return 100
        if kw in last_segment:
            return 80
    for kw in medium:
        if last_segment == kw or path.endswith(f"/{kw}"):
            return 60
        if kw in last_segment:
            return 40
    for kw in weak:
        if last_segment == kw:
            return 20
        if kw in path:
            return 10

    return 5  # Unknown but not excluded


def discover_pages(base_url, mode="team"):
    keywords = TEAM_URL_KEYWORDS if mode == "team" else PORTFOLIO_URL_KEYWORDS

    if not base_url.startswith("http"):
        base_url = "https://" + base_url
    parsed = urlparse(base_url)
    homepage = f"{parsed.scheme}://{parsed.netloc}/"

    cdx_candidates = _discover_via_cdx(parsed.netloc, keywords)
    crawl_candidates = _discover_via_homepage(homepage, keywords)

    seen = set()
    all_candidates = []
    for url in cdx_candidates + crawl_candidates:
        normalized = url.rstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            all_candidates.append(url)

    # Score, filter out wrong-mode pages, and sort by relevance
    scored = [(url, _score_candidate(url, mode)) for url in all_candidates]
    scored = [(url, s) for url, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)

    return [url for url, _ in scored]


def _discover_via_cdx(domain, keywords):
    candidates = []
    for keyword in keywords:
        params = {
            "url": f"{domain}/*{keyword}*",
            "output": "json",
            "fl": "original",
            "filter": "statuscode:200",
            "collapse": "urlkey",
            "limit": 10,
        }
        try:
            resp = _get_with_retries(WAYBACK_CDX_URL, params=params)
            data = resp.json()
            if len(data) > 1:
                for row in data[1:]:
                    url = row[0]
                    if any(url.lower().endswith(ext) for ext in
                           ['.jpg', '.png', '.gif', '.css', '.js', '.svg',
                            '.pdf', '.ico', '.woff', '.woff2', '.ttf']):
                        continue
                    candidates.append(url)
        except Exception:
            continue
    return candidates


def _discover_via_homepage(homepage, keywords):
    from bs4 import BeautifulSoup

    candidates = []
    snapshots = get_snapshots(homepage)
    if not snapshots:
        return candidates

    latest = snapshots[-1]
    html = fetch_snapshot_html(latest["timestamp"], homepage)
    if not html:
        return candidates

    soup = BeautifulSoup(html, "html.parser")
    parsed_home = urlparse(homepage)

    # Use broader link-text keywords in addition to URL-path keywords
    text_keywords = TEAM_LINK_TEXT_KEYWORDS if keywords is TEAM_URL_KEYWORDS else keywords

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        text = link.get_text(strip=True).lower()

        if href.startswith("/"):
            href = f"{parsed_home.scheme}://{parsed_home.netloc}{href}"
        elif not href.startswith("http"):
            continue

        link_parsed = urlparse(href)
        if link_parsed.netloc and link_parsed.netloc != parsed_home.netloc:
            continue

        # Skip asset files
        if any(href.lower().endswith(ext) for ext in
               ['.jpg', '.png', '.gif', '.css', '.js', '.svg', '.pdf']):
            continue

        path_lower = link_parsed.path.lower()
        # Match against URL-path keywords
        for kw in keywords:
            if kw in path_lower:
                candidates.append(href)
                break
        else:
            # Match against link text keywords (catches "Our Firm" -> /firm etc.)
            for kw in text_keywords:
                if kw in text:
                    candidates.append(href)
                    break

    return candidates


# ---------------------------------------------------------------------------
# Name / Company extraction
# ---------------------------------------------------------------------------

def clean_name_text(text):
    text = text.strip()
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return text.strip()


def is_person_name(text):
    text = text.strip()
    if not text or len(text) < 3 or len(text) > 40:
        return False
    words = text.split()
    if len(words) < 2 or len(words) > 4:
        return False
    for w in words:
        if not w[0].isupper():
            return False
    if any(len(w) > 15 for w in words):
        return False
    skip_tokens = [
        "contact", "email", "phone", "address", "copyright", "privacy",
        "terms", "about", "home", "menu", "search", "login", "sign",
        "read", "more", "learn", "view", "see", "click", "download",
        "subscribe", "follow", "share", "next", "previous", "back",
        "all rights", "reserved", "inc", "llc", "corp", "ltd",
        "get in touch", "partner", "director", "manager", "president",
        "chief", "officer", "head of", "vice", "vp of", "cfo", "cmo",
        "cto", "ceo", "growth", "talent", "engineering", "finance",
        "compliance", "operations", "technology", "investor", "deputy",
        "venture", "capital", "founding", "senior", "junior", "associate",
        "analyst", "principal", "counsel", "general", "managing",
        "open source", "go to", "market", "policy", "council",
        "real estate", "private equity", "public equity", "fixed income",
        "our team", "our people", "meet the", "the team", "join us",
        "portfolio", "investment", "research", "writing", "opportunities",
        "credit", "advisory", "community", "platform", "strategy",
    ]
    lower = text.lower()
    for token in skip_tokens:
        if token in lower:
            return False
    if not re.match(r"^[A-Za-z' \-\.]+$", text):
        return False
    for w in words:
        clean_w = w.strip(".-'")
        if len(clean_w) < 2:
            return False
    return True


def is_company_name(text):
    text = text.strip()
    if not text or len(text) < 2 or len(text) > 80:
        return False
    words = text.split()
    if len(words) > 10:
        return False
    skip_tokens = [
        "contact", "email", "phone", "copyright", "privacy", "terms",
        "home", "menu", "search", "login", "sign up", "read more",
        "learn more", "view all", "see all", "click here", "download",
        "subscribe", "follow us", "share", "all rights", "reserved",
        "cookie", "disclaimer",
    ]
    lower = text.lower()
    for token in skip_tokens:
        if token in lower:
            return False
    alpha_chars = sum(1 for c in text if c.isalnum() or c in ' .-&')
    if len(text) > 0 and alpha_chars / len(text) < 0.7:
        return False
    return True


def _get_direct_text(element):
    direct = element.find(string=True, recursive=False)
    if direct and direct.strip():
        return direct.strip()
    children = list(element.children)
    text_children = [c for c in children if hasattr(c, 'get_text') or (isinstance(c, str) and c.strip())]
    if len(text_children) == 1:
        child = text_children[0]
        if isinstance(child, str):
            return child.strip()
        return child.get_text(strip=True)
    return element.get_text(separator=" ", strip=True)


def _extract_names_from_json(data, names_set):
    name_keys = {"name", "fullName", "full_name", "displayName", "display_name",
                 "personName", "person_name", "teamMember", "team_member",
                 "authorName", "author_name", "author", "member", "person",
                 "firstName", "first_name", "title"}
    first_keys = {"firstName", "first_name", "first"}
    last_keys = {"lastName", "last_name", "last"}

    if isinstance(data, dict):
        first = None
        last = None
        for k, v in data.items():
            if k in first_keys and isinstance(v, str) and v.strip():
                first = v.strip()
            if k in last_keys and isinstance(v, str) and v.strip():
                last = v.strip()
        if first and last:
            candidate = f"{first} {last}"
            if is_person_name(candidate):
                names_set.add(candidate)

        for k, v in data.items():
            if k in name_keys and isinstance(v, str) and v.strip():
                candidate = clean_name_text(v.strip())
                if is_person_name(candidate):
                    names_set.add(candidate)
            if isinstance(v, (dict, list)):
                _extract_names_from_json(v, names_set)

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                _extract_names_from_json(item, names_set)


def _extract_names_from_scripts(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    names = set()

    for script in soup.find_all("script"):
        text = script.string
        if not text:
            continue

        if script.get("id") == "__NEXT_DATA__":
            try:
                data = json.loads(text)
                _extract_names_from_json(data, names)
            except (json.JSONDecodeError, ValueError):
                pass
            continue

        if script.get("type") == "application/ld+json":
            try:
                data = json.loads(text)
                _extract_names_from_json(data, names)
            except (json.JSONDecodeError, ValueError):
                pass
            continue

        for match in re.finditer(r'(?:JSON\.parse\(|=\s*)(\{["\'](?:name|team|member|person|people|staff|firstName).*?\})', text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                _extract_names_from_json(data, names)
            except (json.JSONDecodeError, ValueError):
                pass

        for match in re.finditer(r'(\[\s*\{[^]]{20,}\}\s*\])', text):
            try:
                data = json.loads(match.group(1))
                _extract_names_from_json(data, names)
            except (json.JSONDecodeError, ValueError):
                pass

    return names


def extract_team_members(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    names = _extract_names_from_scripts(html)

    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    def try_add_name(raw_text):
        text = clean_name_text(raw_text)
        candidates = [text]
        words = text.split()
        if len(words) > 2:
            candidates.append(" ".join(words[:2]))
            candidates.append(" ".join(words[:3]))
        for candidate in candidates:
            if is_person_name(candidate):
                names.add(candidate)
                return

    team_selectors = [
        "[class*='team']", "[class*='member']", "[class*='person']",
        "[class*='people']", "[class*='staff']", "[class*='partner']",
        "[class*='bio']", "[class*='leader']", "[class*='executive']",
        "[class*='name']",
    ]
    for selector in team_selectors:
        for el in soup.select(selector):
            for name_el in el.find_all(["h2", "h3", "h4", "h5", "strong", "b"]):
                text = _get_direct_text(name_el)
                try_add_name(text)
            for name_el in el.find_all(["span", "a"], class_=lambda c: c and any(
                    kw in (c if isinstance(c, str) else " ".join(c)).lower()
                    for kw in ["name", "title", "person"])):
                text = _get_direct_text(name_el)
                try_add_name(text)

    for tag in soup.find_all(["h2", "h3", "h4"]):
        text = tag.get_text(separator=" ", strip=True)
        try_add_name(text)

    for li in soup.find_all("li"):
        text = li.get_text(separator=" ", strip=True)
        first_line = text.split("\n")[0].strip()
        try_add_name(first_line)

    return sorted(names)


def _extract_companies_from_json(data, companies_set):
    company_keys = {"name", "company", "companyName", "company_name", "title",
                    "portfolio_company", "portfolioCompany", "brand", "startup",
                    "displayName", "display_name"}

    if isinstance(data, dict):
        for k, v in data.items():
            if k in company_keys and isinstance(v, str) and v.strip():
                candidate = v.strip()
                if is_company_name(candidate) and len(candidate) > 1:
                    companies_set.add(candidate)
            if isinstance(v, (dict, list)):
                _extract_companies_from_json(v, companies_set)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                _extract_companies_from_json(item, companies_set)


def _extract_companies_from_scripts(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    companies = set()

    for script in soup.find_all("script"):
        text = script.string
        if not text:
            continue
        if script.get("id") == "__NEXT_DATA__" or script.get("type") == "application/ld+json":
            try:
                data = json.loads(text)
                _extract_companies_from_json(data, companies)
            except (json.JSONDecodeError, ValueError):
                pass

    return companies


def extract_companies(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    companies = _extract_companies_from_scripts(html)

    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    company_selectors = [
        "[class*='portfolio']", "[class*='company']", "[class*='investment']",
        "[class*='holding']", "[class*='fund']", "[class*='venture']",
        "[class*='startup']", "[class*='brand']",
    ]
    for selector in company_selectors:
        for el in soup.select(selector):
            for name_el in el.find_all(["h2", "h3", "h4", "h5", "strong", "b", "span", "a", "p"]):
                text = name_el.get_text(strip=True)
                if is_company_name(text) and len(text) > 1:
                    companies.add(text)

    card_selectors = [
        "[class*='card']", "[class*='grid']", "[class*='item']",
        "[class*='logo']", "[class*='tile']",
    ]
    for selector in card_selectors:
        for el in soup.select(selector):
            heading = el.find(["h2", "h3", "h4", "h5", "strong", "a"])
            if heading:
                text = heading.get_text(strip=True)
                if is_company_name(text):
                    companies.add(text)

    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        first_line = text.split("\n")[0].strip()
        if is_company_name(first_line) and len(first_line) > 1:
            companies.add(first_line)

    return sorted(companies)


# ---------------------------------------------------------------------------
# PDF Report Generation
# ---------------------------------------------------------------------------

def generate_pdf(holdings, fund_name, report_date, filing_date, total_value,
                 num_positions, theses, output_path):
    """Generate a full PDF report: page 1 = holdings table, pages 2+ = theses (2 per page)."""
    from fpdf import FPDF

    class _ReportPDF(FPDF):
        def __init__(self):
            super().__init__(orientation="L", unit="mm", format="Letter")
            self._fund = fund_name
            self._rdate = report_date
            self._fdate = filing_date
            self._tvb = total_value / 1_000_000_000
            self._npos = num_positions
            self.set_auto_page_break(auto=True, margin=15)

        def header(self):
            self.set_font("Helvetica", "B", 10)
            self.cell(0, 6, f"{self._fund} - 13F Top 20 Holdings Analysis",
                      new_x="LMARGIN", new_y="NEXT", align="C")
            self.set_font("Helvetica", "", 7)
            info = (f"Report Date: {self._rdate}  |  Filing Date: {self._fdate}  |  "
                    f"Total 13F Value: ${self._tvb:.1f}B  |  Positions: {self._npos}")
            self.cell(0, 4, info, new_x="LMARGIN", new_y="NEXT", align="C")
            self.ln(2)

        def footer(self):
            self.set_y(-10)
            self.set_font("Helvetica", "I", 6)
            self.cell(0, 4,
                      f"Page {self.page_no()} | 13F Top Ideas | Data from SEC EDGAR | Not investment advice",
                      align="C")

    pdf = _ReportPDF()
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
    for hdr, width, align in columns:
        pdf.cell(width, 6, hdr, border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)

    # Data rows
    for i, h in enumerate(holdings):
        pdf.set_x(start_x)
        pdf.set_fill_color(245, 245, 245) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
        pdf.set_font("Helvetica", "", 6.5)
        ticker = h["ticker"] if h["ticker"] else h["cusip"][:6]
        row = [
            (str(h["rank"]), 8, "C"), (ticker, 18, "C"), (h["name"][:28], 52, "L"),
            (f"${h['value_m']:,.1f}", 25, "R"), (format_shares(h["shares"]), 25, "R"),
            (f"{h['pct_portfolio']:.1f}%", 16, "R"),
            (h["pct_shares_outstanding"], 16, "R"), (h["pct_3m_adv"], 16, "R"),
        ]
        for text, width, align in row:
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
    pdf.cell(0, 4,
             "Notes: 13F filings have a 45-day delay. Only long equity positions shown. "
             "Market values as of quarter-end. This is analysis, not investment advice.")

    # Thesis pages (2 per page, portrait)
    for i in range(0, len(holdings), 2):
        pdf.add_page(orientation="P")
        for j in range(2):
            if i + j >= len(holdings):
                break
            h = holdings[i + j]
            ticker = h["ticker"] if h["ticker"] else "N/A"
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_fill_color(44, 62, 80)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 8, f"  {h['rank']}. {h['name']} ({ticker})",
                     new_x="LMARGIN", new_y="NEXT", fill=True)
            pdf.set_text_color(0, 0, 0)

            pdf.set_font("Helvetica", "B", 8)
            status = h["delta_q1"]
            if status == "NEW":
                s = "NEW POSITION"
            elif status.startswith("+"):
                s = f"Increased {status}"
            elif status.startswith("-"):
                s = f"Decreased {status}"
            else:
                s = "Unchanged"
            pdf.cell(0, 6,
                     f"Position: ${h['value_m']:,.1f}M  |  {h['pct_portfolio']:.1f}% of Portfolio  |  {s}",
                     new_x="LMARGIN", new_y="NEXT")

            pdf.set_font("Helvetica", "", 8)
            thesis = theses.get(h["cusip"], "Thesis analysis not available.")
            thesis = thesis.encode('latin-1', errors='replace').decode('latin-1')
            pdf.multi_cell(0, 4.5, thesis)
            pdf.ln(5)

    pdf.output(str(output_path))
    return output_path


def generate_wayback_pdf(url, label, yearly_data, start_year, end_year, output_path):
    """Save Wayback tracking results as a PDF with year columns and change log."""
    from fpdf import FPDF

    pdf = FPDF(orientation="L", unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    margin = 10
    page_w = pdf.w - 2 * margin
    page_h = pdf.h
    bottom_limit = page_h - 12
    line_h = 3.8
    header_h = 7

    def _safe(text):
        return text.encode('latin-1', errors='replace').decode('latin-1')

    # Title
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, f"{label} Over Time - Wayback Machine Archive",
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5,
             f"URL: {url}  |  Source: web.archive.org  |  {datetime.now().strftime('%Y-%m-%d')}",
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(3)

    # Column layout
    years = list(range(start_year, end_year + 1))
    num_cols = len(years)
    col_w = page_w / num_cols

    table_top = pdf.get_y()
    usable_h = bottom_limit - table_top - header_h
    items_per_page = int(usable_h / line_h)

    max_items = max((len(v) for v in yearly_data.values() if v), default=0)
    total_pages = max((max_items + items_per_page - 1) // items_per_page, 1)

    for page_num in range(total_pages):
        if page_num > 0:
            pdf.add_page()

        y_start = pdf.get_y() if page_num == 0 else margin + 5

        # Header row
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_fill_color(44, 62, 80)
        pdf.set_text_color(255, 255, 255)
        for i, year in enumerate(years):
            x = margin + i * col_w
            pdf.set_xy(x, y_start)
            items = yearly_data.get(year)
            count = len(items) if items else 0
            hdr = f"12/31/{year} ({count})" if items else f"12/31/{year}"
            pdf.cell(col_w, header_h, hdr, border=1, align="C", fill=True)
        pdf.set_text_color(0, 0, 0)

        body_top = y_start + header_h
        slice_start = page_num * items_per_page
        slice_end = slice_start + items_per_page

        for i, year in enumerate(years):
            x = margin + i * col_w
            items = yearly_data.get(year)
            col_h = items_per_page * line_h
            pdf.rect(x, body_top, col_w, col_h)

            if items is None:
                if page_num == 0:
                    pdf.set_font("Helvetica", "I", 7)
                    pdf.set_xy(x + 2, body_top + 2)
                    pdf.cell(col_w - 4, line_h, "No snapshot")
                continue
            if not items:
                if page_num == 0:
                    pdf.set_font("Helvetica", "I", 7)
                    pdf.set_xy(x + 2, body_top + 2)
                    pdf.cell(col_w - 4, line_h, "None detected")
                continue

            page_items = items[slice_start:slice_end]
            pdf.set_font("Helvetica", "", 6.5)
            for j, item in enumerate(page_items):
                y = body_top + j * line_h
                pdf.set_xy(x + 1.5, y)
                idx = slice_start + j + 1
                display = _safe(item)
                max_chars = int(col_w / 1.6)
                if len(display) > max_chars:
                    display = display[:max_chars - 1] + ".."
                pdf.cell(col_w - 3, line_h, f"{idx}. {display}")

        pdf.set_y(body_top + items_per_page * line_h + 4)

    # Year-over-year changes
    if pdf.get_y() + 20 > bottom_limit:
        pdf.add_page()

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Year-over-Year Changes", new_x="LMARGIN", new_y="NEXT")

    prev_year = None
    prev_items = None
    for year in range(start_year, end_year + 1):
        items = yearly_data.get(year)
        if items is None:
            prev_year = year
            prev_items = None
            continue
        if prev_items is not None:
            current_set = set(items)
            prev_set = set(prev_items)
            added = sorted(current_set - prev_set)
            removed = sorted(prev_set - current_set)
            if pdf.get_y() + 7 > bottom_limit:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(0, 5, f"{prev_year} -> {year}:", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 7)
            if added or removed:
                for a in added:
                    if pdf.get_y() + 4 > bottom_limit:
                        pdf.add_page()
                    pdf.set_text_color(39, 174, 96)
                    pdf.cell(0, 3.5, f"    + Joined: {_safe(a)}", new_x="LMARGIN", new_y="NEXT")
                for r in removed:
                    if pdf.get_y() + 4 > bottom_limit:
                        pdf.add_page()
                    pdf.set_text_color(192, 57, 43)
                    pdf.cell(0, 3.5, f"    - Left: {_safe(r)}", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
            else:
                pdf.cell(0, 4, "    No changes detected", new_x="LMARGIN", new_y="NEXT")
        prev_year = year
        prev_items = items

    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.cell(0, 4,
             "Data sourced exclusively from: web.archive.org (Wayback Machine). "
             "Extraction is heuristic-based.",
             new_x="LMARGIN", new_y="NEXT")

    pdf.output(str(output_path))
    return output_path


def track_website_changes(url, mode="team", start_year=None, end_year=None,
                          progress_callback=None):
    """Track team/portfolio changes over time using Wayback Machine.

    If url is just a domain (no path), auto-discovers the best team/portfolio
    page by trying candidate URLs until one yields results.

    Returns:
        dict with yearly_data, changes, url (the resolved URL used)
    """
    now = datetime.now()
    if start_year is None:
        start_year = now.year - 5
    if end_year is None:
        end_year = now.year

    extract_fn = extract_team_members if mode == "team" else extract_companies

    if not url.startswith("http"):
        url = "https://" + url

    def _validate_items(items, check_mode):
        """Sanity-check extracted items to make sure they match the expected mode."""
        if not items:
            return False
        if check_mode == "team":
            # At least 30% of items should look like person names
            person_count = sum(1 for item in items if is_person_name(item))
            return person_count >= max(2, len(items) * 0.3)
        return True  # Portfolio mode — accept any non-empty result

    def _try_extract(snapshot_ts, page_url):
        """Try extracting from raw HTML, then replay; return items or empty list."""
        html = fetch_snapshot_html(snapshot_ts, page_url)
        if html:
            items = extract_fn(html)
            if items and _validate_items(items, mode):
                return items
        # Try Wayback replay version (renders some JS)
        replay_html = fetch_snapshot_html_replay(snapshot_ts, page_url)
        if replay_html:
            items = extract_fn(replay_html)
            if items and _validate_items(items, mode):
                return items
        return []

    # Auto-discover if user gave just a domain (no meaningful path)
    parsed = urlparse(url)
    homepage_url = url  # Remember original for fallback
    if not parsed.path.strip("/"):
        if progress_callback:
            progress_callback("discover", f"Finding {mode} pages on {parsed.netloc}...")
        candidates = discover_pages(url, mode=mode)
        found_subpage = False
        if candidates:
            # Try each candidate until we find one with snapshots and valid data
            for candidate_url in candidates[:8]:
                if progress_callback:
                    progress_callback("try", f"Trying {candidate_url}...")
                test_snaps = get_snapshots(candidate_url, start_year, end_year)
                if not test_snaps:
                    continue
                latest = test_snaps[-1]
                test_items = _try_extract(latest["timestamp"], candidate_url)
                if test_items:
                    url = candidate_url
                    found_subpage = True
                    if progress_callback:
                        progress_callback("found",
                            f"Found {len(test_items)} {mode} entries on {candidate_url}")
                    break

        # Fallback: if no subpage worked, try the homepage itself
        if not found_subpage:
            if progress_callback:
                progress_callback("try", f"No subpage found, trying homepage {homepage_url}...")
            test_snaps = get_snapshots(homepage_url, start_year, end_year)
            if test_snaps:
                latest = test_snaps[-1]
                test_items = _try_extract(latest["timestamp"], homepage_url)
                if test_items:
                    url = homepage_url
                    if progress_callback:
                        progress_callback("found",
                            f"Found {len(test_items)} {mode} entries on homepage")
                elif candidates:
                    url = candidates[0]
                    if progress_callback:
                        progress_callback("found",
                            f"Using best candidate: {url}")
            elif candidates:
                url = candidates[0]

    snapshots = get_snapshots(url, start_year, end_year)
    if not snapshots:
        return {"error": f"No Wayback Machine snapshots found for {url}", "yearly_data": {}, "changes": []}

    if progress_callback:
        progress_callback("snapshots", f"Found {len(snapshots)} snapshots for {url}")

    yearly_data = {}
    for year in range(start_year, end_year + 1):
        target_date = f"{year}1231"
        snap = find_closest_snapshot(snapshots, target_date)

        if not snap:
            yearly_data[year] = None
            continue

        if progress_callback:
            snap_date = snap["timestamp"][:8]
            progress_callback("fetch", f"Fetching {year} snapshot ({snap_date})...")

        html = fetch_snapshot_html(snap["timestamp"], url)
        if not html:
            yearly_data[year] = None
            continue

        items = extract_fn(html)

        if not items:
            replay_html = fetch_snapshot_html_replay(snap["timestamp"], url)
            if replay_html:
                items = extract_fn(replay_html)

        yearly_data[year] = items
        if progress_callback:
            count = len(items) if items else 0
            progress_callback("extracted", f"{year}: found {count} entries")

    # Compute changes
    changes = []
    prev_year = None
    prev_items = None
    for year in range(start_year, end_year + 1):
        items = yearly_data.get(year)
        if items is None:
            prev_year = year
            prev_items = None
            continue

        if prev_items is not None:
            current_set = set(items)
            prev_set = set(prev_items)
            added = sorted(current_set - prev_set)
            removed = sorted(prev_set - current_set)
            changes.append({
                "from_year": prev_year,
                "to_year": year,
                "added": added,
                "removed": removed,
            })

        prev_year = year
        prev_items = items

    return {
        "url": url,
        "mode": mode,
        "period": f"{start_year}-{end_year}",
        "num_snapshots": len(snapshots),
        "yearly_data": yearly_data,
        "changes": changes,
    }

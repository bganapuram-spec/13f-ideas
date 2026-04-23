#!/usr/bin/env python3
"""
Wayback Machine Scraper - Track team and portfolio changes on fund websites.

Uses the Wayback Machine (web.archive.org) to fetch historical snapshots of
investment manager websites and track team member / portfolio company changes.

Usage:
    python wayback_scraper.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from fpdf import FPDF

# ---------------------------------------------------------------------------
# Terminal colors
# ---------------------------------------------------------------------------

class C:
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

def banner():
    print(f"""
{C.BOLD}{C.CYAN}{'='*65}
   Wayback Machine Scraper - Fund Website Change Tracker
   Source: web.archive.org (Wayback Machine archive ONLY)
{'='*65}{C.RESET}
{C.DIM}  Track team members & portfolio companies over time
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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_WEB_URL = "https://web.archive.org/web"
REQUEST_DELAY = 1.5  # Be nice to Wayback Machine
REQUEST_TIMEOUT = 60  # Wayback Machine can be slow
MAX_RETRIES = 3

session = requests.Session()
session.headers.update({
    "User-Agent": "13FTopIdeas-WaybackScraper/1.0 (research@13ftopideas.com)",
})


def _get_with_retries(url, params=None, timeout=REQUEST_TIMEOUT):
    """GET request with retry logic for flaky Wayback Machine responses."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = attempt * 5
                print_warn(f"Attempt {attempt}/{MAX_RETRIES} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

# ---------------------------------------------------------------------------
# Wayback Machine API
# ---------------------------------------------------------------------------

def get_snapshots(url, from_year=None, to_year=None):
    """Fetch available snapshot timestamps for a URL from the CDX API."""
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode",
        "filter": "statuscode:200",
        "collapse": "timestamp:8",  # One per day
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
        # First row is headers, rest are data
        return [{"timestamp": row[0], "url": row[1], "status": row[2]} for row in data[1:]]
    except Exception as e:
        print_error(f"CDX API error: {e}")
        return []


def find_closest_snapshot(snapshots, target_date):
    """Find the snapshot closest to a target date (YYYYMMDD string).

    Prefers snapshots within 90 days before/after the target date.
    """
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

    # Only accept if within ~90 days
    if best and best_diff <= 900000:  # roughly 90 days in YYYYMMDD diff
        return best
    return None


def fetch_snapshot_html(timestamp, url):
    """Fetch the HTML content of a Wayback Machine snapshot.

    Tries the raw (id_) version first, then falls back to the replayed version
    which may include JS-rendered content via Wayback's replay engine.
    """
    snapshot_url = f"{WAYBACK_WEB_URL}/{timestamp}id_/{url}"
    try:
        resp = _get_with_retries(snapshot_url)
        return resp.text
    except Exception as e:
        print_error(f"Failed to fetch snapshot {timestamp}: {e}")
        return None


def fetch_snapshot_html_replay(timestamp, url):
    """Fetch the replayed (non-id_) version of a snapshot.

    This version goes through Wayback's replay engine which can render
    some JS content. Useful as a fallback for JS-heavy sites.
    """
    snapshot_url = f"{WAYBACK_WEB_URL}/{timestamp}/{url}"
    try:
        resp = _get_with_retries(snapshot_url)
        return resp.text
    except Exception as e:
        return None


# ---------------------------------------------------------------------------
# Page discovery — find team / portfolio pages from the site itself
# ---------------------------------------------------------------------------

# Keywords in URLs or link text that suggest team pages
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

# Keywords in URLs or link text that suggest portfolio pages
PORTFOLIO_URL_KEYWORDS = [
    "portfolio", "companies", "investments", "our-companies",
    "holdings", "ventures", "startups", "brands", "fund",
]


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
            return -10

    # Penalize deep paths (likely sub-sub-pages, individual bios, blog posts)
    if len(segments) > 3:
        return -5

    if mode == "team":
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

    return 5


def discover_pages(base_url, mode="team"):
    """Discover team or portfolio pages by crawling the site's homepage via Wayback.

    Instead of requiring an exact URL like /our-team, this fetches the homepage
    (or a recent Wayback snapshot of it) and finds links that look like team or
    portfolio pages. Results are ranked by relevance and filtered to exclude
    wrong-mode pages (e.g., /portfolio excluded when looking for team).

    Args:
        base_url: Domain or homepage URL (e.g. 'https://sequoiacap.com' or just 'sequoiacap.com')
        mode: 'team' or 'portfolio'

    Returns:
        list of candidate URLs sorted by relevance
    """
    keywords = TEAM_URL_KEYWORDS if mode == "team" else PORTFOLIO_URL_KEYWORDS

    # Normalize the URL to the homepage
    if not base_url.startswith("http"):
        base_url = "https://" + base_url
    parsed = urlparse(base_url)
    homepage = f"{parsed.scheme}://{parsed.netloc}/"

    print_info(f"Discovering {mode} pages on {parsed.netloc} via Wayback Machine...")

    # First, use CDX API to find all unique pages archived under this domain
    print_info("Searching Wayback CDX index for relevant pages...")
    cdx_candidates = _discover_via_cdx(parsed.netloc, keywords)

    # Also try crawling the homepage for links
    print_info("Crawling homepage snapshot for links...")
    crawl_candidates = _discover_via_homepage(homepage, keywords)

    # Merge and deduplicate
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
    """Use the CDX API to find pages under the domain matching keywords."""
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
                    # Skip assets, images, etc.
                    if any(url.lower().endswith(ext) for ext in
                           ['.jpg', '.png', '.gif', '.css', '.js', '.svg',
                            '.pdf', '.ico', '.woff', '.woff2', '.ttf']):
                        continue
                    candidates.append(url)
        except Exception:
            continue

    return candidates


def _discover_via_homepage(homepage, keywords):
    """Fetch a recent homepage snapshot and extract relevant links."""
    candidates = []

    # Get the most recent snapshot of the homepage
    snapshots = get_snapshots(homepage)
    if not snapshots:
        return candidates

    # Use the most recent snapshot
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

        # Resolve relative URLs
        if href.startswith("/"):
            href = f"{parsed_home.scheme}://{parsed_home.netloc}{href}"
        elif not href.startswith("http"):
            continue

        # Must be same domain
        link_parsed = urlparse(href)
        if link_parsed.netloc and link_parsed.netloc != parsed_home.netloc:
            continue

        # Skip asset files
        if any(href.lower().endswith(ext) for ext in
               ['.jpg', '.png', '.gif', '.css', '.js', '.svg', '.pdf']):
            continue

        # Check if URL path matches keywords
        path_lower = link_parsed.path.lower()
        for kw in keywords:
            if kw in path_lower:
                candidates.append(href)
                break
        else:
            # Check link text against broader text keywords
            for kw in text_keywords:
                if kw in text:
                    candidates.append(href)
                    break

    return candidates


def prompt_page_selection(candidates, mode="team"):
    """Show discovered pages and let user pick one, or type a custom URL."""
    label = "team" if mode == "team" else "portfolio"
    if not candidates:
        print_warn(f"No {label} pages discovered automatically.")
        print_info(f"You can still enter a URL directly: {mode} <url>")
        return None

    print(f"\n  {C.BOLD}Discovered {label} page candidates:{C.RESET}")
    for i, url in enumerate(candidates[:15], 1):
        print(f"    {C.CYAN}{i:>2}.{C.RESET} {url}")
    if len(candidates) > 15:
        print(f"    {C.DIM}... and {len(candidates) - 15} more{C.RESET}")

    print(f"\n  Enter a number to select, or type a custom URL.")
    print(f"  Press Enter to use #{C.BOLD}1{C.RESET}, or 'skip' to cancel.")

    try:
        choice = input(f"  {C.BOLD}>{C.RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not choice:
        return candidates[0]
    if choice.lower() == "skip":
        return None
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
        print_warn("Invalid selection.")
        return None
    # Treat as custom URL
    if not choice.startswith("http"):
        choice = "https://" + choice
    return choice


# ---------------------------------------------------------------------------
# Name extraction heuristics
# ---------------------------------------------------------------------------

def clean_name_text(text):
    """Clean extracted text that may have concatenated words (e.g., 'Anas BiadGrowth')."""
    text = text.strip()
    # Split camelCase / concatenated words: "BiadGrowth" -> "Biad Growth"
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return text.strip()


def is_person_name(text):
    """Heuristic: check if text looks like a person's name."""
    text = text.strip()
    if not text or len(text) < 3 or len(text) > 40:
        return False
    # Must have at least 2 words
    words = text.split()
    if len(words) < 2 or len(words) > 4:
        return False
    # Each word should start with uppercase
    for w in words:
        if not w[0].isupper():
            return False
    # All words should be short-ish (actual name parts, not titles/descriptions)
    if any(len(w) > 15 for w in words):
        return False
    # Should not contain typical non-name tokens
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
        # Common nav / section labels that look like 2-3 word names
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
    # Should be ONLY alphabetic characters and spaces (names don't have digits/symbols)
    if not re.match(r"^[A-Za-z' \-\.]+$", text):
        return False
    # Each word should look like a name part (not a title or descriptor)
    # Name parts are typically 2-12 chars
    for w in words:
        clean_w = w.strip(".-'")
        if len(clean_w) < 2:
            return False
    return True


def is_company_name(text):
    """Heuristic: check if text looks like a company name."""
    text = text.strip()
    if not text or len(text) < 2 or len(text) > 80:
        return False
    words = text.split()
    if len(words) > 10:
        return False
    # Skip navigation / boilerplate
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
    # Should be mostly alphabetic/numeric
    alpha_chars = sum(1 for c in text if c.isalnum() or c in ' .-&')
    if len(text) > 0 and alpha_chars / len(text) < 0.7:
        return False
    return True


def _get_direct_text(element):
    """Get only the direct text of an element, not its children's text.
    Falls back to get_text if direct text is empty but has a single child."""
    # Get direct (non-child) text
    direct = element.find(string=True, recursive=False)
    if direct and direct.strip():
        return direct.strip()
    # If element has exactly one child, use that child's text
    children = list(element.children)
    text_children = [c for c in children if hasattr(c, 'get_text') or (isinstance(c, str) and c.strip())]
    if len(text_children) == 1:
        child = text_children[0]
        if isinstance(child, str):
            return child.strip()
        return child.get_text(strip=True)
    return element.get_text(separator=" ", strip=True)


def _extract_names_from_json(data, names_set):
    """Recursively walk JSON data looking for person name fields.

    Many modern sites (Next.js, Gatsby, etc.) embed page data as JSON in
    script tags. This walks through looking for common name-related keys.
    """
    name_keys = {"name", "fullName", "full_name", "displayName", "display_name",
                 "personName", "person_name", "teamMember", "team_member",
                 "authorName", "author_name", "author", "member", "person",
                 "firstName", "first_name", "title"}
    first_keys = {"firstName", "first_name", "first"}
    last_keys = {"lastName", "last_name", "last"}

    if isinstance(data, dict):
        # Check for firstName + lastName pattern
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

        # Check for single name field
        for k, v in data.items():
            if k in name_keys and isinstance(v, str) and v.strip():
                candidate = clean_name_text(v.strip())
                if is_person_name(candidate):
                    names_set.add(candidate)
            # Recurse into values
            if isinstance(v, (dict, list)):
                _extract_names_from_json(v, names_set)

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                _extract_names_from_json(item, names_set)


def _extract_names_from_scripts(html):
    """Extract person names from embedded JSON in script tags.

    Handles: __NEXT_DATA__, inline JSON-LD, and other embedded data.
    """
    soup = BeautifulSoup(html, "html.parser")
    names = set()

    for script in soup.find_all("script"):
        text = script.string
        if not text:
            continue

        # Try __NEXT_DATA__ (Next.js)
        if script.get("id") == "__NEXT_DATA__":
            try:
                data = json.loads(text)
                _extract_names_from_json(data, names)
            except (json.JSONDecodeError, ValueError):
                pass
            continue

        # Try JSON-LD
        if script.get("type") == "application/ld+json":
            try:
                data = json.loads(text)
                _extract_names_from_json(data, names)
            except (json.JSONDecodeError, ValueError):
                pass
            continue

        # Try to find embedded JSON objects/arrays in other scripts
        # Look for large JSON blobs (common in SSR/hydration data)
        for match in re.finditer(r'(?:JSON\.parse\(|=\s*)(\{["\'](?:name|team|member|person|people|staff|firstName).*?\})', text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                _extract_names_from_json(data, names)
            except (json.JSONDecodeError, ValueError):
                pass

        # Also look for arrays of objects with name fields
        for match in re.finditer(r'(\[\s*\{[^]]{20,}\}\s*\])', text):
            try:
                data = json.loads(match.group(1))
                _extract_names_from_json(data, names)
            except (json.JSONDecodeError, ValueError):
                pass

    return names


def extract_team_members(html):
    """Extract person names from a team page HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # First, try extracting from embedded JSON/script data (JS-rendered sites)
    names = _extract_names_from_scripts(html)

    # Remove script, style, nav, footer, header elements for DOM extraction
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    def try_add_name(raw_text):
        """Clean text and add to names if it looks like a person name."""
        text = clean_name_text(raw_text)
        # Also try just the first two or three words (strip trailing title text)
        candidates = [text]
        words = text.split()
        if len(words) > 2:
            candidates.append(" ".join(words[:2]))
            candidates.append(" ".join(words[:3]))
        for candidate in candidates:
            if is_person_name(candidate):
                names.add(candidate)
                return

    # Strategy 1: Look for elements with team/member/person-related classes
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
            # Also check direct spans/links with name-like classes
            for name_el in el.find_all(["span", "a"], class_=lambda c: c and any(
                    kw in (c if isinstance(c, str) else " ".join(c)).lower()
                    for kw in ["name", "title", "person"])):
                text = _get_direct_text(name_el)
                try_add_name(text)

    # Strategy 2: Look for h2/h3/h4 that look like names (use separator to avoid concatenation)
    for tag in soup.find_all(["h2", "h3", "h4"]):
        text = tag.get_text(separator=" ", strip=True)
        try_add_name(text)

    # Strategy 3: Look for list items — take first line only
    for li in soup.find_all("li"):
        text = li.get_text(separator=" ", strip=True)
        first_line = text.split("\n")[0].strip()
        try_add_name(first_line)

    return sorted(names)


def _extract_companies_from_json(data, companies_set):
    """Recursively walk JSON data looking for company name fields."""
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
    """Extract company names from embedded JSON in script tags."""
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
    """Extract company names from a portfolio page HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # First, try extracting from embedded JSON/script data (JS-rendered sites)
    companies = _extract_companies_from_scripts(html)

    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    # Strategy 1: Elements with portfolio/company-related classes
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

    # Strategy 2: Look for grid/card-like structures
    card_selectors = [
        "[class*='card']", "[class*='grid']", "[class*='item']",
        "[class*='logo']", "[class*='tile']",
    ]
    for selector in card_selectors:
        for el in soup.select(selector):
            # Get the heading or first text
            heading = el.find(["h2", "h3", "h4", "h5", "strong", "a"])
            if heading:
                text = heading.get_text(strip=True)
                if is_company_name(text):
                    companies.add(text)

    # Strategy 3: List items
    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        first_line = text.split("\n")[0].strip()
        if is_company_name(first_line) and len(first_line) > 1:
            companies.add(first_line)

    return sorted(companies)


# ---------------------------------------------------------------------------
# Main tracking functions
# ---------------------------------------------------------------------------

def track_changes(url, extract_fn, label, start_year, end_year):
    """Track changes on a URL over time using Wayback Machine.

    Args:
        url: The URL to track
        extract_fn: Function to extract items from HTML (extract_team_members or extract_companies)
        label: "Team Members" or "Portfolio Companies"
        start_year: First year to check
        end_year: Last year to check
    """
    print_header(f"Tracking {label} changes on: {url}")
    print_info(f"Period: {start_year} - {end_year}")
    print_info(f"Source: Wayback Machine archive (web.archive.org)")
    print()

    # Fetch all available snapshots
    print_info("Fetching snapshot index from Wayback Machine CDX API...")
    snapshots = get_snapshots(url, start_year, end_year)
    if not snapshots:
        print_error(f"No Wayback Machine snapshots found for {url}")
        return

    print_success(f"Found {len(snapshots)} snapshots between {start_year}-{end_year}")

    # For each year-end, find the closest snapshot and extract data
    yearly_data = {}
    for year in range(start_year, end_year + 1):
        target_date = f"{year}1231"
        snap = find_closest_snapshot(snapshots, target_date)

        if not snap:
            print_warn(f"  12/31/{year}: No snapshot available within 90 days")
            yearly_data[year] = None
            continue

        snap_date = snap["timestamp"][:8]
        formatted_date = f"{snap_date[:4]}-{snap_date[4:6]}-{snap_date[6:8]}"
        print_info(f"  12/31/{year}: Using snapshot from {formatted_date} (closest available)")

        html = fetch_snapshot_html(snap["timestamp"], url)
        if not html:
            yearly_data[year] = None
            continue

        items = extract_fn(html)

        # If raw HTML yielded nothing, try Wayback's replay version (renders some JS)
        if not items:
            print_info(f"  12/31/{year}: Raw HTML empty, trying Wayback replay...")
            replay_html = fetch_snapshot_html_replay(snap["timestamp"], url)
            if replay_html:
                items = extract_fn(replay_html)

        yearly_data[year] = items
        print_success(f"  12/31/{year}: Found {len(items)} {label.lower()}")

    # Display results table — every item on its own line, no truncation
    print(f"\n{C.BOLD}{'='*70}{C.RESET}")
    print(f"{C.BOLD}  {label} Over Time - Source: Wayback Machine Archive{C.RESET}")
    print(f"{C.BOLD}{'='*70}{C.RESET}\n")

    for year in range(start_year, end_year + 1):
        items = yearly_data.get(year)
        print(f"  {C.BOLD}{C.UNDERLINE}12/31/{year}{C.RESET}", end="")
        if items is None:
            print(f"  {C.DIM}— No snapshot available{C.RESET}")
        elif not items:
            print(f"  {C.DIM}— No {label.lower()} detected on page{C.RESET}")
        else:
            print(f"  {C.DIM}({len(items)} found){C.RESET}")
            for i, item in enumerate(items, 1):
                print(f"    {C.DIM}{i:>3}.{C.RESET} {item}")
        print()

    # Show changes year-over-year
    print(f"\n{C.BOLD}Changes:{C.RESET}")
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

            if added or removed:
                print(f"\n  {C.BOLD}{prev_year} -> {year}:{C.RESET}")
                if added:
                    for a in added:
                        print(f"    {C.GREEN}+ {a}{C.RESET}")
                if removed:
                    for r in removed:
                        print(f"    {C.RED}- {r}{C.RESET}")
            else:
                print(f"  {prev_year} -> {year}: {C.DIM}No changes detected{C.RESET}")

        prev_year = year
        prev_items = items

    print(f"\n{C.DIM}  Data sourced exclusively from: web.archive.org (Wayback Machine){C.RESET}")
    print(f"{C.DIM}  Snapshots may not be available for all dates. Extraction is heuristic-based.{C.RESET}")

    # Auto-save results as PDF table to Desktop
    pdf_path = save_results_pdf(url, label, yearly_data, start_year, end_year)
    if pdf_path:
        print_success(f"Results saved to: {pdf_path}")
        if sys.platform == "darwin":
            os.system(f'open "{pdf_path}"')


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

def save_results_pdf(url, label, yearly_data, start_year, end_year):
    """Save the tracking results as a PDF with a side-by-side year column table.

    All years shown as columns. Names listed vertically in each column.
    If a column has too many items for one page, it continues on the next page
    while short columns just show empty space.
    """
    try:
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

        # ---- Title ----
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, f"{label} Over Time - Wayback Machine Archive",
                 new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, f"URL: {url}  |  Source: web.archive.org  |  {datetime.now().strftime('%Y-%m-%d')}",
                 new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(3)

        # ---- Column layout ----
        years = [y for y in range(start_year, end_year + 1)]
        num_cols = len(years)
        col_w = page_w / num_cols

        # Figure out how many items fit per page in one column
        table_top = pdf.get_y()
        usable_h = bottom_limit - table_top - header_h
        items_per_page = int(usable_h / line_h)

        # Determine max items across all years to know total pages needed
        max_items = max((len(v) for v in yearly_data.values() if v), default=0)
        total_pages = max((max_items + items_per_page - 1) // items_per_page, 1)

        for page_num in range(total_pages):
            if page_num > 0:
                pdf.add_page()

            y_start = pdf.get_y() if page_num == 0 else margin + 5

            # Draw header row
            pdf.set_xy(margin, y_start)
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

            # Draw column borders and items
            for i, year in enumerate(years):
                x = margin + i * col_w
                items = yearly_data.get(year)

                if items is None:
                    # No snapshot — only draw on first page
                    if page_num == 0:
                        col_h = items_per_page * line_h
                        pdf.rect(x, body_top, col_w, col_h)
                        pdf.set_font("Helvetica", "I", 7)
                        pdf.set_xy(x + 2, body_top + 2)
                        pdf.cell(col_w - 4, line_h, "No snapshot")
                    else:
                        col_h = items_per_page * line_h
                        pdf.rect(x, body_top, col_w, col_h)
                    continue

                if not items:
                    if page_num == 0:
                        col_h = items_per_page * line_h
                        pdf.rect(x, body_top, col_w, col_h)
                        pdf.set_font("Helvetica", "I", 7)
                        pdf.set_xy(x + 2, body_top + 2)
                        pdf.cell(col_w - 4, line_h, "None detected")
                    else:
                        col_h = items_per_page * line_h
                        pdf.rect(x, body_top, col_w, col_h)
                    continue

                page_items = items[slice_start:slice_end]
                # Draw column border for full height
                col_h = items_per_page * line_h
                pdf.rect(x, body_top, col_w, col_h)

                pdf.set_font("Helvetica", "", 6.5)
                for j, item in enumerate(page_items):
                    y = body_top + j * line_h
                    pdf.set_xy(x + 1.5, y)
                    idx = slice_start + j + 1
                    display = _safe(item)
                    # Truncate to fit column but keep it readable
                    max_chars = int(col_w / 1.6)
                    if len(display) > max_chars:
                        display = display[:max_chars - 1] + ".."
                    pdf.cell(col_w - 3, line_h, f"{idx}. {display}")

            # If this is the last page of the table, move cursor past it
            pdf.set_y(body_top + col_h + 4)

        # ---- Year-over-year Changes ----
        # Check if we need a new page for changes section
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

        # ---- Footer ----
        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 6.5)
        pdf.cell(0, 4,
                 "Data sourced exclusively from: web.archive.org (Wayback Machine). Extraction is heuristic-based.",
                 new_x="LMARGIN", new_y="NEXT")

        # Save
        domain = urlparse(url).netloc.replace(".", "_")
        kind = "team" if "Team" in label else "portfolio"
        filename = f"Wayback_{domain}_{kind}_{datetime.now().strftime('%Y%m%d')}.pdf"
        out_path = Path.home() / "Desktop" / filename
        pdf.output(str(out_path))
        return out_path

    except Exception as e:
        print_error(f"Failed to save PDF: {e}")
        return None


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------

current_start_year = datetime.now().year - 5
current_end_year = datetime.now().year

def show_help():
    print(f"""
{C.BOLD}Available Commands:{C.RESET}
  {C.CYAN}team <domain or url>{C.RESET}       Track team member changes
  {C.CYAN}portfolio <domain or url>{C.RESET}  Track portfolio company changes
  {C.CYAN}discover <domain>{C.RESET}          Find team/portfolio pages on a domain
  {C.CYAN}years <start> <end>{C.RESET}        Set year range (current: {current_start_year}-{current_end_year})
  {C.CYAN}help{C.RESET}                       Show this help
  {C.CYAN}quit{C.RESET}                       Exit

{C.BOLD}Examples:{C.RESET}
  {C.DIM}team sequoiacap.com{C.RESET}                          Auto-discovers team page
  {C.DIM}team https://sequoiacap.com/our-team/{C.RESET}        Uses exact URL
  {C.DIM}portfolio a16z.com{C.RESET}                           Auto-discovers portfolio page
  {C.DIM}discover vikingglobal.com{C.RESET}                    Lists all discoverable pages
  {C.DIM}years 2020 2025{C.RESET}

{C.BOLD}Notes:{C.RESET}
  - All data comes from the Wayback Machine (web.archive.org)
  - If you give just a domain, it auto-discovers the right page to scrape
  - Name/company extraction uses heuristics and may not be perfect
  - Be patient - each year requires fetching an archived page
""")


def _resolve_url(user_url, mode="team"):
    """Resolve a user-provided URL or domain to an actual page URL.

    If the input looks like a full URL with a path (e.g., has /team or /about),
    use it directly. If it's just a domain, auto-discover the right page.
    Falls back to the homepage itself if no subpage yields data.
    """
    if not user_url.startswith("http"):
        user_url = "https://" + user_url

    parsed = urlparse(user_url)
    path = parsed.path.strip("/")

    # If there's a meaningful path beyond just the root, use it directly
    if path:
        return user_url

    # Just a domain — auto-discover
    candidates = discover_pages(user_url, mode=mode)
    selected = prompt_page_selection(candidates, mode=mode)

    # If no subpage was selected/found, fall back to the homepage
    if not selected:
        homepage = f"{parsed.scheme}://{parsed.netloc}/"
        print_info(f"No subpage found — will try the homepage itself: {homepage}")
        return homepage

    return selected


def interactive_loop():
    global current_start_year, current_end_year
    banner()

    while True:
        try:
            prompt_text = f"\n{C.BOLD}{C.CYAN}wayback>{C.RESET} "
            user_input = input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C.DIM}Goodbye!{C.RESET}")
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        if cmd in ("quit", "exit", "q"):
            print(f"{C.DIM}Goodbye!{C.RESET}")
            break

        elif cmd == "help":
            show_help()

        elif cmd.startswith("years"):
            parts = cmd.split()
            if len(parts) != 3:
                print_warn("Usage: years <start> <end>  (e.g., 'years 2020 2025')")
                continue
            try:
                s, e = int(parts[1]), int(parts[2])
                if s > e:
                    print_warn("Start year must be <= end year.")
                    continue
                if s < 1996:
                    print_warn("Wayback Machine started in 1996.")
                    continue
                current_start_year = s
                current_end_year = e
                print_success(f"Year range set to {s}-{e}")
            except ValueError:
                print_warn("Invalid years. Use: years 2020 2025")

        elif cmd.startswith("team "):
            url = user_input[5:].strip()
            url = _resolve_url(url, mode="team")
            if url:
                track_changes(url, extract_team_members, "Team Members",
                             current_start_year, current_end_year)

        elif cmd.startswith("portfolio "):
            url = user_input[10:].strip()
            url = _resolve_url(url, mode="portfolio")
            if url:
                track_changes(url, extract_companies, "Portfolio Companies",
                             current_start_year, current_end_year)

        elif cmd.startswith("discover "):
            domain = user_input[9:].strip()
            if not domain.startswith("http"):
                domain = "https://" + domain
            print_header(f"Discovering pages on {domain}")
            team_pages = discover_pages(domain, mode="team")
            portfolio_pages = discover_pages(domain, mode="portfolio")
            if team_pages:
                print(f"\n  {C.BOLD}Team pages:{C.RESET}")
                for i, p in enumerate(team_pages[:10], 1):
                    print(f"    {i:>2}. {p}")
            else:
                print_warn("No team pages found.")
            if portfolio_pages:
                print(f"\n  {C.BOLD}Portfolio pages:{C.RESET}")
                for i, p in enumerate(portfolio_pages[:10], 1):
                    print(f"    {i:>2}. {p}")
            else:
                print_warn("No portfolio pages found.")
            if not team_pages and not portfolio_pages:
                print_info("Try using a direct URL instead: team <full-url>")

        else:
            print_warn(f"Unknown command: '{user_input}'. Type 'help' for available commands.")


if __name__ == "__main__":
    interactive_loop()

#!/usr/bin/env python3
"""
MCP Server for 13F SEC Filing Analyzer.

Exposes data-fetching tools via the Model Context Protocol.

REQUIRES Python 3.10+. Install with:
    pip install "mcp[cli]"

Usage:
    python mcp_server.py

For Python 3.9 users: The bot_app.py works without this file.
It imports data_tools.py directly.
"""

import sys

if sys.version_info < (3, 10):
    print("ERROR: MCP server requires Python 3.10+")
    print(f"You are running Python {sys.version_info.major}.{sys.version_info.minor}")
    print("")
    print("Options:")
    print("  1. Install Python 3.10+: brew install python@3.12")
    print("  2. Use the Streamlit bot directly: streamlit run bot_app.py")
    print("     (The bot works without the MCP server)")
    sys.exit(1)

from mcp.server.fastmcp import FastMCP
import data_tools as dt

mcp = FastMCP("13F SEC Filing Analyzer")


@mcp.tool()
def list_available_funds() -> dict:
    """List all pre-configured fund shorthand names for quick lookups.
    Returns aliases like 'viking', 'pershing', 'berkshire' mapped to full names."""
    return {"aliases": dt.FUND_ALIASES}


@mcp.tool()
def search_fund(fund_name: str) -> dict:
    """Search for a hedge fund by name and return its SEC CIK number.
    Accepts shorthand like 'viking', 'pershing', 'berkshire' or full names."""
    session = dt.make_session()
    resolved = dt.resolve_fund_name(fund_name)
    cik, display_name = dt.lookup_cik(session, resolved)
    if not cik:
        return {"error": f"Could not find fund '{fund_name}'. Try a different name."}
    return {
        "cik": cik,
        "display_name": display_name,
        "resolved_query": resolved,
    }


@mcp.tool()
def get_fund_holdings(fund_name: str, num_quarters: int = 4, num_top: int = 20) -> dict:
    """Fetch a fund's 13F holdings from SEC EDGAR.
    Returns top N holdings with quarter-over-quarter share changes, tickers, and market data.
    This is the main tool for analyzing what a hedge fund is investing in."""
    data = dt.fetch_fund_data(fund_name)
    if not data:
        return {"error": f"Could not fetch data for '{fund_name}'."}

    holdings_list = []
    for h in data["top_holdings"][:num_top]:
        holdings_list.append({
            "rank": h["rank"],
            "name": h["name"],
            "ticker": h["ticker"],
            "cusip": h["cusip"],
            "value_millions": round(h["value_m"], 1),
            "shares": h["shares"],
            "pct_portfolio": round(h["pct_portfolio"], 1),
            "pct_shares_outstanding": h["pct_shares_outstanding"],
            "avg_daily_volume_days": h["pct_3m_adv"],
            "change_q1": h["delta_q1"],
            "change_q2": h["delta_q2"],
            "change_q3": h["delta_q3"],
        })

    return {
        "fund_name": data["fund_name"],
        "cik": data["cik"],
        "total_value_millions": round(data["total_value"] / 1_000_000, 1),
        "num_positions": data["num_positions"],
        "filing_date": data["filings"][0]["filing_date"],
        "report_date": data["filings"][0]["report_date"],
        "top_holdings": holdings_list,
    }


@mcp.tool()
def get_holding_detail(fund_name: str, rank: int) -> dict:
    """Get detailed information for a specific holding by its rank number."""
    data = dt.fetch_fund_data(fund_name)
    if not data:
        return {"error": f"Could not fetch data for '{fund_name}'."}

    matches = [h for h in data["top_holdings"] if h["rank"] == rank]
    if not matches:
        return {"error": f"Position #{rank} not found."}

    h = matches[0]
    share_history = []
    cusip = h["cusip"]
    for qi, qd in enumerate(data["quarters_data"]):
        label = "Current" if qi == 0 else f"Q-{qi}"
        if qd and cusip in qd:
            share_history.append({
                "quarter": label,
                "shares": qd[cusip]["shares"],
                "value_millions": round(qd[cusip]["value"] / 1_000_000, 1),
            })
        else:
            share_history.append({"quarter": label, "shares": None, "value_millions": None})

    return {
        "fund_name": data["fund_name"],
        "rank": h["rank"],
        "name": h["name"],
        "ticker": h["ticker"],
        "cusip": h["cusip"],
        "value_millions": round(h["value_m"], 1),
        "shares": h["shares"],
        "pct_portfolio": round(h["pct_portfolio"], 1),
        "pct_shares_outstanding": h["pct_shares_outstanding"],
        "change_q1": h["delta_q1"],
        "change_q2": h["delta_q2"],
        "change_q3": h["delta_q3"],
        "share_history": share_history,
    }


@mcp.tool()
def generate_investment_thesis(fund_name: str, rank: int = 0) -> dict:
    """Generate an AI investment thesis for a holding. rank=0 generates for all top holdings.
    Requires Ollama running locally."""
    if not dt.check_ollama():
        return {"error": "Ollama not running. Start it with: ollama serve"}

    data = dt.fetch_fund_data(fund_name)
    if not data:
        return {"error": f"Could not fetch data for '{fund_name}'."}

    targets = [h for h in data["top_holdings"] if h["rank"] == rank] if rank > 0 else data["top_holdings"]
    if rank > 0 and not targets:
        return {"error": f"Position #{rank} not found."}

    theses = []
    for h in targets:
        thesis_text = dt.llm_generate_thesis(h, data["fund_name"])
        theses.append({"rank": h["rank"], "name": h["name"], "ticker": h["ticker"], "thesis": thesis_text})

    return {"fund_name": data["fund_name"], "theses": theses}


@mcp.tool()
def get_sector_breakdown(fund_name: str) -> dict:
    """Get sector and industry classification for a fund's top holdings."""
    data = dt.fetch_fund_data(fund_name)
    if not data:
        return {"error": f"Could not fetch data for '{fund_name}'."}

    sector_data = dt.fetch_sector_data(data["top_holdings"])
    sector_totals = {}
    holdings_with_sectors = []
    for h in data["top_holdings"]:
        ticker = h["ticker"] or "N/A"
        sd = sector_data.get(ticker, {"sector": "Unknown", "industry": "Unknown"})
        holdings_with_sectors.append({
            "rank": h["rank"], "name": h["name"], "ticker": ticker,
            "sector": sd["sector"], "industry": sd["industry"],
            "pct_portfolio": round(h["pct_portfolio"], 1),
        })
        sector_totals[sd["sector"]] = sector_totals.get(sd["sector"], 0) + h["pct_portfolio"]

    return {
        "fund_name": data["fund_name"],
        "holdings": holdings_with_sectors,
        "sector_allocation": [{"sector": s, "pct": round(p, 1)} for s, p in sorted(sector_totals.items(), key=lambda x: -x[1])],
    }


@mcp.tool()
def track_website_changes(domain: str, mode: str = "team", start_year: int = 0, end_year: int = 0) -> dict:
    """Track team member or portfolio company changes on a fund's website using Wayback Machine.
    mode: 'team' or 'portfolio'. Years default to last 5 years if 0."""
    from datetime import datetime
    from urllib.parse import urlparse

    now = datetime.now()
    if start_year == 0:
        start_year = now.year - 5
    if end_year == 0:
        end_year = now.year

    if not domain.startswith("http"):
        domain = "https://" + domain

    parsed = urlparse(domain)
    if not parsed.path.strip("/"):
        candidates = dt.discover_pages(domain, mode=mode)
        if candidates:
            url = candidates[0]
        else:
            return {"error": f"Could not discover {mode} pages on {domain}. Try a full URL."}
    else:
        url = domain

    return dt.track_website_changes(url, mode=mode, start_year=start_year, end_year=end_year)


if __name__ == "__main__":
    mcp.run(transport="stdio")

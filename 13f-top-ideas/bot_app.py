#!/usr/bin/env python3
"""
13F Filing Analyzer - Chat Bot UI

A Streamlit chat interface that lets users conversationally explore
hedge fund 13F SEC filings. Uses Google Gemini (free tier) for AI responses
with RAG (data from SEC EDGAR injected as context).

Usage:
    streamlit run bot_app.py
"""

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd
import data_tools as dt

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="13F Filing Analyzer",
    page_icon="\U0001F4CA",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Custom CSS — chatbot-style UI
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .stApp { max-width: 1200px; margin: 0 auto; }

    [data-testid="stChatMessage"] {
        border-radius: 16px;
        margin-bottom: 8px;
    }

    .change-positive { color: #16a34a; font-weight: 600; }
    .change-negative { color: #dc2626; font-weight: 600; }
    .change-new { color: #2563eb; font-weight: 600; }

    .fund-card {
        background: linear-gradient(135deg, #1e3a5f 0%, #2d5a87 100%);
        color: white; padding: 16px; border-radius: 12px; margin: 8px 0;
    }
    .fund-card h3 { margin: 0 0 4px 0; font-size: 1.1em; }
    .fund-card p { margin: 2px 0; opacity: 0.85; font-size: 0.85em; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

defaults = {
    "messages": [],
    "fund_data": None,
    "sec_session": None,
    "theses": {},
    "sector_data": {},
    "llm_available": None,
    "welcomed": False,
    "pending_action": None,
    "wayback_results": None,      # Last wayback tracking result for PDF export
    "wayback_year_start": datetime.now().year - 5,
    "wayback_year_end": datetime.now().year,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.sec_session is None:
    st.session_state.sec_session = dt.make_session()
if st.session_state.llm_available is None:
    st.session_state.llm_available = dt.check_llm()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def add_msg(role, content, msg_type="text"):
    """Add a message to chat history. msg_type: text, table, sectors, thesis, wayback."""
    st.session_state.messages.append({"role": role, "content": content, "type": msg_type})


def format_holdings_context(data):
    """Format holdings as text for LLM context (RAG)."""
    lines = []
    for h in data["top_holdings"]:
        ticker = h["ticker"] or h["name"]
        lines.append(
            f"#{h['rank']} {ticker} ({h['name']}): ${h['value_m']:,.1f}M, "
            f"{h['pct_portfolio']:.1f}% of portfolio, "
            f"Q-1: {h['delta_q1']}, Q-2: {h['delta_q2']}, Q-3: {h['delta_q3']}, "
            f"Shares: {h['shares']:,}, %S/O: {h['pct_shares_outstanding']}, "
            f"ADV: {h['pct_3m_adv']}"
        )
    return "\n".join(lines)


def ask_llm(question, data):
    """RAG: inject fund data as context, ask Ollama."""
    context = format_holdings_context(data)
    system = (
        f"You are a friendly, conversational financial research assistant. "
        f"You're helping someone explore {data['fund_name']}'s 13F filing "
        f"(as of {data['filings'][0]['report_date']}). "
        f"Total 13F value: ${data['total_value']/1e9:.1f}B across {data['num_positions']} positions.\n\n"
        f"Be concise, helpful, and conversational — like a smart friend who knows finance. "
        f"If the user's question isn't about this fund's data, answer generally but stay helpful. "
        f"Use markdown formatting. Keep answers to 2-4 short paragraphs max.\n\n"
        f"IMPORTANT: 13F filings only show long equity positions. Short positions, derivatives, "
        f"and non-US holdings are NOT included. Market values are as of quarter-end. "
        f"13F filings have a 45-day reporting delay. This is analysis, not investment advice."
    )
    prompt = f"Here are the top 20 holdings:\n{context}\n\nUser: {question}"
    return dt.llm_generate(prompt, system)


def ask_llm_general(question):
    """General-purpose chat — no fund data needed. For answering random questions."""
    system = (
        "You are a friendly, knowledgeable financial research assistant built into "
        "a hedge fund analysis tool. You can chat about anything — finance, investing, "
        "SEC filings, hedge funds, markets, economics, or even general questions.\n\n"
        "Be concise, helpful, and conversational — like a smart friend who knows finance. "
        "Use markdown formatting. Keep answers to 2-4 short paragraphs max.\n\n"
        "If the user asks about a specific fund or wants to see holdings data, let them know "
        "they can ask you to load a fund (e.g., 'show me Viking's holdings' or just type a "
        "fund name like 'Berkshire').\n\n"
        "You can also help with Wayback Machine website tracking — tell users they can say "
        "things like 'who's on the team at a16z.com' to track team changes over time.\n\n"
        "This is analysis and education, not investment advice."
    )
    return dt.llm_generate(question, system)


def build_smart_summary(data):
    """Build a natural-language summary highlighting notable moves."""
    holdings = data["top_holdings"]
    total_b = data["total_value"] / 1e9

    # Find new positions
    new_positions = [h for h in holdings if h["delta_q1"] == "NEW"]
    # Find biggest increases
    increases = []
    decreases = []
    for h in holdings:
        d = h["delta_q1"]
        if d.startswith("+"):
            try:
                pct = float(d.strip("+%"))
                increases.append((h, pct))
            except ValueError:
                pass
        elif d.startswith("-"):
            try:
                pct = float(d.strip("-%"))
                decreases.append((h, pct))
            except ValueError:
                pass

    increases.sort(key=lambda x: -x[1])
    decreases.sort(key=lambda x: -x[1])

    summary = (
        f"Here's **{data['fund_name']}**'s latest 13F "
        f"(filed {data['filings'][0]['filing_date']}, "
        f"period ending {data['filings'][0]['report_date']}):\n\n"
        f"**${total_b:.1f}B** across **{data['num_positions']}** positions."
    )

    # Notable moves
    moves = []
    if new_positions:
        names = ", ".join(f"**{h['ticker'] or h['name'][:15]}**" for h in new_positions[:3])
        suffix = f" (+{len(new_positions)-3} more)" if len(new_positions) > 3 else ""
        moves.append(f"New positions: {names}{suffix}")
    if increases:
        h, pct = increases[0]
        moves.append(f"Biggest increase: **{h['ticker'] or h['name'][:15]}** (+{pct:.1f}%)")
    if decreases:
        h, pct = decreases[0]
        moves.append(f"Biggest trim: **{h['ticker'] or h['name'][:15]}** (-{pct:.1f}%)")

    if moves:
        summary += "\n\n**Notable moves this quarter:**\n" + "\n".join(f"- {m}" for m in moves)

    summary += "\n\nHere are their top holdings:"

    return summary


def _extract_domain(text):
    """Pull a domain or URL out of free-form text.  Returns '' if none found."""
    # Match explicit URLs first
    m = re.search(r'(https?://[^\s,]+)', text)
    if m:
        return m.group(1).rstrip(".,;!?)")
    # Match bare domains  (e.g.  a16z.com, sequoiacap.com/team)
    m = re.search(r'([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}(?:/[^\s,]*)?)', text)
    if m:
        return m.group(1).rstrip(".,;!?)")
    return ""


def _extract_fund_name(text):
    """Try to extract a fund name from natural language.

    Checks against known aliases first, then falls back to heuristics to pull
    out the fund-name portion from sentences like "give me pdf report on Pershing"
    or "what is Viking investing in?".
    """
    lower = text.strip().lower()

    # 1. Check known aliases (longest match first to prefer "lone pine" over "lone")
    sorted_aliases = sorted(dt.FUND_ALIASES.keys(), key=len, reverse=True)
    for alias in sorted_aliases:
        if alias in lower:
            return alias

    # 2. Check full official names
    for alias, official in dt.FUND_ALIASES.items():
        if official.lower() in lower:
            return alias

    # 3. Heuristic: strip common filler words and return what's left
    #    e.g. "give me pdf report on Pershing Square" → "Pershing Square"
    filler = r'\b(?:give|me|show|get|pull|up|find|look|a|an|the|pdf|report|on|for|of|' \
             r'about|what|is|are|do|does|they|their|holdings|positions|' \
             r'sectors?|thes[ie]s|investing|invested|in|at|from|to|generate|' \
             r'create|make|download|export|save|full|please|can|you|i|want|' \
             r'need|let|see|how|has|have|been|with|tell|analyze|analysis)\b'
    cleaned = re.sub(filler, ' ', lower)
    cleaned = re.sub(r'[^\w\s]', ' ', cleaned)  # remove punctuation
    cleaned = ' '.join(cleaned.split()).strip()

    if cleaned and len(cleaned) >= 2:
        return cleaned

    return text.strip()


def _detect_deferred_action(lower):
    """Detect if the user wants a specific action done AFTER the fund loads.

    Returns an action string to queue, or None.
    """
    if any(w in lower for w in ["pdf", "report", "generate report", "full report",
                                 "create report", "make a report", "download report"]):
        return "Generate full PDF report"
    if any(w in lower for w in ["sector", "industry", "industries", "sector breakdown"]):
        return "Sector breakdown"
    if any(w in lower for w in ["thesis", "investment thesis", "bull case", "bear case",
                                 "deep dive"]):
        return "Generate investment theses for top 5"
    return None


def _detect_wayback_mode(lower):
    """Return 'team' or 'portfolio' from natural language, defaulting to team."""
    # Negation check — user explicitly says NOT portfolio
    neg = ["not portfolio", "no portfolio", "don't want portfolio", "not companies",
           "instead of portfolio", "team not portfolio", "team members not portfolio"]
    if any(n in lower for n in neg):
        return "team"

    # Portfolio signals (only if no team words co-occur)
    portfolio_w = ["portfolio compan", "investments over", "what they invest",
                   "companies they", "backed companies", "portfolio changes",
                   "track portfolio", "scrape portfolio", "their investments"]
    team_w = ["team", "members", "people", "staff", "who work", "who was on",
              "who is on", "who are", "employees", "personnel", "leadership",
              "joined", "left the", "hired", "departed"]
    has_portfolio = any(w in lower for w in portfolio_w)
    has_team = any(w in lower for w in team_w)
    if has_portfolio and not has_team:
        return "portfolio"
    return "team"


def classify_intent(user_input):
    """Classify user intent from natural language. Returns (intent, params).

    Designed to understand conversational, human-style requests — not just
    rigid keywords.  When in doubt, defaults to the most helpful action.
    """
    lower = user_input.strip().lower()
    data = st.session_state.fund_data

    # ------------------------------------------------------------------
    # 0.  Greetings / pleasantries  (handle early so they don't leak)
    # ------------------------------------------------------------------
    greetings = ["hi", "hello", "hey", "sup", "yo", "what's up", "howdy",
                 "hola", "good morning", "good afternoon", "good evening",
                 "thanks", "thank you", "thx", "ty", "cool", "ok", "okay",
                 "got it", "nice", "awesome", "great"]
    # Only exact match or very short pleasantries
    if lower.strip("!. ") in greetings:
        return ("greeting", {})

    # ------------------------------------------------------------------
    # 1.  Wayback intents  (work with or without a fund loaded)
    # ------------------------------------------------------------------

    # 1a.  Discover pages on a domain
    discover_signals = ["discover", "find pages", "what pages", "scan pages",
                        "crawl", "list pages", "show pages on", "check pages"]
    if any(w in lower for w in discover_signals):
        domain = _extract_domain(user_input)
        if domain:
            return ("wayback_discover", {"domain": domain})

    # 1b.  Set year range
    yr_range = re.search(r'(\d{4})\s*[-\u2013to ]+\s*(\d{4})', lower)
    if yr_range and any(w in lower for w in ["year", "range", "from", "between", "period", "set"]):
        return ("wayback_years", {"start": int(yr_range.group(1)),
                                   "end": int(yr_range.group(2))})

    # 1c.  Export / download wayback PDF
    if st.session_state.wayback_results and any(w in lower for w in
            ["wayback pdf", "wayback report", "save wayback", "export wayback",
             "download wayback", "save tracking", "export tracking",
             "download tracking", "save the report", "get the pdf"]):
        return ("wayback_pdf", {})

    # 1d.  Track team / portfolio on a website  (the big one)
    #      Very broad set of signals — anything that sounds like a user
    #      wanting to see people/companies on a site over time.
    wayback_signals = [
        # explicit commands
        "track", "wayback", "scrape",
        # team language
        "team change", "team member", "team over", "team history",
        "member change", "staff change", "people at", "staff at",
        "who was on", "who is on", "who works", "who worked",
        "who are the", "who's at", "who's on", "show me the team",
        "show team", "employees at", "leadership at", "people over time",
        "how has the team", "changes to the team",
        # portfolio language
        "portfolio change", "investment change", "companies over",
        "what they invest",
        # generic + domain likely present
        "changes on", "changes at", "over the years", "over time",
        "history of",
    ]
    domain_in_msg = _extract_domain(user_input)
    has_wayback_signal = any(w in lower for w in wayback_signals)
    # Also trigger if a domain is mentioned alongside team/portfolio words
    if domain_in_msg and not has_wayback_signal:
        has_wayback_signal = any(w in lower for w in
            ["team", "people", "staff", "members", "portfolio", "companies",
             "investments", "who", "track", "scrape", "history", "changes"])
    if has_wayback_signal and domain_in_msg:
        mode = _detect_wayback_mode(lower)
        params = {"domain": domain_in_msg, "mode": mode}
        if yr_range:
            params["start_year"] = int(yr_range.group(1))
            params["end_year"] = int(yr_range.group(2))
        return ("wayback", params)
    if has_wayback_signal and not domain_in_msg:
        return ("wayback_ask", {})

    # ------------------------------------------------------------------
    # 2.  Fund data intents  (need a fund loaded, or trigger lookup)
    # ------------------------------------------------------------------

    # If no fund is loaded, decide: is this a fund lookup, or a general question?
    if not data:
        # Check if a known fund name is mentioned
        fund_name = _extract_fund_name(user_input)
        known_fund = False
        for alias in dt.FUND_ALIASES:
            if alias in lower:
                known_fund = True
                break

        # Signals that the user wants to look up / analyze a specific fund
        # These only trigger fund_lookup when a known fund alias is present,
        # OR when combined with strong action words
        if known_fund:
            deferred = _detect_deferred_action(lower)
            return ("fund_lookup", {"query": fund_name, "deferred_action": deferred})

        # Strong signals — user clearly wants to do something fund-specific
        # but used a name we don't recognize (still try the lookup)
        strong_fund_signals = ["pull up", "look up", "analyze", "load",
                               "get me", "pdf", "report", "top 20",
                               "investing in", "what do they own",
                               "what are they holding", "deep dive",
                               "sector breakdown"]
        wants_fund_action = any(w in lower for w in strong_fund_signals)

        if wants_fund_action and fund_name:
            deferred = _detect_deferred_action(lower)
            return ("fund_lookup", {"query": fund_name, "deferred_action": deferred})

        # If the input is very short (1-3 words) and doesn't look like a question,
        # assume it's a fund name attempt
        word_count = len(lower.split())
        is_question = any(lower.startswith(w) for w in
                          ["what", "how", "why", "when", "where", "who", "is ", "are ",
                           "can ", "do ", "does ", "should", "could", "would", "tell me",
                           "explain", "help"])
        if word_count <= 3 and not is_question:
            deferred = _detect_deferred_action(lower)
            return ("fund_lookup", {"query": fund_name, "deferred_action": deferred})

        # Otherwise, treat it as a general question → LLM chat
        return ("general_chat", {"query": user_input})

    # --- PDF / Report ---
    if any(w in lower for w in ["pdf", "report", "download report", "generate report",
                                 "export", "full report", "save report", "create report",
                                 "make a report", "print", "give me a report"]):
        return ("report", {})

    # --- Chart ---
    if any(w in lower for w in ["chart", "graph", "bar chart", "stacked", "visualize",
                                 "plot", "show chart", "holdings chart", "visual"]):
        if any(w in lower for w in ["sector", "industry", "allocation"]):
            return ("sector_chart", {})
        return ("chart", {})

    # --- Sectors ---
    if any(w in lower for w in ["sector", "industry", "industries", "sector breakdown",
                                 "sector analysis", "what sectors", "sector allocation",
                                 "which sectors", "industry breakdown", "sector exposure"]):
        return ("sectors", {})

    # --- Thesis ---
    thesis_signals = ["thesis", "investment thesis", "bull case", "bear case",
                      "why do they own", "why are they holding", "why hold",
                      "what's the thesis", "analyze position", "make a case",
                      "what's the play", "bull bear", "investment case",
                      "why did they buy", "conviction", "deep dive"]
    if any(w in lower for w in thesis_signals):
        nums = re.findall(r'\d+', lower)
        rank = int(nums[0]) if nums and 1 <= int(nums[0]) <= 20 else 0
        all_20 = any(w in lower for w in ["all 20", "all twenty", "all positions",
                                            "every position", "full", "all holdings",
                                            "every holding", "all of them",
                                            "for all", "for everything"])
        return ("thesis", {"rank": rank, "all": all_20})

    # --- Show table ---
    table_signals = ["holdings", "table", "positions", "show me", "top 20",
                     "what are they holding", "what do they own", "what do they have",
                     "show holdings", "their portfolio", "show portfolio",
                     "what are their", "biggest positions", "largest positions",
                     "what they own", "list holdings", "current holdings",
                     "show me what", "what's in their"]
    if any(w in lower for w in table_signals):
        return ("show_table", {})

    # --- Number = holding detail ---
    stripped = lower.strip("#. ")
    if stripped.isdigit() and 1 <= int(stripped) <= 20:
        return ("detail", {"rank": int(stripped)})

    # --- Detail by pattern ---
    detail_match = re.search(
        r'(?:detail|position|holding|number|#|tell me about|more on|info on|what about)\s*#?(\d+)',
        lower)
    if detail_match:
        rank = int(detail_match.group(1))
        if 1 <= rank <= 20:
            return ("detail", {"rank": rank})

    # --- Detail by ticker/name ---
    if data:
        for h in data["top_holdings"]:
            ticker = (h["ticker"] or "").lower()
            name = h["name"].lower()
            if ticker and (ticker == lower or
                          f"about {ticker}" in lower or
                          f"what about {ticker}" in lower or
                          f"tell me about {ticker}" in lower or
                          f"more on {ticker}" in lower or
                          f"info on {ticker}" in lower or
                          f"how about {ticker}" in lower):
                return ("detail", {"rank": h["rank"]})
            if len(name) > 5 and name[:15].lower() in lower:
                return ("detail", {"rank": h["rank"]})

    # --- Switch fund ---
    switch_signals = ["another fund", "different fund", "new fund", "switch",
                      "change fund", "look up", "analyze", "load fund",
                      "try another", "switch to", "let's look at", "pull up",
                      "show me another", "can we look at"]
    if any(w in lower for w in switch_signals):
        for alias in dt.FUND_ALIASES:
            if alias in lower:
                return ("fund_lookup", {"query": alias})
        return ("switch_fund", {})

    # --- Known fund alias (user just types a fund name) ---
    for alias in dt.FUND_ALIASES:
        if lower == alias or lower == dt.FUND_ALIASES[alias].lower():
            return ("fund_lookup", {"query": user_input})

    # --- Default: free-form question → LLM ---
    return ("question", {"query": user_input})


def build_holdings_df(data):
    """Build a DataFrame of top holdings."""
    return pd.DataFrame([
        {
            "#": h["rank"],
            "Ticker": h["ticker"] or h["cusip"][:6],
            "Company": h["name"][:30],
            "Value ($M)": f"${h['value_m']:,.1f}",
            "Shares": dt.format_shares(h["shares"]),
            "% Port": f"{h['pct_portfolio']:.1f}%",
            "% S/O": h["pct_shares_outstanding"],
            "3M ADV": h["pct_3m_adv"],
            "Q-1": h["delta_q1"],
            "Q-2": h["delta_q2"],
            "Q-3": h["delta_q3"],
        }
        for h in data["top_holdings"]
    ])


def build_detail_text(h, data):
    """Build markdown detail text for a holding."""
    ticker = h["ticker"] or "N/A"
    history_lines = []
    cusip = h["cusip"]
    for qi, qd in enumerate(data["quarters_data"]):
        label = "Current" if qi == 0 else f"Q-{qi}"
        if qd and cusip in qd:
            history_lines.append(
                f"  - **{label}**: {qd[cusip]['shares']:,} shares (${qd[cusip]['value']/1e6:,.1f}M)")
        else:
            history_lines.append(f"  - **{label}**: --")

    return f"""**#{h['rank']}. {h['name']} ({ticker})**

| Metric | Value |
|--------|-------|
| Market Value | ${h['value_m']:,.1f}M |
| Shares Held | {h['shares']:,} |
| % of Portfolio | {h['pct_portfolio']:.1f}% |
| % Shares Outstanding | {h['pct_shares_outstanding']} |
| 3-Month ADV | {h['pct_3m_adv']} |
| Q-1 Change | {h['delta_q1']} |
| Q-2 Change | {h['delta_q2']} |
| Q-3 Change | {h['delta_q3']} |

**Share History:**
{chr(10).join(history_lines)}"""


# ---------------------------------------------------------------------------
# Render a single message
# ---------------------------------------------------------------------------

def render_message(msg):
    """Render a chat message based on its type."""
    content = msg["content"]
    msg_type = msg.get("type", "text")
    data = st.session_state.fund_data

    if msg_type == "text":
        st.markdown(content)

    elif msg_type == "table" and data:
        st.markdown(content)
        st.dataframe(build_holdings_df(data), use_container_width=True, hide_index=True)

    elif msg_type == "sectors" and data:
        st.markdown(content)
        sector_data = st.session_state.sector_data
        if sector_data:
            rows = []
            for h in data["top_holdings"]:
                ticker = h["ticker"] or "N/A"
                sd = sector_data.get(ticker, {"sector": "Unknown", "industry": "Unknown"})
                rows.append({
                    "#": h["rank"],
                    "Ticker": ticker,
                    "Company": h["name"][:25],
                    "Sector": sd["sector"],
                    "Industry": sd["industry"][:25],
                    "% Port": f"{h['pct_portfolio']:.1f}%",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    elif msg_type == "chart" and data:
        st.markdown(content)
        chart_type = msg.get("chart_type", "holdings")
        fig = None
        if chart_type == "sector":
            if st.session_state.sector_data:
                fig = dt.generate_sector_chart(data, st.session_state.sector_data)
        else:
            fig = dt.generate_holdings_chart(data)
        if fig:
            st.pyplot(fig)
            import io
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            safe_name = re.sub(r'[^\w\s-]', '', data['fund_name']).strip().replace(' ', '_')
            label = "sector_chart" if chart_type == "sector" else "holdings_chart"
            st.download_button(
                label="Download Chart as PNG",
                data=buf,
                file_name=f"{safe_name}_{label}.png",
                mime="image/png",
                key=f"dl_chart_{chart_type}_{hash(content)}",
            )

    elif msg_type == "report_download":
        st.markdown(content)
        # Show download button if file exists
        path = msg.get("file_path", "")
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                st.download_button(
                    label="Download PDF Report",
                    data=f.read(),
                    file_name=os.path.basename(path),
                    mime="application/pdf",
                    key=f"dl_{hash(path)}",
                )

    else:
        st.markdown(content)


# ---------------------------------------------------------------------------
# Sidebar — fund list
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("\U0001F4CA 13F Analyzer")
    st.caption("Chat with SEC EDGAR data")

    # LLM status
    if st.session_state.llm_available:
        st.success(f"AI: {dt.get_available_model()}", icon="\u2705")
    else:
        st.warning("AI: No API key", icon="\u26A0\uFE0F")
        st.caption("Set GROQ_API_KEY in secrets")
        if st.button("Retry", key="retry_llm"):
            st.session_state.llm_available = dt.check_llm()
            st.rerun()

    st.divider()

    # Current fund card
    if st.session_state.fund_data:
        data = st.session_state.fund_data
        st.markdown(f"""
<div class="fund-card">
    <h3>{data['fund_name']}</h3>
    <p><strong>${data['total_value']/1e9:.1f}B</strong> across <strong>{data['num_positions']}</strong> positions</p>
    <p>Filed: {data['filings'][0]['filing_date']} | Period: {data['filings'][0]['report_date']}</p>
</div>
""", unsafe_allow_html=True)

        st.divider()
        st.caption("QUICK ACTIONS")
        qcols = st.columns(2)
        with qcols[0]:
            if st.button("Holdings", key="qa_holdings", use_container_width=True):
                st.session_state.pending_action = "Show holdings table"
                st.rerun()
            if st.button("Sectors", key="qa_sectors", use_container_width=True):
                st.session_state.pending_action = "Sector breakdown"
                st.rerun()
            if st.button("Holdings Chart", key="qa_chart", use_container_width=True):
                st.session_state.pending_action = "Show holdings chart"
                st.rerun()
        with qcols[1]:
            if st.button("Theses", key="qa_thesis", use_container_width=True):
                st.session_state.pending_action = "Generate investment theses for top 5"
                st.rerun()
            if st.button("PDF Report", key="qa_report", use_container_width=True):
                st.session_state.pending_action = "Generate full PDF report"
                st.rerun()
            if st.button("Sector Chart", key="qa_sector_chart", use_container_width=True):
                st.session_state.pending_action = "Show sector allocation chart"
                st.rerun()

        st.caption("")  # spacer
        if st.button("Analyze Another Fund", key="qa_new", use_container_width=True):
            st.session_state.fund_data = None
            st.session_state.theses = {}
            st.session_state.sector_data = {}
            add_msg("assistant", "Sure! What fund would you like to explore next?")
            st.rerun()

        st.divider()

        st.caption("WAYBACK MACHINE")
        if st.button("Track Website", key="qa_wayback", use_container_width=True):
            st.session_state.pending_action = "track website"
            st.rerun()
        if st.session_state.wayback_results:
            if st.button("Save Wayback PDF", key="qa_wb_pdf", use_container_width=True):
                st.session_state.pending_action = "save wayback PDF"
                st.rerun()

        st.divider()

    # Fund list
    st.caption("FUNDS")
    popular = ["berkshire", "viking", "pershing", "citadel", "bridgewater",
               "renaissance", "coatue", "point72", "soros", "elliott",
               "millennium", "tiger", "lone pine", "d1", "two sigma"]
    for fund in popular:
        if st.button(fund.title(), key=f"sb_{fund}", use_container_width=True):
            st.session_state.pending_action = fund
            st.rerun()

    st.divider()
    st.caption("Data: SEC EDGAR | AI: Ollama")
    st.caption("Not investment advice.")


# ---------------------------------------------------------------------------
# Welcome
# ---------------------------------------------------------------------------

if not st.session_state.welcomed:
    add_msg("assistant",
        "Hey there! I'm your **hedge fund research assistant**.\n\n"
        "I can help you explore what the biggest funds are buying and selling, "
        "or dig into how fund teams have changed over time. "
        "Just talk to me like you normally would — I'll figure out what you need.\n\n"
        "**Here are some things you can ask me:**\n"
        "- *\"What is Viking investing in?\"*\n"
        "- *\"Show me Berkshire's biggest positions\"*\n"
        "- *\"Who's on the team at a16z.com?\"*\n"
        "- *\"How has the team at sequoiacap.com changed over the years?\"*\n"
        "- *\"Give me a PDF report on Pershing Square\"*\n\n"
        "Or just pick a fund from the sidebar to jump right in!"
    )
    st.session_state.welcomed = True


# ---------------------------------------------------------------------------
# Display chat history
# ---------------------------------------------------------------------------

for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        render_message(msg)


# ---------------------------------------------------------------------------
# Process user input
# ---------------------------------------------------------------------------

def process_input(user_input):
    """Process user input and generate response."""
    add_msg("user", user_input)

    intent, params = classify_intent(user_input)
    data = st.session_state.fund_data

    # --- Greeting ---
    if intent == "greeting":
        # Vary response based on what was said
        if any(w in lower for w in ["thanks", "thank you", "thx", "ty"]):
            add_msg("assistant", "Happy to help! Let me know if there's anything else you'd like to explore.")
        elif any(w in lower for w in ["cool", "ok", "okay", "got it", "nice", "awesome", "great"]):
            add_msg("assistant", "Glad that's useful! What would you like to look at next?")
        else:
            add_msg("assistant",
                "Hey! I'm ready to dig into some hedge fund data whenever you are.\n\n"
                "Just tell me what you're curious about — a fund name, a website to track, "
                "or anything else. Here are some ideas:\n\n"
                "- *\"What is Viking investing in?\"*\n"
                "- *\"Show me who's on the team at a16z.com\"*\n"
                "- *\"Track sequoiacap.com over the years\"*\n\n"
                "Or just pick a fund from the sidebar!"
            )
        st.rerun()
        return

    # --- Fund lookup ---
    if intent == "fund_lookup":
        query = params["query"]
        deferred = params.get("deferred_action")
        add_msg("assistant", f"Let me pull up the latest 13F filing for **{query}**...")

        with st.chat_message("assistant"):
            st.markdown(f"Let me pull up the latest 13F filing for **{query}**...")
            with st.spinner("Fetching from SEC EDGAR... this takes 30-60 seconds"):
                result = dt.fetch_fund_data(query, session=st.session_state.sec_session)

        if result:
            st.session_state.fund_data = result
            st.session_state.theses = {}
            st.session_state.sector_data = {}
            st.session_state.messages.pop()  # Remove "fetching" message

            summary = build_smart_summary(result)
            add_msg("assistant", summary, msg_type="table")

            # If the user asked for something specific (e.g. "pdf report on Pershing"),
            # queue that action so it runs right after the fund loads
            if deferred:
                st.session_state.pending_action = deferred
        else:
            st.session_state.messages.pop()
            known = ", ".join(list(dt.FUND_ALIASES.keys())[:8])
            add_msg("assistant",
                f"Hmm, I couldn't find a fund called \"{query}\". "
                f"Here are some I know: **{known}**.\n\n"
                f"You can also type the full fund name exactly as it appears on SEC EDGAR."
            )
        st.rerun()
        return

    # --- Show holdings table ---
    if intent == "show_table":
        if data:
            add_msg("assistant",
                f"Here are **{data['fund_name']}**'s top {len(data['top_holdings'])} holdings:",
                msg_type="table"
            )
        else:
            add_msg("assistant", "No fund loaded yet. Tell me which fund you'd like to look at!")
        st.rerun()
        return

    # --- Detail on a holding ---
    if intent == "detail":
        rank = params["rank"]
        matches = [h for h in data["top_holdings"] if h["rank"] == rank]
        if matches:
            detail = build_detail_text(matches[0], data)
            add_msg("assistant", detail)
        else:
            add_msg("assistant",
                f"Position #{rank} not found. Try a number between 1 and {len(data['top_holdings'])}.")
        st.rerun()
        return

    # --- Sectors ---
    if intent == "sectors":
        add_msg("assistant", "Pulling sector data...")
        with st.chat_message("assistant"):
            st.markdown("Pulling sector data from Yahoo Finance...")
            with st.spinner("Fetching sector classifications..."):
                if not st.session_state.sector_data:
                    st.session_state.sector_data = dt.fetch_sector_data(data["top_holdings"])

        st.session_state.messages.pop()

        sector_data = st.session_state.sector_data
        sector_totals = {}
        for h in data["top_holdings"]:
            ticker = h["ticker"] or "N/A"
            sd = sector_data.get(ticker, {"sector": "Unknown"})
            sector_totals[sd["sector"]] = sector_totals.get(sd["sector"], 0) + h["pct_portfolio"]

        alloc_lines = []
        for sector, pct in sorted(sector_totals.items(), key=lambda x: -x[1]):
            bar = "\u2588" * int(pct)
            alloc_lines.append(f"- **{sector}**: {pct:.1f}% {bar}")

        summary = (
            f"**{data['fund_name']} — Sector Breakdown**\n\n"
            + "\n".join(alloc_lines)
        )
        add_msg("assistant", summary, msg_type="sectors")
        st.rerun()
        return

    # --- Holdings Chart ---
    if intent == "chart":
        if not data:
            add_msg("assistant", "Load a fund first, then ask for a chart.")
            st.rerun()
            return
        with st.chat_message("assistant"):
            with st.spinner("Generating holdings chart..."):
                fig = dt.generate_holdings_chart(data)
            st.pyplot(fig)
        add_msg("assistant",
                f"**{data['fund_name']} — Top 10 Holdings by Quarter**",
                msg_type="chart")
        st.session_state.messages[-1]["chart_type"] = "holdings"
        st.rerun()
        return

    # --- Sector Chart ---
    if intent == "sector_chart":
        if not data:
            add_msg("assistant", "Load a fund first, then ask for a sector chart.")
            st.rerun()
            return
        # Fetch sector data if not already loaded
        if not st.session_state.sector_data:
            with st.spinner("Fetching sector data..."):
                st.session_state.sector_data = dt.fetch_sector_data(data["top_holdings"])
        with st.chat_message("assistant"):
            with st.spinner("Generating sector chart..."):
                fig = dt.generate_sector_chart(data, st.session_state.sector_data)
            st.pyplot(fig)
        add_msg("assistant",
                f"**{data['fund_name']} — Sector Allocation by Quarter**",
                msg_type="chart")
        st.session_state.messages[-1]["chart_type"] = "sector"
        st.rerun()
        return

    # --- Thesis ---
    if intent == "thesis":
        if not st.session_state.llm_available:
            add_msg("assistant",
                "I need an AI API key to generate investment theses. "
                "Please set your GROQ_API_KEY in Streamlit secrets."
            )
            st.rerun()
            return

        rank = params["rank"]
        want_all = params.get("all", False)

        if rank > 0:
            targets = [h for h in data["top_holdings"] if h["rank"] == rank]
        elif want_all:
            targets = data["top_holdings"]  # All 20
        else:
            targets = data["top_holdings"][:5]  # Default top 5

        full_text = ""
        with st.chat_message("assistant"):
            count_label = f"all {len(targets)}" if want_all else str(len(targets))
            st.markdown(f"Generating investment theses for {count_label} positions...")
            for h in targets:
                ticker = h["ticker"] or h["name"]
                st.markdown(f"**#{h['rank']}. {h['name']} ({ticker})**")
                with st.spinner(f"Writing thesis for {ticker}..."):
                    thesis = dt.llm_generate_thesis(h, data["fund_name"])
                    if thesis and thesis.strip() and not thesis.startswith("[Error:"):
                        st.session_state.theses[h["cusip"]] = thesis
                    else:
                        thesis = f"Could not generate thesis: {thesis}"
                st.markdown(thesis)
                if h != targets[-1]:
                    st.divider()
                full_text += f"**#{h['rank']}. {h['name']} ({ticker})**\n\n{thesis}\n\n---\n\n"

        add_msg("assistant", full_text.strip())
        st.rerun()
        return

    # --- PDF Report ---
    if intent == "report":
        with st.chat_message("assistant"):
            st.markdown(f"Generating full PDF report for **{data['fund_name']}**...")

            # Auto-generate theses for all holdings before building PDF
            missing = [h for h in data["top_holdings"]
                       if not st.session_state.theses.get(h["cusip"], "").strip()
                       or st.session_state.theses.get(h["cusip"], "").startswith("[Error:")]
            if missing:
                if not st.session_state.llm_available:
                    st.warning("GROQ_API_KEY not set — PDF will be generated without AI theses.")
                else:
                    st.markdown(f"Writing investment theses for {len(missing)} positions...")
                    for h in missing:
                        ticker = h["ticker"] or h["name"]
                        with st.spinner(f"Thesis for #{h['rank']} {ticker}..."):
                            thesis = dt.llm_generate_thesis(h, data["fund_name"])
                            if thesis and thesis.strip() and not thesis.startswith("[Error:"):
                                st.session_state.theses[h["cusip"]] = thesis
                            else:
                                st.error(f"Failed for {ticker}: {thesis}")

            # Generate PDF
            with st.spinner("Compiling PDF..."):
                output_dir = Path(tempfile.gettempdir())
                safe_name = re.sub(r'[^\w\s-]', '', data['fund_name']).strip().replace(' ', '_')
                date_str = datetime.now().strftime("%Y%m%d")
                output_path = output_dir / f"{safe_name}_13F_Report_{date_str}.pdf"

                dt.generate_pdf(
                    holdings=data["top_holdings"],
                    fund_name=data["fund_name"],
                    report_date=data["filings"][0]["report_date"],
                    filing_date=data["filings"][0]["filing_date"],
                    total_value=data["total_value"],
                    num_positions=data["num_positions"],
                    theses=st.session_state.theses,
                    output_path=output_path,
                )

        report_msg = (
            f"Your **{data['fund_name']}** 13F report is ready!\n\n"
            f"**What's in the report:**\n"
            f"- Page 1: Top {len(data['top_holdings'])} holdings table with all metrics\n"
            f"- Pages 2+: Investment thesis for each position (150-200 words each)\n\n"
            f"Saved to: `{output_path}`"
        )
        msg = {"role": "assistant", "content": report_msg, "type": "report_download",
               "file_path": str(output_path)}
        st.session_state.messages.append(msg)
        st.rerun()
        return

    # --- Wayback: track ---
    if intent == "wayback":
        domain = params["domain"]
        mode = params["mode"]
        start_yr = params.get("start_year", st.session_state.wayback_year_start)
        end_yr = params.get("end_year", st.session_state.wayback_year_end)
        label = "team" if mode == "team" else "portfolio"

        add_msg("assistant", f"Tracking {label} changes on **{domain}** ({start_yr}-{end_yr})...")
        with st.chat_message("assistant"):
            status_area = st.empty()
            status_area.markdown(f"Tracking {label} changes on **{domain}** ({start_yr}-{end_yr})...")

            def _progress(step, msg):
                status_area.markdown(f"*{msg}*")

            with st.spinner("Fetching archived snapshots... this may take a few minutes"):
                result = dt.track_website_changes(
                    domain, mode=mode,
                    start_year=start_yr, end_year=end_yr,
                    progress_callback=_progress,
                )

        st.session_state.messages.pop()

        if "error" in result:
            add_msg("assistant",
                f"Hmm, I had trouble with that one — {result['error']}\n\n"
                f"A few things you can try:\n"
                f"- Make sure the domain is correct\n"
                f"- Try *\"discover {domain}\"* to see what pages are archived\n"
                f"- Give me a more specific URL like *\"{domain}/team\"*"
            )
        else:
            st.session_state.wayback_results = result  # Save for PDF export
            resolved_url = result.get("url", domain)
            full_label = "Team Members" if mode == "team" else "Portfolio Companies"
            lines = [f"**{full_label} on {resolved_url}**\n\n"
                     f"Period: {result['period']} | Snapshots: {result['num_snapshots']}\n"]

            # Show each year with full member list
            for year, items in sorted(result["yearly_data"].items()):
                if items is None:
                    lines.append(f"**{year}:** No snapshot available")
                elif not items:
                    lines.append(f"**{year}:** No {full_label.lower()} detected")
                else:
                    lines.append(f"\n**{year}** ({len(items)} found):")
                    for i, name in enumerate(items, 1):
                        lines.append(f"{i}. {name}")

            # Changes section — who joined / left
            if result["changes"]:
                has_any_change = any(c.get("added") or c.get("removed") for c in result["changes"])
                if has_any_change:
                    lines.append("\n---\n### Who Joined & Who Left")
                    for change in result["changes"]:
                        added = change.get("added", [])
                        removed = change.get("removed", [])
                        if added or removed:
                            lines.append(f"\n**{change['from_year']} \u2192 {change['to_year']}:**")
                            for a in added:
                                lines.append(f"- :green[+ {a}]")
                            for r in removed:
                                lines.append(f"- :red[- {r}]")
                        else:
                            lines.append(f"**{change['from_year']} \u2192 {change['to_year']}:** No changes")

            lines.append("\n---\n*Say **\"save wayback PDF\"** to download, "
                        "or **\"discover domain.com\"** to find other pages.*")
            add_msg("assistant", "\n".join(lines))
        st.rerun()
        return

    # --- Wayback: discover pages ---
    if intent == "wayback_discover":
        domain = params["domain"]
        if not domain.startswith("http"):
            domain = "https://" + domain

        add_msg("assistant", f"Searching for trackable pages on **{domain}**...")
        with st.chat_message("assistant"):
            st.markdown(f"Searching for pages on **{domain}**...")
            with st.spinner("Querying Wayback Machine CDX..."):
                team_pages = dt.discover_pages(domain, mode="team")
                portfolio_pages = dt.discover_pages(domain, mode="portfolio")

        st.session_state.messages.pop()

        lines = [f"**Discovered pages on {domain}:**\n"]

        if team_pages:
            lines.append("**Team / People pages:**")
            for i, p in enumerate(team_pages[:8], 1):
                lines.append(f"{i}. `{p}`")
        else:
            lines.append("**Team pages:** None found")

        lines.append("")
        if portfolio_pages:
            lines.append("**Portfolio / Investment pages:**")
            for i, p in enumerate(portfolio_pages[:8], 1):
                lines.append(f"{i}. `{p}`")
        else:
            lines.append("**Portfolio pages:** None found")

        lines.append("\nTo track one, say something like:\n"
                    "- *\"track team sequoiacap.com/our-team\"*\n"
                    "- *\"track portfolio a16z.com/portfolio\"*")

        add_msg("assistant", "\n".join(lines))
        st.rerun()
        return

    # --- Wayback: set year range ---
    if intent == "wayback_years":
        start = params["start"]
        end = params["end"]
        if start > end:
            add_msg("assistant", "Start year must be before end year. Try again.")
        elif start < 1996:
            add_msg("assistant", "The Wayback Machine started in 1996 — try a later start year.")
        else:
            st.session_state.wayback_year_start = start
            st.session_state.wayback_year_end = end
            add_msg("assistant",
                f"Got it! Wayback tracking range set to **{start}-{end}**. "
                f"Next time you track a site, I'll use this range.")
        st.rerun()
        return

    # --- Wayback: export PDF ---
    if intent == "wayback_pdf":
        result = st.session_state.wayback_results
        mode = result.get("mode", "team")
        label = "Team Members" if mode == "team" else "Portfolio Companies"
        url = result.get("url", "unknown")

        with st.chat_message("assistant"):
            st.markdown("Generating Wayback tracking PDF...")
            with st.spinner("Compiling PDF..."):
                from urllib.parse import urlparse
                domain_safe = urlparse(url).netloc.replace(".", "_")
                kind = "team" if mode == "team" else "portfolio"
                filename = f"Wayback_{domain_safe}_{kind}_{datetime.now().strftime('%Y%m%d')}.pdf"
                output_path = Path(tempfile.gettempdir()) / filename

                # Parse years from period
                period = result.get("period", "")
                parts = period.split("-")
                start_yr = int(parts[0]) if len(parts) == 2 else datetime.now().year - 5
                end_yr = int(parts[1]) if len(parts) == 2 else datetime.now().year

                dt.generate_wayback_pdf(
                    url=url, label=label,
                    yearly_data=result["yearly_data"],
                    start_year=start_yr, end_year=end_yr,
                    output_path=output_path,
                )

        report_msg = (
            f"Wayback tracking PDF is ready!\n\n"
            f"**{label}** changes on `{url}`\n\n"
            f"Saved to: `{output_path}`"
        )
        msg = {"role": "assistant", "content": report_msg, "type": "report_download",
               "file_path": str(output_path)}
        st.session_state.messages.append(msg)
        st.rerun()
        return

    # --- Wayback: ask for domain ---
    if intent == "wayback_ask":
        yr_range = f"{st.session_state.wayback_year_start}-{st.session_state.wayback_year_end}"
        add_msg("assistant",
            f"Sure, I can look that up! I just need to know **which website** to track.\n\n"
            f"Just give me a domain and I'll use the Wayback Machine to see how "
            f"things have changed over time. For example:\n\n"
            f"- *\"Show me who's on the team at sequoiacap.com\"*\n"
            f"- *\"Track a16z.com portfolio companies\"*\n"
            f"- *\"Who worked at vikingglobal.com from 2018 to 2024?\"*\n\n"
            f"I'm currently looking at **{yr_range}**, but you can change that too — "
            f"just say something like *\"set years 2018 to 2025\"*."
        )
        st.rerun()
        return

    # --- Switch fund ---
    if intent == "switch_fund":
        st.session_state.fund_data = None
        st.session_state.theses = {}
        st.session_state.sector_data = {}
        add_msg("assistant", "Sure! What fund would you like to explore next?")
        st.rerun()
        return

    # --- General chat (no fund loaded, just chatting) ---
    if intent == "general_chat":
        if st.session_state.llm_available:
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    answer = ask_llm_general(params["query"])
            if answer and answer.strip():
                add_msg("assistant", answer)
            else:
                add_msg("assistant",
                    "Hmm, I'm having trouble thinking right now. "
                    "Could you try rephrasing that?\n\n"
                    "You can also just give me a fund name like **Viking** or **Berkshire** "
                    "and I'll pull up their latest 13F filing!"
                )
        else:
            add_msg("assistant",
                "Great question! I'd love to chat about that, but AI features aren't "
                "configured yet.\n\n"
                "In the meantime, I can still do a lot! Try:\n"
                "- **Load a fund** — just type a name like *\"Viking\"* or *\"Berkshire\"*\n"
                "- **Track a website** — *\"Who's on the team at a16z.com?\"*\n"
                "- **Discover pages** — *\"Discover sequoiacap.com\"*"
            )
        st.rerun()
        return

    # --- Free-form question → LLM (fund is loaded) ---
    if intent == "question":
        if st.session_state.llm_available and data:
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    answer = ask_llm(params["query"], data)
            add_msg("assistant", answer)
        else:
            add_msg("assistant",
                "I'd love to answer that, but AI features aren't configured yet. "
                "Set your GROQ_API_KEY in Streamlit secrets for free-form Q&A.\n\n"
                "In the meantime, try asking about **holdings**, **sectors**, or **thesis**."
            )
        st.rerun()
        return


# ---------------------------------------------------------------------------
# Chat input + pending actions
# ---------------------------------------------------------------------------

if st.session_state.pending_action:
    action = st.session_state.pending_action
    st.session_state.pending_action = None
    process_input(action)

if user_input := st.chat_input("Ask me anything — fund names, team tracking, holdings..."):
    process_input(user_input)

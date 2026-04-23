# 13F Top Ideas — User Guide for Investment Professionals

> A plain-English guide to using this tool. No coding experience needed — just follow the steps.

---

## What Does This Tool Do?

This tool lets you **instantly look up what the world's top hedge funds are buying and selling**, based on their public SEC 13F filings. You can:

- See any fund's **top 20 holdings** with dollar values, share counts, and quarter-over-quarter changes
- Get **sector breakdowns** (tech, healthcare, financials, etc.)
- Generate **AI-written investment theses** for each position
- Create **PDF reports** you can share with your team
- Track **team and portfolio changes** on fund websites over time (via the Wayback Machine)

---

## Getting Started (One-Time Setup)

You only need to do this once.

### Step 1: Install Python

Open **Terminal** (on Mac, search "Terminal" in Spotlight).

Check if Python is installed:
```
python3 --version
```
If you see a version number (3.8+), you're good. If not, download Python from https://python.org.

### Step 2: Install Dependencies

In Terminal, navigate to the project folder and install:
```
cd 13f-top-ideas
pip3 install -r requirements.txt
```

### Step 3 (Optional): Install Ollama for AI Features

If you want AI-generated investment theses, install Ollama:
- Download from https://ollama.com
- Open it and run: `ollama pull llama3.1:8b`
- The tool works fine without this — you just won't get the AI thesis feature.

---

## Two Ways to Use the Tool

### Option A: Chat Interface (Recommended for Beginners)

This gives you a web-based chat window — like talking to ChatGPT but for fund analysis.

**To launch:**
```
streamlit run bot_app.py
```
A browser window will open. Type in plain English. Examples below.

### Option B: Terminal Interface

For those comfortable typing commands.

**To launch:**
```
python3 app.py
```

---

## Common Tasks & Commands

### Looking Up a Fund

| What you want | Chat Interface (type this) | Terminal Command |
|---|---|---|
| Look up Viking Global | "What is Viking investing in?" | `viking` |
| Look up Pershing Square | "Show me Pershing Square holdings" | `pershing` |
| Look up Berkshire Hathaway | "What does Berkshire own?" | `berkshire` |

### Supported Fund Names (Quick Reference)

Just type the short name — the tool knows what you mean:

| Short Name | Full Fund Name |
|---|---|
| `viking` | Viking Global Investors |
| `pershing` | Pershing Square Capital Management |
| `berkshire` | Berkshire Hathaway |
| `third point` | Third Point |
| `appaloosa` | Appaloosa Management |
| `elliott` | Elliott Investment Management |
| `citadel` | Citadel Advisors |
| `bridgewater` | Bridgewater Associates |
| `renaissance` | Renaissance Technologies |
| `tiger` | Tiger Global Management |
| `millennium` | Millennium Management |
| `coatue` | Coatue Management |
| `lone pine` | Lone Pine Capital |
| `d1` | D1 Capital Partners |
| `greenlight` | Greenlight Capital |
| `icahn` | Icahn Enterprises |
| `jana` | JANA Partners |
| `maverick` | Maverick Capital |
| `point72` | Point72 Asset Management |
| `two sigma` | Two Sigma Investments |
| `whale rock` | Whale Rock Capital Management |
| `soros` | Soros Fund Management |
| `baupost` | Baupost Group |
| `dragoneer` | Dragoneer Investment Group |

> **Tip:** You can also type any fund name not on this list — the tool will search SEC EDGAR for it.

---

### Viewing Holdings

Once you've loaded a fund:

| What you want | Chat Interface | Terminal |
|---|---|---|
| See top 20 positions | "Show me the holdings" | `show` |
| See ALL positions (full filing) | "Show all positions" | `show all` |
| Deep dive on position #3 | "Tell me about position 3" | `detail 3` |

**What you'll see for each holding:**
- **Rank** — by portfolio weight
- **Ticker** — stock symbol
- **Market Value** — dollar amount held
- **Shares** — number of shares
- **% of Portfolio** — how big the position is
- **% Shares Outstanding** — how much of the company they own
- **3M ADV** — how many days of average trading volume the position represents
- **Q/Q Change** — did they buy more or sell? (NEW, ↑, ↓, EXIT)

---

### Sector Analysis

| What you want | Chat Interface | Terminal |
|---|---|---|
| See sector breakdown | "What sectors are they in?" | `sectors` |
| See sector chart over time | "Show sector chart" | `sector chart` |

This tells you if a fund is concentrated in tech, healthcare, financials, etc.

---

### Charts & Visuals

| What you want | Chat Interface | Terminal |
|---|---|---|
| Holdings bar chart | "Show me a chart" | `chart` |
| Sector allocation chart | "Sector chart" | `sector chart` |

Charts show how the fund's top positions and sector bets have changed across recent quarters.

---

### AI Investment Theses

> Requires Ollama to be running (see setup above).

| What you want | Chat Interface | Terminal |
|---|---|---|
| Thesis for all top holdings | "Generate theses" | `thesis` |
| Thesis for position #5 only | "Thesis for position 5" | `thesis 5` |
| Ask a custom question | "Why might they like AAPL?" | `ask Why might they like AAPL?` |

The AI will write a short investment thesis explaining **why** the fund might hold each position.

---

### PDF Reports

| What you want | Chat Interface | Terminal |
|---|---|---|
| Generate a full PDF report | "Give me a PDF report" | `report` |

This creates a multi-page PDF with:
- Holdings table
- AI-generated thesis for each top position
- Ready to share with your team or save to your files

---

### Website Tracking (Wayback Machine)

Track how a fund's team or portfolio companies have changed over time.

| What you want | Chat Interface |
|---|---|
| Track team changes | "Track team changes at a16z.com" |
| Track portfolio changes | "Track portfolio at sequoiacap.com" |
| Discover trackable pages | "Discover pages on bridgewater.com" |
| Set year range | "Set range 2020 to 2025" |
| Save as PDF | "Save Wayback PDF" |

---

## Typical Workflow Examples

### Example 1: "What's Pershing Square doing?"
```
1. Type: pershing
2. Type: show           → see their top 20
3. Type: detail 1       → deep dive on biggest position
4. Type: sectors        → see sector breakdown
5. Type: thesis         → get AI analysis of each holding
6. Type: report         → save a PDF to share with your team
```

### Example 2: "Quick scan of multiple funds"
```
1. Type: viking         → load Viking
2. Type: show           → see holdings
3. Type: coatue         → switch to Coatue
4. Type: show           → see their holdings
5. Type: tiger          → switch to Tiger Global
6. Type: show           → compare
```

### Example 3: "Has this fund been adding or trimming?"
```
1. Type: elliott        → load Elliott
2. Type: show           → look at the Q/Q Change column
   - "NEW" = just initiated the position
   - "↑ 25%" = added 25% more shares
   - "↓ 10%" = trimmed 10% of shares
   - "EXIT" = completely sold out
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "command not found: python3" | Install Python from https://python.org |
| "No module named requests" | Run `pip3 install -r requirements.txt` |
| Thesis command not working | Make sure Ollama is running (open the Ollama app) |
| Fund not found | Try the full fund name, or check the alias list above |
| Slow loading | SEC data takes a few seconds — the tool respects SEC rate limits |
| Charts not showing | Make sure `matplotlib` is installed (`pip3 install matplotlib`) |

---

## Quick Reference Card

**Start the tool:**
```
streamlit run bot_app.py     ← web chat (recommended)
python3 app.py               ← terminal
```

**Core commands (terminal):**
```
<fund name>     Load a fund (e.g., viking, pershing, berkshire)
show            Top 20 holdings table
show all        All positions
detail <#>      Deep dive on position #
sectors         Sector breakdown
chart           Holdings chart
sector chart    Sector chart
thesis          AI theses for all holdings
thesis <#>      AI thesis for one position
report          Generate PDF report
ask <question>  Ask AI anything about the data
help            Show help
quit            Exit
```

---

## Where Does the Data Come From?

- **Holdings data** → SEC EDGAR (official government filings, public record)
- **Ticker symbols** → SEC & Yahoo Finance
- **Market data** → Yahoo Finance (% outstanding, trading volume)
- **Sector data** → Yahoo Finance industry classification
- **AI theses** → Ollama running locally on your machine (nothing sent to the cloud)
- **Website tracking** → Wayback Machine (Internet Archive)

> **Note:** 13F filings are reported with a ~45-day delay. Q1 filings (Jan-Mar) are due by May 15, Q2 by Aug 14, Q3 by Nov 14, Q4 by Feb 14.

---

*Built for investment professionals who want hedge fund intelligence without the Bloomberg terminal price tag.*

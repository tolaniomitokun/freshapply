# FreshApply

A zero-dependency Python tool that scrapes PM and AI PM job postings from 22 top tech companies, scores them on freshness and fit, and generates an interactive HTML dashboard you can open in any browser.

## Quick Start

```bash
python3 freshapply.py
```

That's it. No `pip install`, no API keys, no config files. Just Python 3.10+ standard library.

## What It Does

1. **Scrapes** public ATS JSON APIs across 4 platforms (Greenhouse, Lever, Ashby, Workable)
2. **Filters** for Product Manager / AI PM roles using keyword matching
3. **Stores** every job in a local SQLite database, tracking when each role was first and last seen
4. **Detects reposts** by hashing job descriptions and comparing against historical data
5. **Scores** each role on two axes:
   - **Freshness (0-100)** — how recently the role appeared, with a penalty for reposts
   - **Fit (0-100)** — weighted keyword matching for AI/ML, seniority, platform/infrastructure, and industry verticals
6. **Prioritizes** into three tiers: Apply Today, Apply This Week, Watch List
7. **Generates** a Markdown digest and an interactive HTML dashboard

## Companies Tracked

| ATS | Companies |
|---|---|
| **Greenhouse** | Anthropic, Stripe, Databricks, Glean, Vercel, Grammarly, Runway, Compass, Scale AI, Figma, Hebbia |
| **Ashby** | OpenAI, Cohere, Notion, Perplexity, Cursor, Replit, Harvey, Ramp, Decagon |
| **Lever** | Mistral |
| **Workable** | Hugging Face |

## Dashboard Features

The HTML dashboard is a single self-contained file — no server needed.

- **Search** across titles, companies, and locations
- **Filter** by company, tier, or application status
- **Sort** by combined score, freshness, fit, company, or date
- **Track applications** — mark jobs as Saved, Applied, Interviewing, or Rejected (persists in localStorage)
- **Score breakdown** — click any card to see exactly which keywords matched and how the fit score was calculated
- **Notes** — add personal notes to any job (persists in localStorage)
- **Hide** irrelevant roles without deleting them
- **Export CSV** of the current filtered view
- **Dark mode** — follows your system preference

## Usage

```bash
# Full run: scrape all boards, score, generate digest + dashboard
python3 freshapply.py

# Regenerate outputs from existing database (no network requests)
python3 freshapply.py --digest
```

Output files are written to `digests/`:
- `digest-YYYY-MM-DD.md` — Markdown summary
- `dashboard-YYYY-MM-DD.html` — interactive HTML dashboard (open in browser)

## Scoring

### Freshness (0-100)

| Age | Score |
|---|---|
| < 6 hours | 100 |
| < 24 hours | 90 |
| < 48 hours | 80 |
| < 72 hours | 70 |
| < 1 week | 55 |
| < 2 weeks | 35 |
| < 30 days | 15 |
| 30+ days | 5 |

Reposts receive a -15 penalty.

### Fit (0-100)

| Category | Weight | Keywords |
|---|---|---|
| AI / ML | 15 | ai, ml, llm, machine learning, generative, deep learning, nlp, gpt, transformer |
| Seniority | 10 | senior, staff, principal, director, lead, head of, vp |
| Domain Fit | 8 | platform, enterprise, infrastructure, workflow, automation, agent, agentic |
| Industry Verticals | 6 | real estate, proptech, healthcare, health tech, clinical |

### Tier Assignment

- **Apply Today** — freshness >= 70, fit >= 30, and AI keywords present
- **Apply This Week** — freshness >= 50, fit >= 20
- **Watch List** — everything else

## Customization

Edit the constants at the top of `freshapply.py` to:

- **Add companies** — append board slugs to `GREENHOUSE_COMPANIES`, `LEVER_COMPANIES`, `ASHBY_COMPANIES`, or `WORKABLE_COMPANIES`
- **Change title filters** — modify `PM_TITLE_PATTERNS` to match different roles (e.g., add `r"software\s+engineer"`)
- **Adjust fit keywords** — edit `FIT_KEYWORDS` to change scoring weights or add new keyword buckets
- **Tune tier thresholds** — modify the `tier()` function

## License

[MIT](LICENSE)

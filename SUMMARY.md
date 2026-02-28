# FreshApply: Build Summary

## What We Built

FreshApply is a zero-dependency Python tool that scrapes Product Manager and AI PM job postings from 65+ tech companies, scores them on freshness and fit, and generates an interactive HTML dashboard — all from a single `python3 freshapply.py` command.

No API keys. No pip install. No config files. Just Python 3.10+ standard library.

## How We Iterated

### Phase 1: Core Engine
Started with the fundamental problem: job boards are noisy, stale, and disconnected from your actual profile. We built:
- **Multi-ATS scraper** covering Greenhouse, Lever, Ashby, and Workable public APIs (65+ companies including Anthropic, OpenAI, DeepMind, Together AI, LangChain, ElevenLabs, Stripe, Databricks, Airbnb, and more)
- **SQLite persistence** with repost detection via description hashing
- **Two-axis scoring system**: Freshness (0-100 based on age, with repost penalty) and Fit (0-100 via weighted keyword matching across AI/ML, seniority, domain, and industry buckets)
- **Three-tier prioritization**: Apply Today, Apply This Week, Watch List
- **Markdown digest** + **self-contained HTML dashboard** (single file, no server)

### Phase 2: Smart Resume Features
This is where it got interesting. Instead of just showing jobs, we made the tool actively help you apply:
- **Resume gap analysis**: For every job, shows which keyword buckets are MISSING or NEED MORE, with specific point values for how much your fit score would improve
- **Learning recommendations**: Each gap bucket includes actionable learning suggestions (courses, projects, skills to develop)
- **Tailored resume generation**: One-click Word-compatible (.doc) download that reorders your bullet points, competencies, and tagline to maximize keyword overlap with the specific job description
- **Resume preview**: Before downloading, see exactly what changed — which bullets got promoted, which competencies moved up, how the tagline adapted — with keyword match counts
- **Custom resume upload**: Paste or upload your own resume via the dashboard UI, persisted in localStorage

### Phase 3: UX Polish
Iterated heavily on the dashboard experience:
- **Modern chip-based filters**: Tier (single-select), Work Type (multi-select), Status (multi-select) — inspired by Linear and Notion. Count badges update in real-time.
- **Work type classification**: Automatically tags jobs as Remote, Hybrid, or On-site from location and description text
- **Salary filter**: Filter by salary range or just "has salary listed"
- **Card uniformity**: Flexbox layout ensures consistent alignment across all cards regardless of missing data (salary placeholder, bottom-aligned footers)
- **Apply button**: Quick-access Apply button on every card + prominent "Apply Now" in the modal header
- **Application tracking**: Mark jobs as New, Saved, Applied, Interviewing, or Rejected — persists across sessions
- **Notes**: Add personal notes to any job (referral contacts, follow-up dates, prep notes)
- **Dark mode**: Follows system preference automatically
- **CSV export**: Export your current filtered view

### Phase 4: Data Quality
Fixed critical issues that degraded the experience:
- **Title filtering overhaul**: The original `pm\b` regex matched "4 PM" in time strings, `director.*product` was too greedy, and TPM patterns pulled in 40+ non-PM roles. Added exclude patterns and cleaned 74 false positives from the database.
- **Description sanitization**: ATS systems store HTML in wildly different ways — entity-encoded, wrapped in proprietary divs, with pay-transparency boilerplate. Built a robust sanitizer that decodes entities, strips ATS wrapper markup, removes compensation/conclusion/EEO sections, and produces clean formatted HTML. Dashboard shrank 350KB from this alone.

## How We Leveraged AI

### Development Process
The entire tool was built through iterative pair programming with Claude Code (Claude Opus). The workflow:

1. **Rapid prototyping**: Described the concept → got a working scraper + scorer + dashboard in the first session
2. **Feature iteration**: Each session added 2-4 features, tested live, committed and pushed
3. **Bug diagnosis**: AI identified root causes quickly (entity-encoded HTML in DB, regex variable bugs, Python f-string escaping issues) that would have taken much longer to trace manually
4. **Architecture decisions**: AI provided trade-off analysis (e.g., "ATS APIs don't expose applicant counts — here are 3 alternative approaches with effort/value assessments")
5. **Code generation within constraints**: The single-file, zero-dependency, f-string-based architecture created unique constraints (doubled braces, no nested template literals, careful quote escaping). AI navigated these consistently.

### AI Agents Used
- **Claude Code (Claude Opus 4.6)**: Primary development agent — wrote all code, debugged issues, made architecture decisions
- **Explore agents**: Used for codebase navigation when searching across the growing file
- **Plan agents**: Used for multi-step features (chip filter redesign, gap analysis enhancement) — designed implementation plans before writing code

### What Makes This AI-Native
- The **gap analysis + tailored resume** workflow is fundamentally an AI-assisted job search loop: scrape → score → identify gaps → generate targeted resume → apply
- The scoring system encodes domain expertise (what keywords matter for PM roles, how to weight AI/ML vs. seniority vs. domain fit)
- The resume tailoring algorithm (bullet reordering by keyword overlap, tagline adaptation, competency prioritization) is a lightweight version of what you'd get from an AI resume service — but runs entirely locally with zero API calls

## What We're Still Exploring

### Near-term
- **LinkedIn smart links**: For each job, generate a LinkedIn search URL to find mutual connections at the company for referrals. Zero-dependency approach that adds real value.
- **Config file**: Move company lists, title patterns, and fit keywords to a YAML/JSON config so users don't edit Python code.

### Medium-term
- **Role generalization**: Currently PM-specific. Making title patterns, fit keywords, and resume templates configurable would open it to engineers, designers, data scientists, etc.
- **ATS auto-discovery**: Given a company name, detect which ATS platform they use and find the board slug automatically.
- **Application tracking sync**: Export application status to a format that integrates with other tools.

### Bigger questions
- **Hosted version**: Could a simple web wrapper (paste resume, pick role type, get dashboard) validate demand from non-technical users?
- **Monetization**: The resume tailoring + gap analysis is genuinely novel. Most job tools show listings; this one tells you exactly how to improve your application. Is that worth paying for?
- **Beyond 4 ATS platforms**: Workday, iCIMS, Taleo, and custom career pages would dramatically expand company coverage, but require browser automation (Playwright/Selenium) — breaking the zero-dependency promise.

## By the Numbers
- **1 file**, ~1,600 lines of Python
- **0 dependencies** — pure standard library
- **65+ companies** across 4 ATS platforms
- **158 active PM roles** tracked in the database
- **3 scoring tiers** with two-axis scoring (freshness + fit)
- **5 major phases** of iteration across multiple sessions
- **~15 features** shipped from initial scraper to full dashboard with resume tailoring

---

*Built by Tolani Omitokun, pair-programmed with Claude Code (Claude Opus 4.6)*
*February 2026*

# FreshApply

## Project Overview
Zero-dependency Python tool that scrapes PM/AI PM job postings from 65+ tech companies via public ATS APIs, scores them on freshness + fit, and generates a self-contained interactive HTML dashboard.

## Architecture
- **Single file**: `freshapply.py` (~2100 lines) — scraper, scorer, database, and HTML dashboard generator all in one
- **Eval suite**: `eval_freshapply.py` (~750 lines) — 7 deterministic eval categories
- **Database**: `freshapply.db` (SQLite) — jobs table (with `published_at`, `work_type` columns) + desc_hashes for repost detection
- **Output**: `digests/dashboard-YYYY-MM-DD.html` + `digests/digest-YYYY-MM-DD.md`
- **Resume**: `resume.json` (gitignored) — structured resume with `country`/`city` fields for location flagging
- **No dependencies**: Python 3.10+ standard library only

## Key Constraints

### Python f-string HTML generation
The entire HTML dashboard (CSS + HTML + JS) is generated inside a Python f-string. This means:
- **All JS braces must be doubled**: `{{` and `}}` instead of `{` and `}`
- **No nested template literals**: Use string concatenation with `function(){{}}` instead of arrow functions with template literals inside template literals
- **Quote escaping for onclick handlers**: Use `\\'` in Python source to produce `\'` in the generated JS output (single `\'` in Python just produces `'`)
- **`</script>` in data**: The JSON data blob uses `.replace("</", "<\\/")` to avoid breaking the script tag

### HTML sanitization
- `_sanitize_html()` must call `html_mod.unescape()` first — ATS systems store entity-encoded HTML in the database (`&lt;div` instead of `<div`)
- Always apply sanitization at dashboard generation time (not just at scrape time) to handle pre-existing DB data
- Strips iframes (Vimeo embeds from Applied Intuition), pay-transparency boilerplate, class/style attributes

### Timezone handling
- ATS dates (`published_at`) come with timezone info (e.g., `2026-02-27T20:38:32-05:00`)
- `first_seen_at` is stored as naive UTC
- `freshness_score()` normalizes naive datetimes to UTC before subtraction

### Dashboard state
- All user state (statuses, notes, hidden jobs) stored in `localStorage` key `freshapply_state`
- Custom resume stored in `localStorage` key `freshapply_custom_resume`
- Resume data is embedded as `RESUME_DATA` JSON in the dashboard
- `FIT_KW` (fit keyword config) is also embedded for client-side re-scoring
- Uploading a custom resume triggers `rescoreWithResume()` — recalculates fit scores, tiers, and suggestions in JS

### Fit scoring
- Uses `FIT_KEYWORDS` dict with 4 buckets: AI/ML (max 30), Seniority (max 25), Domain Fit (max 25), Industry Verticals (max 20)
- Patterns must handle plurals (use `\bllms?\b` not `\bllm\b`, `\bplatforms?\b` not `\bplatform\b`)
- Total fit capped at 100
- Gap analysis is **job-specific** — `_build_resume_suggestions()` extracts keywords from each job's description, not a static template

### Tier assignment
- **Today**: freshness >= 80 (within 48h) + fit >= 40 + has AI keywords
- **This Week**: freshness >= 55 (within 7 days) + fit >= 25
- **1 Week+**: everything else

### Location detection & flagging
- `_detect_countries()` parses location strings for countries using state abbreviations, province codes, country names, known cities, and region names (NAMER, EMEA, APAC)
- `_is_region_only()` detects locations with no specific city (e.g., "NAMER", "United States") → classified as Remote
- `_classify_location_flag()` returns "", "Relocation", or "International" based on user's `country`/`city` in resume.json
- Jobs are never hidden — just flagged with colored badges

## Important File Sections
- **Lines ~25-46**: `RESUME_DATA` — structured resume loaded from `resume.json`
- **Lines ~48-102**: ATS company rosters + display names
- **Lines ~104-268**: Location detection constants + functions (`_detect_countries`, `_is_region_only`, `_classify_location_flag`)
- **Lines ~270-331**: `PM_TITLE_PATTERNS` + `PM_EXCLUDE_PATTERNS` + `FIT_KEYWORDS`
- **Lines ~339-440**: Database schema (with `published_at`, `work_type` migrations) + `upsert_job()`
- **Lines ~460-685**: ATS scrapers (Greenhouse, Lever, Ashby, Workable) — each captures `publishedAt` and `workType`
- **Lines ~686-765**: Scoring functions (`freshness_score`, `compute_fit_breakdown`, `fit_score`, `tier`)
- **Lines ~839-940**: `_build_resume_suggestions()` — job-specific gap analysis
- **Lines ~942-1010**: Dashboard JSON builder — work type from ATS, salary, scoring, location flags
- **Lines ~1010-1280**: CSS (dark mode, chip filters, card layout, modal, location flag badges)
- **Lines ~1280-1420**: HTML structure (toolbar with tier/work-type/location/status chips, grid, modal, resume upload modal)
- **Lines ~1420-2070**: JavaScript (rendering, filtering, sorting, modal, gap analysis, resume tailoring, re-scoring, CSV export)

## Eval Suite (`eval_freshapply.py`)
7 eval categories, all must pass:
1. **Salary Extraction** — DB scan checking `_extract_salary()` against descriptions with `$X - $Y` patterns
2. **PM Title Filtering** — 29 curated test cases (15 should match, 14 should not) + DB scan for suspicious titles
3. **Work Type Classification** — all 377 jobs checked for misclassifications (region-only → Remote)
4. **Description Sanitization** — checks for residual HTML tags, entity-encoded content, boilerplate
5. **Location Detection** — 25 country detection + 16 region-only + 12 flag classification test cases
6. **Fit Scoring** — 6 curated test cases with expected score ranges, breakdown consistency (sum = total), bucket cap validation
7. **Freshness & Date Accuracy** — 10 freshness score tests, published_at date reasonableness, tier assignment consistency

## Commands
```bash
python3 freshapply.py          # Full scrape + score + generate
python3 freshapply.py --digest # Regenerate from existing DB (no network)
python3 eval_freshapply.py     # Run all 7 evals
```

## Testing Changes
After modifying the dashboard JS/CSS:
1. `python3 freshapply.py --digest` — regenerate
2. Extract JS and syntax check: `node --check` on the script block
3. Open the HTML file in a browser to verify visually
4. `python3 eval_freshapply.py` — all 7 evals must pass

## Git
- Remote: `https://github.com/tolaniomitokun/freshapply`
- Branch: `main`
- Always commit with `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`

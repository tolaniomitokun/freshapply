# FreshApply

## Project Overview
Zero-dependency Python tool that scrapes PM/AI PM job postings from 65+ tech companies via public ATS APIs, scores them, and generates a self-contained interactive HTML dashboard.

## Architecture
- **Single file**: `freshapply.py` (~1600 lines) — scraper, scorer, database, and HTML dashboard generator all in one
- **Database**: `freshapply.db` (SQLite) — jobs table + desc_hashes for repost detection
- **Output**: `digests/dashboard-YYYY-MM-DD.html` + `digests/digest-YYYY-MM-DD.md`
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

### Dashboard state
- All user state (statuses, notes, hidden jobs) stored in `localStorage` key `freshapply_state`
- Custom resume stored in `localStorage` key `freshapply_custom_resume`
- Resume data is embedded as `RESUME_DATA` JSON in the dashboard

## Important File Sections
- **Lines ~29-134**: `RESUME_DATA` — structured resume with `section` field per experience
- **Lines ~174-195**: `PM_TITLE_PATTERNS` + `PM_EXCLUDE_PATTERNS` — job title filtering
- **Lines ~353-385**: `_sanitize_html()` — HTML cleanup for job descriptions
- **Lines ~665-720**: `_build_resume_suggestions()` — gap analysis with `tips_map` containing keywords, bullets, and learning recommendations
- **Lines ~745-810**: Dashboard JSON builder — work type classification, salary extraction, scoring
- **Lines ~850-1100**: CSS (including dark mode, chip filters, card layout, modal)
- **Lines ~1100-1190**: HTML structure (toolbar, grid, modal, resume upload modal)
- **Lines ~1190-1550**: JavaScript (rendering, filtering, modal, resume tailoring, preview)

## Commands
```bash
python3 freshapply.py          # Full scrape + score + generate
python3 freshapply.py --digest # Regenerate from existing DB (no network)
```

## Testing Changes
After modifying the dashboard JS/CSS:
1. `python3 freshapply.py --digest` — regenerate
2. Extract JS and syntax check: `node --check` on the script block
3. Open the HTML file in a browser to verify visually

## Git
- Remote: `https://github.com/tolaniomitokun/freshapply`
- Branch: `main`
- Always commit with `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`

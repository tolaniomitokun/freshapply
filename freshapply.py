#!/usr/bin/env python3
"""
FreshApply — Daily PM / AI PM job scraper, scorer, and digest generator.

Scrapes public ATS JSON APIs (Greenhouse, Lever, Ashby, Workable), stores
results in SQLite, scores every role on freshness + fit, and writes a
Markdown digest.

Usage:
    python3 freshapply.py            # scrape, score, and generate digest
    python3 freshapply.py --digest   # regenerate digest from existing DB
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── ATS company rosters ─────────────────────────────────────────────────────

GREENHOUSE_COMPANIES = [
    "anthropic", "stripe", "databricks", "gleanwork", "vercel",
    "grammarly", "runwayml", "urbancompass", "scaleai", "figma", "hebbia",
]

LEVER_COMPANIES = [
    "mistral",
]

ASHBY_COMPANIES = [
    "openai", "cohere", "notion", "perplexity", "cursor", "replit",
    "harvey", "ramp", "decagon",
]

WORKABLE_COMPANIES = [
    "huggingface",
]

# Friendly display names (board slug → label)
DISPLAY_NAMES = {
    "gleanwork": "Glean",
    "runwayml": "Runway",
    "huggingface": "Hugging Face",
    "scaleai": "Scale AI",
    "urbancompass": "Compass",
    "cursor": "Cursor",
}

# ── PM keyword filter ────────────────────────────────────────────────────────

PM_TITLE_PATTERNS = [
    r"product\s+manager",
    r"product\s+management",
    r"pm\b",
    r"product\s+lead",
    r"product\s+director",
    r"director.*product",
    r"head\s+of\s+product",
    r"vp.*product",
    r"group\s+pm",
    r"senior\s+pm",
    r"staff\s+pm",
    r"principal\s+pm",
    r"technical\s+program\s+manager",
    r"tpm\b",
]

PM_RE = re.compile("|".join(PM_TITLE_PATTERNS), re.IGNORECASE)

# ── Fit‑score keyword buckets (weight → list of patterns) ────────────────────

FIT_KEYWORDS = {
    # AI / ML — highest signal
    15: [
        r"\bai\b", r"\bartificial\s+intelligence\b", r"\bmachine\s+learning\b",
        r"\bml\b", r"\bllm\b", r"\blarge\s+language\s+model",
        r"\bgenerative\b", r"\bdeep\s+learning\b", r"\bnlp\b",
        r"\bfoundation\s+model", r"\bgpt\b", r"\btransformer\b",
    ],
    # Seniority
    10: [
        r"\bsenior\b", r"\bstaff\b", r"\bprincipal\b", r"\bdirector\b",
        r"\blead\b", r"\bhead\s+of\b", r"\bvp\b",
    ],
    # Domain fit
    8: [
        r"\bplatform\b", r"\benterprise\b", r"\binfrastructure\b",
        r"\bworkflow\b", r"\bautomation\b", r"\bagent\b", r"\bagentic\b",
    ],
    # Industry verticals
    6: [
        r"\breal\s+estate\b", r"\bproptech\b",
        r"\bhealthcare\b", r"\bhealth\s+tech\b", r"\bclinical\b",
    ],
}

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "freshapply.db")
DIGEST_DIR = os.path.join(BASE_DIR, "digests")

# ── Database ─────────────────────────────────────────────────────────────────


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id            TEXT PRIMARY KEY,   -- ats:company:external_id
            ats           TEXT NOT NULL,
            company       TEXT NOT NULL,
            title         TEXT NOT NULL,
            url           TEXT,
            location      TEXT,
            description   TEXT,
            desc_hash     TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL,
            reposted      INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS desc_hashes (
            hash       TEXT NOT NULL,
            company    TEXT NOT NULL,
            title      TEXT NOT NULL,
            job_id     TEXT NOT NULL,
            seen_at    TEXT NOT NULL
        )
    """)
    conn.commit()


def upsert_job(conn: sqlite3.Connection, job: dict, now: str):
    """Insert or update a job row. Returns ('new'|'updated'|'reposted', row)."""
    jid = job["id"]
    existing = conn.execute("SELECT id, desc_hash, first_seen_at FROM jobs WHERE id = ?", (jid,)).fetchone()

    desc_hash = hashlib.sha256((job.get("description") or "").encode()).hexdigest()[:16]

    if existing is None:
        # Check if a previous job at same company+title had the same description
        reposted = 0
        prev = conn.execute(
            "SELECT job_id FROM desc_hashes WHERE hash = ? AND company = ? AND job_id != ?",
            (desc_hash, job["company"], jid),
        ).fetchone()
        if prev:
            reposted = 1

        conn.execute(
            """INSERT INTO jobs (id, ats, company, title, url, location, description,
                                 desc_hash, first_seen_at, last_seen_at, reposted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (jid, job["ats"], job["company"], job["title"], job.get("url"),
             job.get("location"), job.get("description"), desc_hash, now, now, reposted),
        )
        conn.execute(
            "INSERT INTO desc_hashes (hash, company, title, job_id, seen_at) VALUES (?, ?, ?, ?, ?)",
            (desc_hash, job["company"], job["title"], jid, now),
        )
        conn.commit()
        return "reposted" if reposted else "new"
    else:
        conn.execute("UPDATE jobs SET last_seen_at = ? WHERE id = ?", (now, jid))
        conn.commit()
        return "updated"


# ── HTTP helper ──────────────────────────────────────────────────────────────

def fetch_json(url: str, timeout: int = 30, method: str = "GET", data: bytes = None,
               headers: dict = None) -> list | dict | None:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "FreshApply/1.0 (job-search-tool)")
    req.add_header("Accept", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as exc:
        print(f"  ⚠  {url[:80]}… → {exc}")
        return None


# ── ATS scrapers ─────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def scrape_greenhouse(company: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
    data = fetch_json(url)
    if not data or "jobs" not in data:
        return []
    jobs = []
    for j in data["jobs"]:
        title = j.get("title", "")
        if not PM_RE.search(title):
            continue
        loc = j.get("location")
        location = loc.get("name", "") if isinstance(loc, dict) else str(loc or "")
        jobs.append({
            "id": f"gh:{company}:{j['id']}",
            "ats": "greenhouse",
            "company": company,
            "title": title,
            "url": j.get("absolute_url", ""),
            "location": location,
            "description": _strip_html(j.get("content", "")),
        })
    return jobs


def scrape_lever(company: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{company}"
    data = fetch_json(url)
    if not data or not isinstance(data, list):
        return []
    jobs = []
    for j in data:
        title = j.get("text", "")
        if not PM_RE.search(title):
            continue
        cats = j.get("categories", {})
        location = cats.get("location", "") or cats.get("allLocations", "")
        if isinstance(location, list):
            location = ", ".join(location)
        jobs.append({
            "id": f"lv:{company}:{j['id']}",
            "ats": "lever",
            "company": company,
            "title": title,
            "url": j.get("hostedUrl", ""),
            "location": location,
            "description": _strip_html(j.get("descriptionPlain", j.get("description", ""))),
        })
    return jobs


def scrape_ashby(company: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
    data = fetch_json(url)
    if not data:
        return []
    job_list = data.get("jobs", [])
    jobs = []
    for j in job_list:
        title = j.get("title", "")
        if not PM_RE.search(title):
            continue
        location = j.get("location", "")
        if isinstance(location, dict):
            location = location.get("name", "")
        job_url = j.get("jobUrl", "") or j.get("hostedUrl", "")
        desc = j.get("descriptionPlain", "") or _strip_html(j.get("descriptionHtml", "") or j.get("description", ""))
        jobs.append({
            "id": f"ab:{company}:{j['id']}",
            "ats": "ashby",
            "company": company,
            "title": title,
            "url": job_url,
            "location": location,
            "description": desc,
        })
    return jobs


def scrape_workable(company: str) -> list[dict]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{company}"
    data = fetch_json(url)
    if not data or "jobs" not in data:
        return []
    jobs = []
    for j in data["jobs"]:
        title = j.get("title", "")
        if not PM_RE.search(title):
            continue
        location = j.get("location", "")
        if isinstance(location, dict):
            location = location.get("name", "")
        shortcode = j.get("shortcode", j.get("id", ""))
        job_url = j.get("url", f"https://apply.workable.com/{company}/j/{shortcode}/")
        jobs.append({
            "id": f"wk:{company}:{shortcode}",
            "ats": "workable",
            "company": company,
            "title": title,
            "url": job_url,
            "location": location,
            "description": j.get("description", ""),
        })
    return jobs


# ── Scoring ──────────────────────────────────────────────────────────────────

def freshness_score(first_seen: str, last_seen: str, reposted: bool, now: datetime) -> int:
    """0‑100. Higher = fresher. Reposts get a penalty."""
    try:
        first_dt = datetime.fromisoformat(first_seen)
    except (ValueError, TypeError):
        return 0
    age_hours = max(0, (now - first_dt).total_seconds() / 3600)

    if age_hours <= 6:
        score = 100
    elif age_hours <= 24:
        score = 90
    elif age_hours <= 48:
        score = 80
    elif age_hours <= 72:
        score = 70
    elif age_hours <= 168:  # 1 week
        score = 55
    elif age_hours <= 336:  # 2 weeks
        score = 35
    elif age_hours <= 720:  # ~30 days
        score = 15
    else:
        score = 5

    if reposted:
        score = max(0, score - 15)

    return score


FIT_BUCKET_NAMES = {15: "AI / ML", 10: "Seniority", 8: "Domain Fit", 6: "Industry Verticals"}


def compute_fit_breakdown(title: str, description: str) -> list[dict]:
    """Return list of {bucket, weight, matched} for each keyword bucket."""
    text = f"{title} {description}"
    breakdown = []
    for weight, patterns in FIT_KEYWORDS.items():
        matched_term = None
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                matched_term = m.group(0)
                break
        breakdown.append({
            "bucket": FIT_BUCKET_NAMES.get(weight, f"Weight {weight}"),
            "weight": weight,
            "matched": matched_term,
        })
    return breakdown


def fit_score(title: str, description: str) -> int:
    """0‑100. Higher = better match to target profile."""
    text = f"{title} {description}".lower()
    total = 0
    for weight, patterns in FIT_KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                total += weight
                break  # one hit per bucket is enough

    return min(100, total)


def tier(fresh: int, fit: int, title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    has_ai = bool(re.search(r"\bai\b|\bartificial.intelligence|\bml\b|\bllm\b|\bmachine.learn", text))

    if fresh >= 70 and fit >= 30 and has_ai:
        return "Apply Today"
    if fresh >= 50 and fit >= 20:
        return "Apply This Week"
    return "Watch List"


# ── Digest ───────────────────────────────────────────────────────────────────

TIER_ORDER = {"Apply Today": 0, "Apply This Week": 1, "Watch List": 2}


def generate_digest(conn: sqlite3.Connection):
    os.makedirs(DIGEST_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    path = os.path.join(DIGEST_DIR, f"digest-{today}.md")

    rows = conn.execute("SELECT * FROM jobs").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM jobs LIMIT 0").description]

    scored = []
    for row in rows:
        job = dict(zip(cols, row))
        fresh = freshness_score(job["first_seen_at"], job["last_seen_at"], bool(job["reposted"]), now)
        fit = fit_score(job["title"], job["description"] or "")
        t = tier(fresh, fit, job["title"], job["description"] or "")
        combined = fresh * 0.4 + fit * 0.6
        scored.append({**job, "fresh": fresh, "fit": fit, "tier": t, "combined": combined})

    scored.sort(key=lambda j: (TIER_ORDER.get(j["tier"], 9), -j["combined"]))

    lines = [
        f"# FreshApply Daily Digest — {today}",
        "",
        f"*Generated {now.strftime('%Y-%m-%d %H:%M UTC')} · {len(scored)} PM roles tracked*",
        "",
    ]

    # Summary counts
    tier_counts = {}
    for s in scored:
        tier_counts[s["tier"]] = tier_counts.get(s["tier"], 0) + 1
    for t in ["Apply Today", "Apply This Week", "Watch List"]:
        if t in tier_counts:
            lines.append(f"- **{t}**: {tier_counts[t]} roles")
    lines.append("")

    current_tier = None
    for s in scored:
        if s["tier"] != current_tier:
            current_tier = s["tier"]
            emoji = {"Apply Today": "🔴", "Apply This Week": "🟡", "Watch List": "⚪"}.get(current_tier, "")
            lines.append(f"---\n\n## {emoji} {current_tier}\n")

        display = DISPLAY_NAMES.get(s["company"], s["company"].replace("-", " ").title())
        repost_tag = " *(repost)*" if s["reposted"] else ""
        link = f"[{s['title']}]({s['url']})" if s['url'] else s['title']
        loc = f" · {s['location']}" if s.get("location") else ""

        lines.append(f"### {link}{repost_tag}")
        lines.append(f"**{display}**{loc}")
        lines.append(f"Freshness: **{s['fresh']}** · Fit: **{s['fit']}** · Combined: **{s['combined']:.0f}**")
        lines.append(f"First seen: {s['first_seen_at'][:10]} · Last seen: {s['last_seen_at'][:10]}")
        lines.append("")

    if not scored:
        lines.append("*No PM roles found across tracked boards. Try running again later.*\n")

    content = "\n".join(lines)
    with open(path, "w") as f:
        f.write(content)

    print(f"\n✅ Digest written → {path}")
    return path


# ── HTML Dashboard ───────────────────────────────────────────────────────────

def generate_html_dashboard(conn: sqlite3.Connection):
    os.makedirs(DIGEST_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    path = os.path.join(DIGEST_DIR, f"dashboard-{today}.html")

    rows = conn.execute("SELECT * FROM jobs").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM jobs LIMIT 0").description]

    scored = []
    for row in rows:
        job = dict(zip(cols, row))
        fresh = freshness_score(job["first_seen_at"], job["last_seen_at"], bool(job["reposted"]), now)
        fit = fit_score(job["title"], job["description"] or "")
        t = tier(fresh, fit, job["title"], job["description"] or "")
        combined = round(fresh * 0.4 + fit * 0.6, 1)
        breakdown = compute_fit_breakdown(job["title"], job["description"] or "")
        display = DISPLAY_NAMES.get(job["company"], job["company"].replace("-", " ").title())
        scored.append({
            "id": job["id"],
            "ats": job["ats"],
            "company": display,
            "companySlug": job["company"],
            "title": job["title"],
            "url": job["url"] or "",
            "location": job["location"] or "",
            "fresh": fresh,
            "fit": fit,
            "tier": t,
            "combined": combined,
            "reposted": bool(job["reposted"]),
            "firstSeen": job["first_seen_at"][:10],
            "lastSeen": job["last_seen_at"][:10],
            "breakdown": breakdown,
            "description": (job["description"] or "")[:3000],
        })

    scored.sort(key=lambda j: (TIER_ORDER.get(j["tier"], 9), -j["combined"]))
    jobs_json = json.dumps(scored, ensure_ascii=False)
    gen_time = now.strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FreshApply Dashboard — {today}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#f8f9fa;--card:#fff;--text:#1a1a2e;--muted:#6b7280;--border:#e5e7eb;
--red:#ef4444;--amber:#f59e0b;--green:#22c55e;--blue:#3b82f6;--purple:#8b5cf6;
--red-bg:#fef2f2;--amber-bg:#fffbeb;--gray-bg:#f9fafb;--accent:#2563eb;
--bar-fresh:#22c55e;--bar-fit:#8b5cf6;--radius:10px;--shadow:0 1px 3px rgba(0,0,0,.08)}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f172a;--card:#1e293b;--text:#e2e8f0;
--muted:#94a3b8;--border:#334155;--red-bg:#1c1317;--amber-bg:#1c1a0f;--gray-bg:#1a2332;
--shadow:0 1px 3px rgba(0,0,0,.3)}}}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:var(--bg);color:var(--text);line-height:1.5;padding:0}}
a{{color:var(--accent);text-decoration:none}}
a:hover{{text-decoration:underline}}

/* Header */
.header{{background:var(--card);border-bottom:1px solid var(--border);padding:16px 24px;
position:sticky;top:0;z-index:100;box-shadow:var(--shadow)}}
.header-row{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;max-width:1400px;margin:0 auto}}
.logo{{font-size:22px;font-weight:700;letter-spacing:-.5px}}
.logo span{{color:var(--accent)}}
.header-stats{{display:flex;gap:10px;margin-left:auto;align-items:center;flex-wrap:wrap}}
.stat-badge{{padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600}}
.stat-red{{background:var(--red-bg);color:var(--red)}}
.stat-amber{{background:var(--amber-bg);color:var(--amber)}}
.stat-gray{{background:var(--gray-bg);color:var(--muted)}}
.gen-time{{font-size:12px;color:var(--muted)}}

/* Toolbar */
.toolbar{{background:var(--card);border-bottom:1px solid var(--border);padding:12px 24px}}
.toolbar-inner{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;max-width:1400px;margin:0 auto}}
.search-box{{flex:1;min-width:200px;padding:8px 12px;border:1px solid var(--border);
border-radius:var(--radius);font-size:14px;background:var(--bg);color:var(--text);outline:none}}
.search-box:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.15)}}
select,.btn{{padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius);
font-size:13px;background:var(--card);color:var(--text);cursor:pointer}}
select:focus,.btn:focus{{outline:none;border-color:var(--accent)}}
.tier-pills{{display:flex;gap:4px}}
.pill{{padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600;cursor:pointer;
border:2px solid var(--border);background:var(--card);color:var(--muted);transition:.15s}}
.pill:hover{{border-color:var(--accent)}}
.pill.active{{color:#fff}}
.pill.active[data-tier="Apply Today"]{{background:var(--red);border-color:var(--red)}}
.pill.active[data-tier="Apply This Week"]{{background:var(--amber);border-color:var(--amber)}}
.pill.active[data-tier="Watch List"]{{background:#6b7280;border-color:#6b7280}}
.pill.active[data-tier="all"]{{background:var(--accent);border-color:var(--accent)}}
.btn-export{{background:var(--accent);color:#fff;border:none;font-weight:600}}
.btn-export:hover{{opacity:.9}}

/* Counter */
.counter-bar{{max-width:1400px;margin:12px auto 0;padding:0 24px;font-size:13px;color:var(--muted)}}

/* Grid */
.grid{{max-width:1400px;margin:12px auto;padding:0 24px 40px;
display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}}

/* Card */
.card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
padding:16px;box-shadow:var(--shadow);transition:.15s;position:relative;cursor:pointer}}
.card:hover{{box-shadow:0 4px 12px rgba(0,0,0,.1);transform:translateY(-1px)}}
.card-header{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.card-title{{font-size:15px;font-weight:600;flex:1}}
.card-title a{{color:var(--text)}}
.card-title a:hover{{color:var(--accent)}}
.card-dismiss{{background:none;border:none;color:var(--muted);cursor:pointer;font-size:16px;
padding:2px 6px;border-radius:4px;line-height:1}}
.card-dismiss:hover{{background:var(--border);color:var(--text)}}
.card-meta{{font-size:13px;color:var(--muted);margin:4px 0 10px}}
.card-meta .company{{font-weight:600;color:var(--text)}}
.tier-tag{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;margin-left:6px}}
.tier-tag.t-today{{background:var(--red-bg);color:var(--red)}}
.tier-tag.t-week{{background:var(--amber-bg);color:var(--amber)}}
.tier-tag.t-watch{{background:var(--gray-bg);color:var(--muted)}}
.repost-tag{{font-size:11px;color:var(--amber);font-weight:600;margin-left:4px}}
.score-bars{{display:flex;gap:12px;margin:8px 0}}
.score-bar{{flex:1}}
.score-label{{font-size:11px;color:var(--muted);margin-bottom:2px;display:flex;justify-content:space-between}}
.bar-track{{height:6px;background:var(--border);border-radius:3px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:3px;transition:width .3s}}
.bar-fill.fresh{{background:var(--bar-fresh)}}
.bar-fill.fit{{background:var(--bar-fit)}}
.card-foot{{display:flex;justify-content:space-between;align-items:center;margin-top:10px}}
.card-date{{font-size:11px;color:var(--muted)}}
.status-select{{padding:4px 8px;font-size:11px;border-radius:6px;border:1px solid var(--border);
background:var(--card);color:var(--text)}}
.status-select.s-applied{{border-color:var(--green);color:var(--green)}}
.status-select.s-saved{{border-color:var(--blue);color:var(--blue)}}
.status-select.s-interviewing{{border-color:var(--purple);color:var(--purple)}}
.status-select.s-rejected{{border-color:var(--red);color:var(--red)}}
.has-note{{display:inline-block;width:8px;height:8px;background:var(--amber);border-radius:50%;margin-left:6px;vertical-align:middle}}

/* Modal */
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;
justify-content:center;align-items:flex-start;padding:40px 20px;overflow-y:auto}}
.modal-overlay.open{{display:flex}}
.modal{{background:var(--card);border-radius:14px;max-width:740px;width:100%;
box-shadow:0 20px 60px rgba(0,0,0,.2);padding:28px;position:relative;max-height:85vh;overflow-y:auto}}
.modal-close{{position:absolute;top:12px;right:16px;background:none;border:none;font-size:24px;
cursor:pointer;color:var(--muted);line-height:1}}
.modal-close:hover{{color:var(--text)}}
.modal h2{{font-size:20px;margin-bottom:4px;padding-right:30px}}
.modal .m-meta{{color:var(--muted);font-size:14px;margin-bottom:16px}}
.modal .m-scores{{display:flex;gap:20px;margin-bottom:16px}}
.modal .m-score-box{{text-align:center;padding:10px 16px;border-radius:var(--radius);background:var(--bg)}}
.modal .m-score-val{{font-size:28px;font-weight:700}}
.modal .m-score-lbl{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.breakdown-table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}}
.breakdown-table th{{text-align:left;padding:6px 10px;background:var(--bg);font-weight:600;border-bottom:1px solid var(--border)}}
.breakdown-table td{{padding:6px 10px;border-bottom:1px solid var(--border)}}
.breakdown-table .match{{color:var(--green);font-weight:600}}
.breakdown-table .no-match{{color:var(--muted)}}
.modal .m-desc{{font-size:13px;line-height:1.7;color:var(--muted);margin:12px 0;
max-height:250px;overflow-y:auto;padding:12px;background:var(--bg);border-radius:var(--radius)}}
.modal .m-notes{{width:100%;padding:10px;border:1px solid var(--border);border-radius:var(--radius);
font-size:13px;min-height:70px;background:var(--bg);color:var(--text);resize:vertical;font-family:inherit}}
.modal .m-notes:focus{{outline:none;border-color:var(--accent)}}
.modal .m-actions{{display:flex;gap:10px;margin-top:14px}}
.btn-apply{{padding:10px 20px;background:var(--accent);color:#fff;border:none;border-radius:var(--radius);
font-weight:600;font-size:14px;cursor:pointer}}
.btn-apply:hover{{opacity:.9}}
</style>
</head>
<body>
<div class="header"><div class="header-row">
<div class="logo">Fresh<span>Apply</span></div>
<div class="gen-time">{gen_time} &middot; <span id="totalCount"></span> PM roles</div>
<div class="header-stats" id="tierBadges"></div>
</div></div>

<div class="toolbar"><div class="toolbar-inner">
<input type="text" class="search-box" id="search" placeholder="Search titles, companies, locations...">
<select id="companyFilter"><option value="">All Companies</option></select>
<div class="tier-pills" id="tierPills">
<div class="pill active" data-tier="all">All</div>
<div class="pill" data-tier="Apply Today">Apply Today</div>
<div class="pill" data-tier="Apply This Week">This Week</div>
<div class="pill" data-tier="Watch List">Watch List</div>
</div>
<select id="statusFilter">
<option value="">All Statuses</option>
<option value="New">New</option>
<option value="Saved">Saved</option>
<option value="Applied">Applied</option>
<option value="Interviewing">Interviewing</option>
<option value="Rejected">Rejected</option>
<option value="Hidden">Hidden</option>
</select>
<select id="sortBy">
<option value="combined">Combined Score</option>
<option value="fresh">Freshness</option>
<option value="fit">Fit Score</option>
<option value="company">Company A-Z</option>
<option value="newest">Newest First</option>
</select>
<button class="btn btn-export" onclick="exportCSV()">Export CSV</button>
</div></div>

<div class="counter-bar" id="counterBar"></div>
<div class="grid" id="grid"></div>

<div class="modal-overlay" id="modalOverlay">
<div class="modal" id="modal">
<button class="modal-close" onclick="closeModal()">&times;</button>
<h2 id="mTitle"></h2>
<div class="m-meta" id="mMeta"></div>
<div class="m-scores" id="mScores"></div>
<h4>Fit Score Breakdown</h4>
<table class="breakdown-table"><thead><tr><th>Category</th><th>Weight</th><th>Matched</th></tr></thead>
<tbody id="mBreakdown"></tbody></table>
<h4 style="margin-top:14px">Description</h4>
<div class="m-desc" id="mDesc"></div>
<h4 style="margin-top:14px">Notes</h4>
<textarea class="m-notes" id="mNotes" placeholder="Add your notes here..."></textarea>
<div class="m-actions">
<a class="btn-apply" id="mApplyBtn" href="#" target="_blank">Open Application &rarr;</a>
</div>
</div>
</div>

<script>
const JOBS={jobs_json};
const LS_KEY='freshapply_state';

function loadState(){{try{{return JSON.parse(localStorage.getItem(LS_KEY))||{{}}}}catch{{return{{}}}}}}
function saveState(s){{localStorage.setItem(LS_KEY,JSON.stringify(s))}}
function getState(){{const s=loadState();s.statuses=s.statuses||{{}};s.notes=s.notes||{{}};s.hidden=s.hidden||[];return s}}

let state=getState();
let activeTier='all';
let currentModalId=null;

function esc(s){{const d=document.createElement('div');d.textContent=s;return d.innerHTML}}

function tierClass(t){{if(t==='Apply Today')return 't-today';if(t==='Apply This Week')return 't-week';return 't-watch'}}

function statusClass(st){{return st?'s-'+st.toLowerCase():''}}

function renderCard(j){{
const s=state.statuses[j.id]||'New';
const hasNote=state.notes[j.id]?'<span class="has-note"></span>':'';
return `<div class="card" data-id="${{esc(j.id)}}" onclick="openModal('${{esc(j.id)}}')">
<div class="card-header">
<div class="card-title"><a href="${{esc(j.url)}}" target="_blank" onclick="event.stopPropagation()">${{esc(j.title)}}</a>
<span class="tier-tag ${{tierClass(j.tier)}}">${{esc(j.tier)}}</span>
${{j.reposted?'<span class="repost-tag">REPOST</span>':''}}</div>
<button class="card-dismiss" onclick="event.stopPropagation();dismissJob('${{esc(j.id)}}')" title="Hide">&times;</button>
</div>
<div class="card-meta"><span class="company">${{esc(j.company)}}</span> &middot; ${{esc(j.location||'Remote')}}</div>
<div class="score-bars">
<div class="score-bar"><div class="score-label"><span>Freshness</span><span>${{j.fresh}}</span></div>
<div class="bar-track"><div class="bar-fill fresh" style="width:${{j.fresh}}%"></div></div></div>
<div class="score-bar"><div class="score-label"><span>Fit</span><span>${{j.fit}}</span></div>
<div class="bar-track"><div class="bar-fill fit" style="width:${{j.fit}}%"></div></div></div>
</div>
<div class="card-foot">
<span class="card-date">First seen ${{j.firstSeen}}${{hasNote}}</span>
<select class="status-select ${{statusClass(s)}}" onclick="event.stopPropagation()" onchange="setStatus('${{esc(j.id)}}',this.value,this)">
${{['New','Saved','Applied','Interviewing','Rejected'].map(o=>`<option ${{o===s?'selected':''}}>${{o}}</option>`).join('')}}
</select>
</div></div>`;
}}

function getFiltered(){{
const q=document.getElementById('search').value.toLowerCase();
const co=document.getElementById('companyFilter').value;
const sf=document.getElementById('statusFilter').value;
let jobs=JOBS.filter(j=>{{
if(sf==='Hidden')return state.hidden.includes(j.id);
if(state.hidden.includes(j.id))return false;
if(activeTier!=='all'&&j.tier!==activeTier)return false;
if(co&&j.companySlug!==co)return false;
if(sf){{const st=state.statuses[j.id]||'New';if(st!==sf)return false}}
if(q){{const txt=(j.title+' '+j.company+' '+j.location).toLowerCase();if(!txt.includes(q))return false}}
return true}});
const sort=document.getElementById('sortBy').value;
if(sort==='fresh')jobs.sort((a,b)=>b.fresh-a.fresh);
else if(sort==='fit')jobs.sort((a,b)=>b.fit-a.fit);
else if(sort==='company')jobs.sort((a,b)=>a.company.localeCompare(b.company));
else if(sort==='newest')jobs.sort((a,b)=>b.firstSeen.localeCompare(a.firstSeen));
else jobs.sort((a,b)=>{{const to={{'Apply Today':0,'Apply This Week':1,'Watch List':2}};
const td=to[a.tier]-to[b.tier];return td!==0?td:b.combined-a.combined}});
return jobs}}

function render(){{
const jobs=getFiltered();
document.getElementById('grid').innerHTML=jobs.map(renderCard).join('');
document.getElementById('counterBar').textContent=`Showing ${{jobs.length}} of ${{JOBS.length}} roles`;
const counts={{}};JOBS.forEach(j=>{{counts[j.tier]=(counts[j.tier]||0)+1}});
document.getElementById('tierBadges').innerHTML=
`<span class="stat-badge stat-red">${{counts['Apply Today']||0}} today</span>`+
`<span class="stat-badge stat-amber">${{counts['Apply This Week']||0}} this week</span>`+
`<span class="stat-badge stat-gray">${{counts['Watch List']||0}} watch</span>`;
document.getElementById('totalCount').textContent=JOBS.length;
}}

function initCompanies(){{
const cos=[...new Set(JOBS.map(j=>j.companySlug))].sort();
const sel=document.getElementById('companyFilter');
cos.forEach(c=>{{const o=document.createElement('option');o.value=c;
const dn=JOBS.find(j=>j.companySlug===c);o.textContent=dn?dn.company:c;sel.appendChild(o)}})}}

function setStatus(id,val,el){{state.statuses[id]=val;saveState(state);
el.className='status-select '+statusClass(val);render()}}

function dismissJob(id){{if(!state.hidden.includes(id))state.hidden.push(id);saveState(state);render()}}

function openModal(id){{
const j=JOBS.find(x=>x.id===id);if(!j)return;currentModalId=id;
document.getElementById('mTitle').textContent=j.title;
document.getElementById('mMeta').innerHTML=
`<strong>${{esc(j.company)}}</strong> &middot; ${{esc(j.location||'Remote')}} &middot; `+
`<span class="tier-tag ${{tierClass(j.tier)}}">${{j.tier}}</span>`+
(j.reposted?' <span class="repost-tag">REPOST</span>':'')+
` &middot; First seen ${{j.firstSeen}}`;
document.getElementById('mScores').innerHTML=
`<div class="m-score-box"><div class="m-score-val" style="color:var(--bar-fresh)">${{j.fresh}}</div><div class="m-score-lbl">Fresh</div></div>`+
`<div class="m-score-box"><div class="m-score-val" style="color:var(--bar-fit)">${{j.fit}}</div><div class="m-score-lbl">Fit</div></div>`+
`<div class="m-score-box"><div class="m-score-val" style="color:var(--accent)">${{j.combined}}</div><div class="m-score-lbl">Combined</div></div>`;
document.getElementById('mBreakdown').innerHTML=j.breakdown.map(b=>
`<tr><td>${{esc(b.bucket)}}</td><td>${{b.weight}} pts</td><td>${{b.matched?
`<span class="match">${{esc(b.matched)}}</span>`:'<span class="no-match">--</span>'}}</td></tr>`).join('');
document.getElementById('mDesc').textContent=j.description||'No description available.';
document.getElementById('mNotes').value=state.notes[id]||'';
document.getElementById('mApplyBtn').href=j.url||'#';
document.getElementById('modalOverlay').classList.add('open');
}}

function closeModal(){{
if(currentModalId){{const n=document.getElementById('mNotes').value.trim();
if(n)state.notes[currentModalId]=n;else delete state.notes[currentModalId];saveState(state)}}
currentModalId=null;document.getElementById('modalOverlay').classList.remove('open');render()}}

function exportCSV(){{
const jobs=getFiltered();
const hdr='Title,Company,Location,Tier,Freshness,Fit,Combined,URL,Status,First Seen\\n';
const rows=jobs.map(j=>{{const s=state.statuses[j.id]||'New';
return [j.title,j.company,j.location,j.tier,j.fresh,j.fit,j.combined,j.url,s,j.firstSeen]
.map(v=>`"${{String(v).replace(/"/g,'""')}}"`)
.join(',')}}).join('\\n');
const blob=new Blob([hdr+rows],{{type:'text/csv'}});const a=document.createElement('a');
a.href=URL.createObjectURL(blob);a.download='freshapply-export.csv';a.click()}}

document.getElementById('search').addEventListener('input',render);
document.getElementById('companyFilter').addEventListener('change',render);
document.getElementById('statusFilter').addEventListener('change',render);
document.getElementById('sortBy').addEventListener('change',render);
document.querySelectorAll('.pill').forEach(p=>p.addEventListener('click',function(){{
document.querySelectorAll('.pill').forEach(x=>x.classList.remove('active'));
this.classList.add('active');activeTier=this.dataset.tier;render()}}));
document.getElementById('modalOverlay').addEventListener('click',function(e){{
if(e.target===this)closeModal()}});
document.addEventListener('keydown',function(e){{if(e.key==='Escape')closeModal()}});

initCompanies();render();
</script>
</body>
</html>"""

    with open(path, "w") as f:
        f.write(html)

    print(f"✅ Dashboard written → {path}")
    return path


# ── Main ─────────────────────────────────────────────────────────────────────

def run_scrape(conn: sqlite3.Connection):
    now_str = datetime.now(timezone.utc).isoformat()
    stats = {"new": 0, "updated": 0, "reposted": 0, "errors": 0}

    tasks = (
        [(c, scrape_greenhouse) for c in GREENHOUSE_COMPANIES]
        + [(c, scrape_lever) for c in LEVER_COMPANIES]
        + [(c, scrape_ashby) for c in ASHBY_COMPANIES]
        + [(c, scrape_workable) for c in WORKABLE_COMPANIES]
    )

    total = len(tasks)
    for i, (company, scraper) in enumerate(tasks, 1):
        display = DISPLAY_NAMES.get(company, company.title())
        ats = {scrape_greenhouse: "Greenhouse", scrape_lever: "Lever",
               scrape_ashby: "Ashby", scrape_workable: "Workable"}[scraper]
        print(f"  [{i:2d}/{total}] {display:<25s} ({ats})  ", end="", flush=True)

        try:
            jobs = scraper(company)
        except Exception as exc:
            print(f"ERROR: {exc}")
            stats["errors"] += 1
            continue

        pm_count = len(jobs)
        for job in jobs:
            result = upsert_job(conn, job, now_str)
            stats[result] = stats.get(result, 0) + 1

        print(f"→ {pm_count} PM role{'s' if pm_count != 1 else ''}")

    print(f"\nScrape complete: {stats['new']} new · {stats['updated']} updated · "
          f"{stats['reposted']} reposts · {stats['errors']} errors")


def main():
    digest_only = "--digest" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if not digest_only:
        print("=" * 60)
        print("  FreshApply — PM / AI PM Job Tracker")
        print("=" * 60)
        print()
        run_scrape(conn)

    generate_digest(conn)
    generate_html_dashboard(conn)
    conn.close()


if __name__ == "__main__":
    main()

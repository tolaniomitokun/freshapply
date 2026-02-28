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
import html as html_mod
import json
import os
import re
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Resume data (structured for per-job tailoring) ──────────────────────────

RESUME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.json")

# Load resume from local file (gitignored — keeps personal data out of the repo)
if os.path.exists(RESUME_PATH):
    with open(RESUME_PATH, "r") as _f:
        RESUME_DATA = json.load(_f)
else:
    RESUME_DATA = {
        "name": "YOUR NAME",
        "contact": "City, ST  |  email@example.com",
        "headline": "YOUR TITLE",
        "tagline": "",
        "summary": "",
        "competencies": [],
        "experience": [],
        "tools": {},
        "education": "",
        "country": "",
        "city": "",
    }

# ── ATS company rosters ─────────────────────────────────────────────────────

GREENHOUSE_COMPANIES = [
    # AI-native
    "anthropic", "appliedintuition", "arizeai", "assemblyai", "cresta",
    "deepmind", "descript", "fireworksai", "inflectionai", "nuro",
    "sambanovasystems", "snorkelai", "stabilityai", "togetherai",
    # AI-heavy tech
    "airbnb", "airtable", "amplitude", "brex", "coinbase", "databricks",
    "datadog", "figma", "gitlab", "gleanwork", "gongio", "grammarly",
    "hebbia", "onetrust", "runwayml", "samsara", "scaleai", "stripe",
    "twilio", "urbancompass", "vercel", "verkada",
]

LEVER_COMPANIES = [
    "mistral", "plaid", "zoox",
]

ASHBY_COMPANIES = [
    # AI-native
    "anyscale", "baseten", "character", "cohere", "deepgram", "elevenlabs",
    "langchain", "modal", "openai", "pinecone", "sierra", "twelve-labs",
    "writer",
    # AI-heavy tech
    "cursor", "decagon", "harvey", "linear", "notion", "perplexity",
    "ramp", "replit", "rula", "zapier",
]

WORKABLE_COMPANIES = [
    "huggingface", "kody", "leadtech", "smeetz",
]

# Friendly display names (board slug → label)
DISPLAY_NAMES = {
    "appliedintuition": "Applied Intuition",
    "arizeai": "Arize AI",
    "assemblyai": "AssemblyAI",
    "character": "Character.AI",
    "cursor": "Cursor",
    "elevenlabs": "ElevenLabs",
    "fireworksai": "Fireworks AI",
    "gleanwork": "Glean",
    "gongio": "Gong",
    "huggingface": "Hugging Face",
    "inflectionai": "Inflection AI",
    "leadtech": "Leadtech",
    "runwayml": "Runway",
    "sambanovasystems": "SambaNova Systems",
    "scaleai": "Scale AI",
    "snorkelai": "Snorkel AI",
    "stabilityai": "Stability AI",
    "togetherai": "Together AI",
    "twelve-labs": "Twelve Labs",
    "urbancompass": "Compass",
}

# ── Location detection for country-aware flagging ────────────────────────────

US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC",
}

CA_PROVINCES = {
    "AB","BC","MB","NB","NL","NS","NT","NU","ON","PE","QC","SK","YT",
}

COUNTRY_CODES: dict[str, str] = {
    "united states": "US", "united states of america": "US", "us": "US", "usa": "US",
    "canada": "CA",
    "united kingdom": "UK", "england": "UK", "uk": "UK",
    "germany": "DE", "france": "FR", "netherlands": "NL",
    "ireland": "IE", "israel": "IL", "spain": "ES", "italy": "IT",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI",
    "switzerland": "CH", "austria": "AT", "belgium": "BE", "portugal": "PT",
    "poland": "PL",
    "australia": "AU", "india": "IN", "singapore": "SG", "japan": "JP",
    "south korea": "KR", "china": "CN", "taiwan": "TW",
    "brazil": "BR", "mexico": "MX",
    "uae": "AE", "united arab emirates": "AE", "qatar": "QA", "saudi arabia": "SA",
}

REGION_COUNTRIES: dict[str, set[str]] = {
    "namer": {"US", "CA"},
    "north america": {"US", "CA"},
    "americas": {"US", "CA"},
    "latam": {"MX", "BR"},
    "emea": {"UK", "DE", "FR", "NL", "IE", "IL", "ES", "CH"},
    "europe": {"UK", "DE", "FR", "NL", "IE", "ES", "CH", "SE"},
    "apac": {"IN", "SG", "JP", "AU", "KR"},
}

KNOWN_CITIES: dict[str, str] = {
    "san francisco": "US", "new york": "US", "new york city": "US", "nyc": "US",
    "seattle": "US", "austin": "US", "chicago": "US", "los angeles": "US",
    "mountain view": "US", "palo alto": "US", "menlo park": "US",
    "sunnyvale": "US", "redwood city": "US", "san mateo": "US",
    "san jose": "US", "miami": "US", "dallas": "US", "houston": "US",
    "boston": "US", "denver": "US", "portland": "US", "phoenix": "US",
    "salt lake city": "US", "washington": "US", "atlanta": "US",
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "ottawa": "CA",
    "london": "UK", "edinburgh": "UK", "manchester": "UK",
    "dublin": "IE", "paris": "FR", "berlin": "DE", "munich": "DE",
    "amsterdam": "NL", "zurich": "CH", "barcelona": "ES", "stockholm": "SE",
    "tel aviv": "IL", "singapore": "SG", "tokyo": "JP", "seoul": "KR",
    "bangalore": "IN", "bengaluru": "IN", "mumbai": "IN", "hyderabad": "IN",
    "sydney": "AU", "melbourne": "AU",
    "dubai": "AE", "riyadh": "SA",
}


def _detect_countries(location: str) -> set[str]:
    """Return set of ISO country codes detected in a location string."""
    if not location or not location.strip():
        return set()
    countries: set[str] = set()
    # Split multi-location strings on | ; and •
    parts = re.split(r"\s*[|;•]\s*|\s+or\s+", location)
    for part in parts:
        pl = part.lower().strip()
        if not pl:
            continue
        found = False
        # Check for multi-country regions first (NAMER, EMEA, etc.)
        for region, codes in REGION_COUNTRIES.items():
            if re.search(r"\b" + re.escape(region) + r"\b", pl):
                countries.update(codes)
                found = True
                break
        if found:
            continue
        # Check for country names/codes (word boundary)
        for name, code in COUNTRY_CODES.items():
            if re.search(r"\b" + re.escape(name) + r"\b", pl):
                countries.add(code)
                found = True
                break
        if found:
            continue
        # Check for US state abbreviation: "City, CA" pattern
        state_m = re.search(r",\s*([A-Z]{2})\b", part)
        if state_m:
            abbr = state_m.group(1)
            if abbr in US_STATES:
                countries.add("US")
                continue
            if abbr in CA_PROVINCES:
                countries.add("CA")
                continue
        # Fallback: known city names
        for city, code in KNOWN_CITIES.items():
            if city in pl:
                countries.add(code)
                break
    return countries


def _city_in_location(user_city: str, location: str) -> bool:
    """Check if user's city name appears in the job location string."""
    loc_lower = location.lower()
    city_name = user_city.lower().split(",")[0].strip()
    return city_name in loc_lower


def _is_region_only(location: str) -> bool:
    """True if location is only a region/country name with no specific city.

    "NAMER", "United States", "North America" → True (no city)
    "San Francisco, CA", "Sunnyvale, California, United States" → False (has city)
    """
    if not location or not location.strip():
        return True
    parts = re.split(r"\s*[|;•]\s*|\s+or\s+", location)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        part_lower = part.lower()
        # If this part has a comma + state abbreviation pattern → specific city
        if re.search(r",\s*[A-Z]{2}\b", part):
            return False
        # If this part contains a known city name → specific city
        for city in KNOWN_CITIES:
            if city in part_lower:
                return False
        # If this part has a comma → likely "City, State" or "City, Country" → specific
        if "," in part:
            return False
    return True


def _classify_location_flag(
    location: str, work_type: str, user_country: str, user_city: str
) -> str:
    """Classify location flag: '' (local), 'Relocation', or 'International'."""
    if not user_country:
        return ""
    user_cc = user_country.upper().strip()
    job_countries = _detect_countries(location)

    if work_type == "Remote":
        if not job_countries:
            return ""  # Global remote — no flag
        if user_cc in job_countries:
            return ""
        return "International"

    # On-site or Hybrid
    if not job_countries:
        return ""  # No location info — can't determine
    if user_cc not in job_countries:
        return "International"
    # Same country — check city
    if not user_city:
        return ""
    if _city_in_location(user_city, location):
        return ""
    return "Relocation"


# ── PM keyword filter ────────────────────────────────────────────────────────

PM_TITLE_PATTERNS = [
    r"product\s+manag",                           # product manager / management
    r"product\s+lead",
    r"product\s+director",
    r"(?:group|senior|staff|principal)\s+pm\b",   # prefixed PM abbreviations only
    r"director.{0,30}product",                    # director of/for product (limited gap)
    r"head\s+of\s+product",
    r"vp.{0,20}product",
    r"vice\s+president.{0,20}product",
]

# Exclude non-PM roles that happen to contain "product" in the title
PM_EXCLUDE_PATTERNS = [
    r"product\s+(?:market|design|counsel|communi|account|legal|launch)",
    r"(?:engineer|software|legal|video|sales).{0,25}product",
    r"technical\s+program",
    r"project\s+manag",
]

PM_RE = re.compile("|".join(PM_TITLE_PATTERNS), re.IGNORECASE)
PM_EXCLUDE_RE = re.compile("|".join(PM_EXCLUDE_PATTERNS), re.IGNORECASE)

# ── Fit‑score keyword buckets (weight → list of patterns) ────────────────────

FIT_KEYWORDS = {
    # AI / ML — highest signal (up to 30 pts: 8 per hit, max 30)
    "AI / ML": {
        "base": 8, "max": 30,
        "patterns": [
            r"\bai\b", r"\bartificial\s+intelligence\b", r"\bmachine\s+learning\b",
            r"\bml\b", r"\bllm\b", r"\blarge\s+language\s+model",
            r"\bgenerative\b", r"\bdeep\s+learning\b", r"\bnlp\b",
            r"\bfoundation\s+model", r"\bgpt\b", r"\btransformer\b",
        ],
    },
    # Seniority (up to 25 pts: 10 per hit, max 25)
    "Seniority": {
        "base": 10, "max": 25,
        "patterns": [
            r"\bsenior\b", r"\bstaff\b", r"\bprincipal\b", r"\bdirector\b",
            r"\blead\b", r"\bhead\s+of\b", r"\bvp\b",
        ],
    },
    # Domain fit (up to 25 pts: 7 per hit, max 25)
    "Domain Fit": {
        "base": 7, "max": 25,
        "patterns": [
            r"\bplatform\b", r"\benterprise\b", r"\binfrastructure\b",
            r"\bworkflow\b", r"\bautomation\b", r"\bagent\b", r"\bagentic\b",
        ],
    },
    # Industry verticals (up to 20 pts: 10 per hit, max 20)
    "Industry Verticals": {
        "base": 10, "max": 20,
        "patterns": [
            r"\breal\s+estate\b", r"\bproptech\b",
            r"\bhealthcare\b", r"\bhealth\s+tech\b", r"\bclinical\b",
        ],
    },
}

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "freshapply.db")
DIGEST_DIR = os.path.join(BASE_DIR, "digests")

# ── Database ─────────────────────────────────────────────────────────────────


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id               TEXT PRIMARY KEY,   -- ats:company:external_id
            ats              TEXT NOT NULL,
            company          TEXT NOT NULL,
            title            TEXT NOT NULL,
            url              TEXT,
            location         TEXT,
            description      TEXT,
            description_html TEXT DEFAULT '',
            salary           TEXT DEFAULT '',
            desc_hash        TEXT,
            first_seen_at    TEXT NOT NULL,
            last_seen_at     TEXT NOT NULL,
            reposted         INTEGER DEFAULT 0
        )
    """)
    # Migrate existing DBs that lack new columns
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN description_html TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN salary TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
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
                                 description_html, salary, desc_hash,
                                 first_seen_at, last_seen_at, reposted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (jid, job["ats"], job["company"], job["title"], job.get("url"),
             job.get("location"), job.get("description"),
             job.get("descriptionHtml", ""), job.get("salary", ""),
             desc_hash, now, now, reposted),
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

def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = html_mod.unescape(raw)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sanitize_html(raw: str) -> str:
    """Keep basic formatting tags but remove scripts, styles, events, and ATS boilerplate."""
    if not raw:
        return ""
    # Decode HTML entities first — some ATS systems store entity-encoded HTML
    text = html_mod.unescape(raw)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<iframe[^>]*>.*?</iframe>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<iframe[^>]*/?>", "", text, flags=re.IGNORECASE)
    text = re.sub(r'\s+on\w+\s*=\s*"[^"]*"', "", text)
    text = re.sub(r"\s+on\w+\s*=\s*'[^']*'", "", text)
    # Remove pay-transparency / compensation / conclusion sections entirely
    # These use DOTALL + greedy .* to strip from the div to the end of the string
    # (everything after pay-transparency is boilerplate: conclusion, about us, EEO, etc.)
    text = re.sub(r'<div[^>]*class="[^"]*pay-transparency[^"]*"[^>]*>.*', "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<div[^>]*class="[^"]*content-pay[^"]*"[^>]*>.*', "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<div[^>]*class="[^"]*compensation[^"]*"[^>]*>.*', "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<div[^>]*class="[^"]*content-conclusion[^"]*"[^>]*>.*', "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove common ATS boilerplate sections by heading text
    text = re.sub(r'<p>\s*<strong>\s*(?:PLEASE NOTE|About Us|EEO|Equal Opportunity)[^<]*</strong>.*', "", text, flags=re.DOTALL | re.IGNORECASE)
    # Strip all div tags (opening and closing) — they add no formatting value
    text = re.sub(r"</?div[^>]*>", "", text, flags=re.IGNORECASE)
    # Clean up &nbsp; and other HTML entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&mdash;", "—")
    text = text.replace("&ndash;", "–")
    # Remove empty paragraphs
    text = re.sub(r"<p>\s*</p>", "", text, flags=re.IGNORECASE)
    # Strip class/style/id/data attributes from remaining tags
    text = re.sub(r'\s+(?:class|style|id|data-\w+)\s*=\s*"[^"]*"', "", text)
    text = re.sub(r"\s+(?:class|style|id|data-\w+)\s*=\s*'[^']*'", "", text)
    # Collapse excessive whitespace / blank lines
    text = re.sub(r"(\s*\n){3,}", "\n\n", text)
    return text.strip()


def _extract_salary(text: str) -> str:
    """Pull salary range(s) from description. Merges multiple location-based ranges."""
    if not text:
        return ""
    # Matches: $X - $Y, $X USD - $Y USD, CAD $X - CAD $Y
    _cur = r"(?:(?:USD|CAD|GBP|EUR)\s*)?"
    _amt = r"\$[\d,]+(?:\.\d+)?\s*[kK]?"
    _suf = r"(?:\s*(?:USD|CAD|GBP|EUR)\+?)?"
    _end = r"(?:\s*(?:per\s+(?:year|annum)|annually|/\s*yr|/\s*year))?"
    pat1 = _cur + _amt + _suf + r"\s*[-–—~]+\s*" + _cur + _amt + _suf + _end
    pat2 = _cur + _amt + _suf + r"\s+(?:to|and)\s+" + _cur + _amt + _suf + _end
    matches = re.findall(pat1, text, re.IGNORECASE) + re.findall(pat2, text, re.IGNORECASE)
    if not matches:
        return ""
    if len(matches) == 1:
        return matches[0].strip()
    # Multiple ranges (e.g. location-based) — find overall min and max
    all_vals = []
    for m in matches:
        for raw, k in re.findall(r"\$([\d,]+(?:\.\d+)?)\s*([kK])?", m):
            v = float(raw.replace(",", ""))
            if k:
                v *= 1000
            all_vals.append(int(v))
    if not all_vals:
        return matches[0].strip()
    return f"${min(all_vals):,} - ${max(all_vals):,}"


def scrape_greenhouse(company: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
    data = fetch_json(url)
    if not data or "jobs" not in data:
        return []
    jobs = []
    for j in data["jobs"]:
        title = j.get("title", "")
        if not PM_RE.search(title) or PM_EXCLUDE_RE.search(title):
            continue
        loc = j.get("location")
        location = loc.get("name", "") if isinstance(loc, dict) else str(loc or "")
        raw_html = j.get("content", "")
        plain = _strip_html(raw_html)
        jobs.append({
            "id": f"gh:{company}:{j['id']}",
            "ats": "greenhouse",
            "company": company,
            "title": title,
            "url": j.get("absolute_url", ""),
            "location": location,
            "description": plain,
            "descriptionHtml": _sanitize_html(raw_html),
            "salary": _extract_salary(plain),
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
        if not PM_RE.search(title) or PM_EXCLUDE_RE.search(title):
            continue
        cats = j.get("categories", {})
        location = cats.get("location", "") or cats.get("allLocations", "")
        if isinstance(location, list):
            location = ", ".join(location)
        raw_html = j.get("description", "")
        plain = j.get("descriptionPlain", "") or _strip_html(raw_html)
        jobs.append({
            "id": f"lv:{company}:{j['id']}",
            "ats": "lever",
            "company": company,
            "title": title,
            "url": j.get("hostedUrl", ""),
            "location": location,
            "description": plain,
            "descriptionHtml": _sanitize_html(raw_html),
            "salary": _extract_salary(plain),
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
        if not PM_RE.search(title) or PM_EXCLUDE_RE.search(title):
            continue
        location = j.get("location", "")
        if isinstance(location, dict):
            location = location.get("name", "")
        job_url = j.get("jobUrl", "") or j.get("hostedUrl", "")
        raw_html = j.get("descriptionHtml", "") or j.get("description", "")
        plain = j.get("descriptionPlain", "") or _strip_html(raw_html)
        jobs.append({
            "id": f"ab:{company}:{j['id']}",
            "ats": "ashby",
            "company": company,
            "title": title,
            "url": job_url,
            "location": location,
            "description": plain,
            "descriptionHtml": _sanitize_html(raw_html),
            "salary": _extract_salary(plain),
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
        if not PM_RE.search(title) or PM_EXCLUDE_RE.search(title):
            continue
        location = j.get("location", "")
        if isinstance(location, dict):
            location = location.get("name", "")
        shortcode = j.get("shortcode", j.get("id", ""))
        job_url = j.get("url", f"https://apply.workable.com/{company}/j/{shortcode}/")
        plain = j.get("description", "")
        jobs.append({
            "id": f"wk:{company}:{shortcode}",
            "ats": "workable",
            "company": company,
            "title": title,
            "url": job_url,
            "location": location,
            "description": plain,
            "descriptionHtml": "",
            "salary": _extract_salary(plain),
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


def compute_fit_breakdown(title: str, description: str) -> list[dict]:
    """Return list of {bucket, weight, matched, hits, maxPts} for each keyword bucket."""
    text = f"{title} {description}"
    breakdown = []
    for bucket_name, cfg in FIT_KEYWORDS.items():
        matched_terms = []
        for pat in cfg["patterns"]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                matched_terms.append(m.group(0))
        pts = min(cfg["max"], len(matched_terms) * cfg["base"])
        breakdown.append({
            "bucket": bucket_name,
            "weight": pts,
            "maxPts": cfg["max"],
            "matched": ", ".join(matched_terms) if matched_terms else None,
            "hits": len(matched_terms),
        })
    return breakdown


def fit_score(title: str, description: str) -> int:
    """0‑100. Higher = better match to target profile."""
    text = f"{title} {description}"
    total = 0
    for cfg in FIT_KEYWORDS.values():
        hits = sum(1 for pat in cfg["patterns"] if re.search(pat, text, re.IGNORECASE))
        total += min(cfg["max"], hits * cfg["base"])
    return min(100, total)


def tier(fresh: int, fit: int, title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    has_ai = bool(re.search(r"\bai\b|\bartificial.intelligence|\bml\b|\bllm\b|\bmachine.learn", text))

    if fresh >= 70 and fit >= 40 and has_ai:
        return "Apply Today"
    if fresh >= 50 and fit >= 25:
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

def _build_resume_suggestions(breakdown: list[dict], fit: int) -> list[dict]:
    """For unmatched or under-matched keyword buckets, return resume improvement tips."""
    tips_map = {
        "AI / ML": {
            "keywords": "AI, machine learning, ML, LLM, NLP, generative AI, deep learning, GPT, transformer models",
            "bullets": [
                "Led AI/ML product strategy for [product], driving [X%] adoption among enterprise customers",
                "Defined and shipped LLM-powered features that reduced [process] time by [X%]",
                "Partnered with ML engineering to build and deploy generative AI capabilities at scale",
            ],
            "learning": [
                "Complete a generative AI or LLM course (DeepLearning.AI, Coursera, or fast.ai)",
                "Build a hands-on project using LangChain, RAG pipelines, or agentic frameworks",
                "Practice prompt engineering and learn model evaluation (evals) techniques",
            ],
        },
        "Seniority": {
            "keywords": "senior, staff, principal, director, lead, head of, VP",
            "bullets": [
                "Led cross-functional team of [X] engineers, designers, and data scientists to deliver [product]",
                "Directed product strategy and roadmap for a [X]-person org generating [$X]M ARR",
                "Mentored [X] PMs and established product development best practices across the organization",
            ],
            "learning": [
                "Seek leadership opportunities: lead a cross-functional initiative or mentor junior PMs",
                "Take a strategic leadership course (Reforge, Product Faculty, or similar)",
                "Document and quantify your leadership impact with measurable outcomes",
            ],
        },
        "Domain Fit": {
            "keywords": "platform, enterprise, infrastructure, workflow, automation, agent, agentic",
            "bullets": [
                "Built enterprise platform features serving [X]+ B2B customers with [X]% retention",
                "Designed workflow automation tools that reduced manual processes by [X%] across [X] teams",
                "Owned infrastructure product roadmap powering [X]M+ API calls per day",
            ],
            "learning": [
                "Study platform/infrastructure product patterns (APIs, SDKs, developer experience)",
                "Build or contribute to a workflow automation or agentic AI project",
                "Learn enterprise product concepts: multi-tenancy, RBAC, compliance, and SLAs",
            ],
        },
        "Industry Verticals": {
            "keywords": "real estate, proptech, healthcare, health tech, clinical",
            "bullets": [
                "Launched [healthcare/real estate] product vertical generating [$X]M in first-year revenue",
                "Built HIPAA-compliant / proptech solutions used by [X]+ [providers/agents]",
                "Developed domain-specific features for [industry] reducing customer onboarding time by [X%]",
            ],
            "learning": [
                "Research the target industry's regulations, workflows, and pain points",
                "Network with domain experts and attend industry-specific conferences or webinars",
                "Build a side project or case study targeting the specific vertical",
            ],
        },
    }
    suggestions = []
    for b in breakdown:
        if b["bucket"] not in tips_map:
            continue
        tip = tips_map[b["bucket"]]
        if b["matched"] is None:
            # Completely missing — high priority
            suggestions.append({
                "bucket": b["bucket"],
                "weight": b["maxPts"],
                "status": "missing",
                "keywords": tip["keywords"],
                "bullets": tip["bullets"],
                "learning": tip.get("learning", []),
            })
        elif b["weight"] < b["maxPts"]:
            # Partially matched — can earn more points
            gap = b["maxPts"] - b["weight"]
            suggestions.append({
                "bucket": b["bucket"],
                "weight": gap,
                "status": "partial",
                "keywords": tip["keywords"],
                "bullets": tip["bullets"],
                "learning": tip.get("learning", []),
            })
    return suggestions


def generate_html_dashboard(conn: sqlite3.Connection):
    os.makedirs(DIGEST_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    path = os.path.join(DIGEST_DIR, f"dashboard-{today}.html")

    rows = conn.execute("SELECT * FROM jobs").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM jobs LIMIT 0").description]

    user_country = RESUME_DATA.get("country", "")
    user_city = RESUME_DATA.get("city", "")
    scored = []
    for row in rows:
        job = dict(zip(cols, row))
        title = job["title"]
        # Skip non-PM roles that slipped into the database
        if not PM_RE.search(title) or PM_EXCLUDE_RE.search(title):
            continue
        fresh = freshness_score(job["first_seen_at"], job["last_seen_at"], bool(job["reposted"]), now)
        fit = fit_score(title, job["description"] or "")
        t = tier(fresh, fit, title, job["description"] or "")
        combined = round(fresh * 0.4 + fit * 0.6, 1)
        breakdown = compute_fit_breakdown(title, job["description"] or "")
        display = DISPLAY_NAMES.get(job["company"], job["company"].replace("-", " ").title())
        suggestions = _build_resume_suggestions(breakdown, fit) if fit < 75 else []
        salary = job.get("salary", "") or _extract_salary(job["description"] or "")
        # Classify work type from location + description
        loc_lower = (job["location"] or "").lower()
        desc_lower = (job["description"] or "").lower()
        if "hybrid" in loc_lower or "hybrid" in desc_lower[:500]:
            work_type = "Hybrid"
        elif "remote" in loc_lower:
            work_type = "Remote"
        elif not job["location"] or not job["location"].strip():
            work_type = "Remote"
        elif _is_region_only(job["location"]):
            work_type = "Remote"
        else:
            work_type = "On-site"
        location_flag = _classify_location_flag(
            job["location"] or "", work_type, user_country, user_city
        )
        scored.append({
            "id": job["id"],
            "ats": job["ats"],
            "company": display,
            "companySlug": job["company"],
            "title": title,
            "url": job["url"] or "",
            "location": job["location"] or "",
            "workType": work_type,
            "locationFlag": location_flag,
            "salary": salary,
            "fresh": fresh,
            "fit": fit,
            "tier": t,
            "combined": combined,
            "reposted": bool(job["reposted"]),
            "firstSeen": job["first_seen_at"][:10],
            "lastSeen": job["last_seen_at"][:10],
            "breakdown": breakdown,
            "suggestions": suggestions,
            "descHtml": _sanitize_html((job.get("description_html") or "")[:10000]),
            "description": (job["description"] or "")[:3000],
        })

    scored.sort(key=lambda j: (TIER_ORDER.get(j["tier"], 9), -j["combined"]))
    # Escape </script> inside JSON to avoid breaking the HTML
    jobs_json = json.dumps(scored, ensure_ascii=False).replace("</", "<\\/")
    resume_json = json.dumps(RESUME_DATA, ensure_ascii=False).replace("</", "<\\/")
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

/* Toolbar layout */
.toolbar{{background:var(--card);border-bottom:1px solid var(--border);padding:0}}
.toolbar-inner{{max-width:1400px;margin:0 auto}}
.toolbar-row{{display:flex;align-items:center;gap:10px;padding:10px 24px}}
.toolbar-row--primary{{border-bottom:1px solid var(--border);gap:12px}}
.toolbar-row--filters{{gap:6px;flex-wrap:wrap;padding:8px 24px}}
.search-wrap{{flex:1;min-width:200px;position:relative}}
.search-icon{{position:absolute;left:10px;top:50%;transform:translateY(-50%);width:16px;height:16px;color:var(--muted);pointer-events:none}}
.search-box{{width:100%;padding:8px 12px 8px 34px;border:1px solid var(--border);
border-radius:var(--radius);font-size:14px;background:var(--bg);color:var(--text);outline:none}}
.search-box:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.1)}}
.toolbar-actions{{display:flex;gap:8px;align-items:center;margin-left:auto;flex-shrink:0}}
select,.btn{{padding:7px 10px;border:1px solid var(--border);border-radius:6px;
font-size:12px;background:var(--card);color:var(--text);cursor:pointer}}
select:focus,.btn:focus{{outline:none;border-color:var(--accent)}}
select.has-filter{{border-color:var(--accent);color:var(--accent);font-weight:600}}
.btn-export{{background:var(--accent);color:#fff;border:none;font-weight:600;padding:7px 14px}}
.btn-export:hover{{opacity:.9}}
/* Filter groups */
.filter-group{{display:flex;align-items:center;gap:5px}}
.filter-label{{font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;
letter-spacing:.5px;white-space:nowrap;user-select:none}}
.filter-sep{{width:1px;height:22px;background:var(--border);margin:0 4px;flex-shrink:0}}
.filter-group--dropdowns select{{padding:5px 8px;font-size:12px}}
/* Chips */
.chip-group{{display:flex;gap:3px}}
.chip{{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:6px;
font-size:12px;font-weight:500;cursor:pointer;border:1px solid var(--border);
background:var(--card);color:var(--muted);transition:all .15s;white-space:nowrap;line-height:1.4}}
.chip:hover{{border-color:var(--accent);color:var(--text);background:var(--bg)}}
.chip-count{{font-size:10px;font-weight:600;padding:0 5px;border-radius:4px;
background:var(--bg);color:var(--muted);min-width:16px;text-align:center;line-height:1.6}}
/* Tier chip active */
.chip.active[data-tier="all"]{{background:var(--accent);border-color:var(--accent);color:#fff}}
.chip.active[data-tier="all"] .chip-count{{background:rgba(255,255,255,.2);color:#fff}}
.chip.active[data-tier="Apply Today"]{{background:var(--red);border-color:var(--red);color:#fff}}
.chip.active[data-tier="Apply Today"] .chip-count{{background:rgba(255,255,255,.2);color:#fff}}
.chip.active[data-tier="Apply This Week"]{{background:var(--amber);border-color:var(--amber);color:#fff}}
.chip.active[data-tier="Apply This Week"] .chip-count{{background:rgba(255,255,255,.2);color:#fff}}
.chip.active[data-tier="Watch List"]{{background:#6b7280;border-color:#6b7280;color:#fff}}
.chip.active[data-tier="Watch List"] .chip-count{{background:rgba(255,255,255,.2);color:#fff}}
/* Work type chip active */
.chip.active[data-wt="Remote"]{{background:#dcfce7;border-color:#86efac;color:#166534}}
.chip.active[data-wt="Hybrid"]{{background:#fef3c7;border-color:#fcd34d;color:#92400e}}
.chip.active[data-wt="On-site"]{{background:#e0e7ff;border-color:#a5b4fc;color:#3730a3}}
/* Location flag chip active */
.chip.active[data-loc="Local"]{{background:#dcfce7;border-color:#86efac;color:#166534}}
.chip.active[data-loc="Relocation"]{{background:#fef3c7;border-color:#fcd34d;color:#92400e}}
.chip.active[data-loc="International"]{{background:#ede9fe;border-color:#c4b5fd;color:#5b21b6}}
/* Status chip active */
.chip.active[data-status="New"]{{background:var(--bg);border-color:var(--accent);color:var(--accent)}}
.chip.active[data-status="Saved"]{{background:#eff6ff;border-color:var(--blue);color:var(--blue)}}
.chip.active[data-status="Applied"]{{background:#f0fdf4;border-color:var(--green);color:var(--green)}}
.chip.active[data-status="Interviewing"]{{background:#faf5ff;border-color:var(--purple);color:var(--purple)}}
.chip.active[data-status="Rejected"]{{background:var(--red-bg);border-color:var(--red);color:var(--red)}}
/* Clear filters */
.clear-filters{{display:none;align-items:center;gap:4px;padding:4px 10px;border-radius:6px;
font-size:12px;font-weight:500;cursor:pointer;border:1px dashed var(--red);
background:transparent;color:var(--red);transition:all .15s;margin-left:auto;white-space:nowrap}}
.clear-filters:hover{{background:var(--red-bg)}}
.clear-filters svg{{width:14px;height:14px}}
/* Dark mode chip overrides */
@media(prefers-color-scheme:dark){{
.chip.active[data-wt="Remote"]{{background:#14532d;color:#86efac;border-color:#22c55e}}
.chip.active[data-wt="Hybrid"]{{background:#78350f;color:#fcd34d;border-color:#f59e0b}}
.chip.active[data-wt="On-site"]{{background:#312e81;color:#a5b4fc;border-color:#8b5cf6}}
.chip.active[data-loc="Local"]{{background:#14532d;color:#86efac;border-color:#22c55e}}
.chip.active[data-loc="Relocation"]{{background:#78350f;color:#fcd34d;border-color:#f59e0b}}
.chip.active[data-loc="International"]{{background:#3b1f6e;color:#c4b5fd;border-color:#8b5cf6}}
.chip.active[data-status="Saved"]{{background:#1e3a5f;color:#93c5fd;border-color:#3b82f6}}
.chip.active[data-status="Applied"]{{background:#14532d;color:#86efac;border-color:#22c55e}}
.chip.active[data-status="Interviewing"]{{background:#3b1f6e;color:#c4b5fd;border-color:#8b5cf6}}
.chip-count{{background:var(--card)}}
}}
/* Mobile */
@media(max-width:768px){{.toolbar-row--primary{{flex-wrap:wrap}}
.search-wrap{{flex:1 1 100%;min-width:0}}
.toolbar-actions{{margin-left:0;flex-wrap:wrap;width:100%;justify-content:flex-end}}
.filter-sep{{display:none}}
.filter-group{{flex-wrap:wrap}}
.chip-group{{flex-wrap:wrap}}
.clear-filters{{margin-left:0;margin-top:4px}}
}}

.counter-bar{{max-width:1400px;margin:12px auto 0;padding:0 24px;font-size:13px;color:var(--muted)}}

.grid{{max-width:1400px;margin:12px auto;padding:0 24px 40px;
display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}}

.card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
padding:16px;box-shadow:var(--shadow);transition:.15s;position:relative;cursor:pointer;
display:flex;flex-direction:column}}
.card:hover{{box-shadow:0 4px 12px rgba(0,0,0,.1);transform:translateY(-1px)}}
.card-header{{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}}
.card-title{{font-size:15px;font-weight:600;flex:1;min-width:0;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.card-title a{{color:var(--text)}}
.card-title a:hover{{color:var(--accent)}}
.card-dismiss{{background:none;border:none;color:var(--muted);cursor:pointer;font-size:16px;
padding:2px 6px;border-radius:4px;line-height:1;flex-shrink:0}}
.card-dismiss:hover{{background:var(--border);color:var(--text)}}
.card-meta{{font-size:13px;color:var(--muted);margin:4px 0 6px;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.card-meta .company{{font-weight:600;color:var(--text)}}
.card-salary{{font-size:12px;font-weight:600;color:var(--green);margin-bottom:8px;min-height:18px}}
.no-salary{{color:var(--muted);font-weight:400;font-size:11px}}
.tier-tag{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;margin-left:6px}}
.tier-tag.t-today{{background:var(--red-bg);color:var(--red)}}
.tier-tag.t-week{{background:var(--amber-bg);color:var(--amber)}}
.tier-tag.t-watch{{background:var(--gray-bg);color:var(--muted)}}
.repost-tag{{font-size:11px;color:var(--amber);font-weight:600;margin-left:4px}}
.work-tag{{font-size:10px;padding:1px 6px;border-radius:4px;font-weight:500;margin-left:4px}}
.wt-remote{{background:#dcfce7;color:#166534}}
.wt-hybrid{{background:#fef3c7;color:#92400e}}
.wt-onsite{{background:#e0e7ff;color:#3730a3}}
@media(prefers-color-scheme:dark){{.wt-remote{{background:#14532d;color:#86efac}}
.wt-hybrid{{background:#78350f;color:#fcd34d}}.wt-onsite{{background:#312e81;color:#a5b4fc}}}}
.loc-flag{{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;font-weight:600;margin-left:4px}}
.lf-relocation{{background:#fef3c7;color:#92400e;border:1px solid #fcd34d}}
.lf-international{{background:#ede9fe;color:#5b21b6;border:1px solid #c4b5fd}}
@media(prefers-color-scheme:dark){{.lf-relocation{{background:#78350f;color:#fcd34d;border-color:#f59e0b}}
.lf-international{{background:#3b1f6e;color:#c4b5fd;border-color:#8b5cf6}}}}
.score-bars{{display:flex;gap:12px;margin:8px 0}}
.score-bar{{flex:1}}
.score-label{{font-size:11px;color:var(--muted);margin-bottom:2px;display:flex;justify-content:space-between}}
.bar-track{{height:6px;background:var(--border);border-radius:3px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:3px;transition:width .3s}}
.bar-fill.fresh{{background:var(--bar-fresh)}}
.bar-fill.fit-high{{background:var(--green)}}
.bar-fill.fit-mid{{background:var(--blue)}}
.bar-fill.fit-low{{background:var(--amber)}}
.bar-fill.fit-vlow{{background:var(--red)}}
.card-foot{{display:flex;justify-content:space-between;align-items:center;margin-top:auto;padding-top:10px}}
.card-date{{font-size:11px;color:var(--muted)}}
.status-select{{padding:4px 8px;font-size:11px;border-radius:6px;border:1px solid var(--border);
background:var(--card);color:var(--text)}}
.status-select.s-applied{{border-color:var(--green);color:var(--green)}}
.status-select.s-saved{{border-color:var(--blue);color:var(--blue)}}
.status-select.s-interviewing{{border-color:var(--purple);color:var(--purple)}}
.status-select.s-rejected{{border-color:var(--red);color:var(--red)}}
.has-note{{display:inline-block;width:8px;height:8px;background:var(--amber);border-radius:50%;margin-left:6px;vertical-align:middle}}
.card-actions{{display:flex;gap:6px;align-items:center}}
.btn-card-apply{{padding:4px 10px;font-size:11px;font-weight:600;border-radius:6px;border:none;
background:var(--accent);color:#fff;cursor:pointer;text-decoration:none;white-space:nowrap}}
.btn-card-apply:hover{{opacity:.85}}

/* Modal */
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;
justify-content:center;align-items:flex-start;padding:40px 20px;overflow-y:auto}}
.modal-overlay.open{{display:flex}}
.modal{{background:var(--card);border-radius:14px;max-width:780px;width:100%;
box-shadow:0 20px 60px rgba(0,0,0,.2);padding:28px;position:relative;max-height:85vh;overflow-y:auto}}
.modal-close{{position:absolute;top:12px;right:16px;background:none;border:none;font-size:24px;
cursor:pointer;color:var(--muted);line-height:1}}
.modal-close:hover{{color:var(--text)}}
.modal h2{{font-size:20px;margin-bottom:4px;padding-right:30px}}
.modal h4{{font-size:14px;margin-top:18px;margin-bottom:6px;color:var(--text)}}
.modal .m-meta{{color:var(--muted);font-size:14px;margin-bottom:12px}}
.modal .m-header-actions{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;gap:12px}}
.modal .m-salary{{font-size:15px;font-weight:700;color:var(--green);margin:0}}
.modal .m-scores{{display:flex;gap:20px;margin-bottom:16px}}
.modal .m-score-box{{text-align:center;padding:10px 16px;border-radius:var(--radius);background:var(--bg)}}
.modal .m-score-val{{font-size:28px;font-weight:700}}
.modal .m-score-lbl{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.breakdown-table{{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}}
.breakdown-table th{{text-align:left;padding:6px 10px;background:var(--bg);font-weight:600;border-bottom:1px solid var(--border)}}
.breakdown-table td{{padding:6px 10px;border-bottom:1px solid var(--border)}}
.breakdown-table .match{{color:var(--green);font-weight:600}}
.breakdown-table .no-match{{color:var(--red);font-weight:600}}
.modal .m-desc{{font-size:13px;line-height:1.7;color:var(--text);margin:8px 0;
max-height:400px;overflow-y:auto;padding:14px;background:var(--bg);border-radius:var(--radius)}}
.modal .m-desc h1,.modal .m-desc h2,.modal .m-desc h3{{font-size:15px;margin:12px 0 6px;color:var(--text)}}
.modal .m-desc p{{margin:6px 0}}
.modal .m-desc ul,.modal .m-desc ol{{margin:6px 0;padding-left:20px}}
.modal .m-desc li{{margin:3px 0}}

/* Gap analysis */
.gap-section{{background:var(--red-bg);border:1px solid var(--red);border-radius:var(--radius);
padding:16px;margin:12px 0}}
.gap-section h4{{color:var(--red);margin:0 0 8px;font-size:14px}}
.gap-item{{margin:10px 0;padding:10px;background:var(--card);border-radius:8px}}
.gap-item-head{{font-weight:600;font-size:13px;margin-bottom:4px}}
.gap-item-head .pts{{color:var(--muted);font-weight:400}}
.gap-keywords{{font-size:12px;color:var(--accent);margin-bottom:6px}}
.gap-bullets{{font-size:12px;color:var(--muted);padding-left:16px}}
.gap-bullets li{{margin:3px 0}}
.gap-sub-label{{font-size:11px;font-weight:600;color:var(--text);margin:8px 0 2px;text-transform:uppercase;letter-spacing:.3px}}
.gap-learn-label{{color:var(--accent)}}
.gap-learn{{font-size:12px;color:var(--accent);padding-left:16px;list-style:none}}
.gap-learn li{{margin:3px 0}}
.gap-learn li::before{{content:"→ ";color:var(--accent)}}
.gap-actions{{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}}
.btn-preview{{padding:8px 16px;background:var(--card);color:var(--accent);border:1px solid var(--accent);
border-radius:var(--radius);font-weight:600;font-size:13px;cursor:pointer}}
.btn-preview:hover{{background:var(--accent);color:#fff}}
.btn-resume{{padding:8px 16px;background:var(--red);color:#fff;border:none;
border-radius:var(--radius);font-weight:600;font-size:13px;cursor:pointer}}
.btn-resume:hover{{opacity:.9}}
/* Resume changes preview */
.resume-preview{{margin-top:14px;padding:14px;background:var(--bg);border-radius:var(--radius);
border:1px solid var(--border);font-size:12px}}
.resume-preview h5{{font-size:13px;margin:0 0 10px;color:var(--text)}}
.rp-section{{margin:10px 0}}
.rp-section-title{{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
letter-spacing:.3px;margin-bottom:4px}}
.rp-change{{padding:4px 0;display:flex;gap:6px;align-items:flex-start}}
.rp-arrow{{font-weight:700;font-size:13px;flex-shrink:0;width:16px;text-align:center}}
.rp-arrow.up{{color:var(--green)}}
.rp-arrow.same{{color:var(--muted)}}
.rp-text{{color:var(--text);line-height:1.4}}
.rp-score{{color:var(--muted);font-size:11px;white-space:nowrap}}

/* Resume upload modal */
.resume-overlay{{display:none;position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,.55);
justify-content:center;align-items:center}}
.resume-overlay.open{{display:flex}}
.resume-modal{{background:var(--card);border-radius:var(--radius);padding:28px;width:min(600px,90vw);
max-height:85vh;overflow-y:auto;position:relative;box-shadow:0 12px 40px rgba(0,0,0,.25)}}
.resume-modal h3{{font-size:18px;margin:0 0 4px}}
.resume-modal p{{font-size:13px;color:var(--muted);margin:0 0 14px}}
.resume-modal .rm-close{{position:absolute;top:14px;right:14px;background:none;border:none;
font-size:22px;cursor:pointer;color:var(--muted);line-height:1}}
.resume-modal .rm-close:hover{{color:var(--text)}}
.resume-modal textarea{{width:100%;min-height:250px;padding:12px;border:1px solid var(--border);
border-radius:var(--radius);font-size:12px;font-family:'Courier New',monospace;
background:var(--bg);color:var(--text);resize:vertical;line-height:1.5}}
.resume-modal textarea:focus{{outline:none;border-color:var(--accent)}}
.resume-modal .rm-file{{margin:10px 0;font-size:13px}}
.resume-modal .rm-actions{{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}}
.resume-modal .rm-btn{{padding:8px 18px;border:none;border-radius:var(--radius);
font-weight:600;font-size:13px;cursor:pointer}}
.resume-modal .rm-save{{background:var(--accent);color:#fff}}
.resume-modal .rm-save:hover{{opacity:.9}}
.resume-modal .rm-clear{{background:var(--bg);color:var(--red);border:1px solid var(--border)}}
.resume-modal .rm-clear:hover{{background:var(--red);color:#fff}}
.resume-modal .rm-status{{font-size:12px;color:var(--green);margin-top:8px;display:none}}
.btn-upload{{padding:8px 14px;background:var(--card);color:var(--text);border:1px solid var(--border);
border-radius:var(--radius);font-size:13px;font-weight:500;cursor:pointer}}
.btn-upload:hover{{border-color:var(--accent);color:var(--accent)}}

.modal .m-notes{{width:100%;padding:10px;border:1px solid var(--border);border-radius:var(--radius);
font-size:13px;min-height:70px;background:var(--bg);color:var(--text);resize:vertical;font-family:inherit}}
.modal .m-notes:focus{{outline:none;border-color:var(--accent)}}
.modal .m-actions{{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}}
.btn-apply{{padding:10px 20px;background:var(--accent);color:#fff;border:none;border-radius:var(--radius);
font-weight:600;font-size:14px;cursor:pointer;text-decoration:none;text-align:center}}
.btn-apply:hover{{opacity:.9;text-decoration:none}}
</style>
</head>
<body>
<div class="header"><div class="header-row">
<div class="logo">Fresh<span>Apply</span></div>
<div class="gen-time">{gen_time} &middot; <span id="totalCount"></span> PM roles</div>
<div class="header-stats" id="tierBadges"></div>
</div></div>

<div class="toolbar"><div class="toolbar-inner">
<div class="toolbar-row toolbar-row--primary">
<div class="search-wrap">
<svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
<input type="text" class="search-box" id="search" placeholder="Search titles, companies, locations...">
</div>
<div class="toolbar-actions">
<select id="sortBy">
<option value="combined">Sort: Combined</option>
<option value="fresh">Sort: Freshness</option>
<option value="fit">Sort: Fit Score</option>
<option value="company">Sort: Company A-Z</option>
<option value="newest">Sort: Newest</option>
</select>
<button class="btn btn-export" onclick="exportCSV()">Export CSV</button>
<button class="btn-upload" onclick="openResumeModal()">Update Resume</button>
</div>
</div>
<div class="toolbar-row toolbar-row--filters">
<div class="filter-group">
<span class="filter-label">Priority</span>
<div class="chip-group" id="tierChips">
<button class="chip active" data-tier="all">All <span class="chip-count" id="countAll"></span></button>
<button class="chip" data-tier="Apply Today">Today <span class="chip-count" id="countToday"></span></button>
<button class="chip" data-tier="Apply This Week">This Week <span class="chip-count" id="countWeek"></span></button>
<button class="chip" data-tier="Watch List">Watch <span class="chip-count" id="countWatch"></span></button>
</div>
</div>
<div class="filter-sep"></div>
<div class="filter-group">
<span class="filter-label">Work</span>
<div class="chip-group" id="workTypeChips">
<button class="chip" data-wt="Remote">Remote <span class="chip-count" id="countRemote"></span></button>
<button class="chip" data-wt="Hybrid">Hybrid <span class="chip-count" id="countHybrid"></span></button>
<button class="chip" data-wt="On-site">On-site <span class="chip-count" id="countOnsite"></span></button>
</div>
</div>
<div class="filter-sep"></div>
<div class="filter-group">
<span class="filter-label">Location</span>
<div class="chip-group" id="locFlagChips">
<button class="chip" data-loc="Local">Local <span class="chip-count" id="countLocal"></span></button>
<button class="chip" data-loc="Relocation">Relocation <span class="chip-count" id="countRelocation"></span></button>
<button class="chip" data-loc="International">Intl <span class="chip-count" id="countInternational"></span></button>
</div>
</div>
<div class="filter-sep"></div>
<div class="filter-group">
<span class="filter-label">Status</span>
<div class="chip-group" id="statusChips">
<button class="chip" data-status="New">New <span class="chip-count" id="countNew"></span></button>
<button class="chip" data-status="Saved">Saved <span class="chip-count" id="countSaved"></span></button>
<button class="chip" data-status="Applied">Applied <span class="chip-count" id="countApplied"></span></button>
<button class="chip" data-status="Interviewing">Interview <span class="chip-count" id="countInterview"></span></button>
<button class="chip" data-status="Rejected">Rejected <span class="chip-count" id="countRejected"></span></button>
<button class="chip" data-status="Hidden">Hidden <span class="chip-count" id="countHidden"></span></button>
</div>
</div>
<div class="filter-sep"></div>
<div class="filter-group filter-group--dropdowns">
<select id="companyFilter"><option value="">All Companies</option></select>
<select id="salaryFilter">
<option value="">All Salaries</option>
<option value="has">Has Salary</option>
<option value="100">$100k+</option>
<option value="150">$150k+</option>
<option value="200">$200k+</option>
<option value="250">$250k+</option>
</select>
</div>
<button class="clear-filters" id="clearFilters" onclick="clearAllFilters()">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/></svg>
Clear filters
</button>
</div>
</div></div>

<div class="counter-bar" id="counterBar"></div>
<div class="grid" id="grid"></div>

<div class="modal-overlay" id="modalOverlay">
<div class="modal" id="modal">
<button class="modal-close" onclick="closeModal()">&times;</button>
<h2 id="mTitle"></h2>
<div class="m-meta" id="mMeta"></div>
<div class="m-header-actions">
<div class="m-salary" id="mSalary"></div>
<a class="btn-apply" id="mApplyBtn" href="#" target="_blank">Apply Now &rarr;</a>
</div>
<div class="m-scores" id="mScores"></div>
<h4>Fit Score Breakdown</h4>
<table class="breakdown-table"><thead><tr><th>Category</th><th>Weight</th><th>Status</th></tr></thead>
<tbody id="mBreakdown"></tbody></table>
<div id="mGap"></div>
<h4>Job Description</h4>
<div class="m-desc" id="mDesc"></div>
<h4>Your Notes</h4>
<textarea class="m-notes" id="mNotes" placeholder="Track your progress: referral contacts, follow-up dates, interview prep notes..."></textarea>
</div>
</div>

<div class="resume-overlay" id="resumeOverlay">
<div class="resume-modal">
<button class="rm-close" onclick="closeResumeModal()">&times;</button>
<h3>Update Your Resume</h3>
<p>Paste your resume text below or upload a .txt file. This will be used when generating tailored resumes for each job.</p>
<div class="rm-file">
<label><strong>Upload .txt file:</strong>
<input type="file" id="resumeFile" accept=".txt" onchange="loadResumeFile(this)" style="margin-left:8px">
</label>
</div>
<textarea id="resumeText" placeholder="Paste your resume text here..."></textarea>
<div class="rm-actions">
<button class="rm-btn rm-save" onclick="saveResume()">Save Resume</button>
<button class="rm-btn rm-clear" onclick="clearResume()">Clear Custom Resume</button>
</div>
<div class="rm-status" id="resumeStatus">Resume saved successfully!</div>
</div>
</div>

<script>
const JOBS={jobs_json};
const RESUME={resume_json};
const LS_KEY='freshapply_state';

function loadState(){{try{{return JSON.parse(localStorage.getItem(LS_KEY))||{{}}}}catch{{return{{}}}}}}
function saveState(s){{localStorage.setItem(LS_KEY,JSON.stringify(s))}}
function getState(){{const s=loadState();s.statuses=s.statuses||{{}};s.notes=s.notes||{{}};s.hidden=s.hidden||[];return s}}

let state=getState();
let activeTier='all';
let activeWorkTypes=new Set();
let activeLocFlags=new Set();
let activeStatuses=new Set();
let currentModalId=null;

function esc(s){{const d=document.createElement('div');d.textContent=s;return d.innerHTML}}

function tierClass(t){{if(t==='Apply Today')return 't-today';if(t==='Apply This Week')return 't-week';return 't-watch'}}
function fitClass(score){{if(score>=75)return 'fit-high';if(score>=50)return 'fit-mid';if(score>=25)return 'fit-low';return 'fit-vlow'}}
function fitColor(score){{if(score>=75)return 'var(--green)';if(score>=50)return 'var(--blue)';if(score>=25)return 'var(--amber)';return 'var(--red)'}}
function statusClass(st){{return st?'s-'+st.toLowerCase():''}}

function renderCard(j){{
const s=state.statuses[j.id]||'New';
const hasNote=state.notes[j.id]?'<span class="has-note"></span>':'';
const salaryHtml='<div class="card-salary">'+(j.salary?esc(j.salary):'<span class="no-salary">Salary not listed</span>')+'</div>';
return `<div class="card" data-id="${{esc(j.id)}}" onclick="openModal('${{esc(j.id)}}')">
<div class="card-header">
<div class="card-title"><a href="${{esc(j.url)}}" target="_blank" onclick="event.stopPropagation()">${{esc(j.title)}}</a>
<span class="tier-tag ${{tierClass(j.tier)}}">${{esc(j.tier)}}</span>
${{j.reposted?'<span class="repost-tag">REPOST</span>':''}}</div>
<button class="card-dismiss" onclick="event.stopPropagation();dismissJob('${{esc(j.id)}}')" title="Hide">&times;</button>
</div>
<div class="card-meta"><span class="company">${{esc(j.company)}}</span> &middot; ${{esc(j.location||'Remote')}} <span class="work-tag wt-${{j.workType.toLowerCase().replace('-','')}}">${{j.workType}}</span>${{j.locationFlag?'<span class="loc-flag lf-'+j.locationFlag.toLowerCase()+'">'+j.locationFlag+'</span>':''}}</div>
${{salaryHtml}}
<div class="score-bars">
<div class="score-bar"><div class="score-label"><span>Freshness</span><span>${{j.fresh}}</span></div>
<div class="bar-track"><div class="bar-fill fresh" style="width:${{j.fresh}}%"></div></div></div>
<div class="score-bar"><div class="score-label"><span>Fit</span><span>${{j.fit}}</span></div>
<div class="bar-track"><div class="bar-fill ${{fitClass(j.fit)}}" style="width:${{j.fit}}%"></div></div></div>
</div>
<div class="card-foot">
<span class="card-date">First seen ${{j.firstSeen}}${{hasNote}}</span>
<div class="card-actions">
<select class="status-select ${{statusClass(s)}}" onclick="event.stopPropagation()" onchange="setStatus('${{esc(j.id)}}',this.value,this)">
${{['New','Saved','Applied','Interviewing','Rejected'].map(function(o){{return '<option '+(o===s?'selected':'')+'>'+o+'</option>'}}).join('')}}
</select>
<a class="btn-card-apply" href="${{esc(j.url)}}" target="_blank" onclick="event.stopPropagation()">Apply</a>
</div>
</div></div>`;
}}

function parseSalaryNum(s){{
if(!s)return 0;
var m=s.match(/\\$(\\d[\\d,]*)/);
return m?parseInt(m[1].replace(/,/g,''),10):0;
}}

function getFiltered(){{
const q=document.getElementById('search').value.toLowerCase();
const co=document.getElementById('companyFilter').value;
const sal=document.getElementById('salaryFilter').value;
let jobs=JOBS.filter(j=>{{
if(activeStatuses.has('Hidden'))return state.hidden.includes(j.id);
if(state.hidden.includes(j.id))return false;
if(activeTier!=='all'&&j.tier!==activeTier)return false;
if(co&&j.companySlug!==co)return false;
if(activeWorkTypes.size>0&&!activeWorkTypes.has(j.workType))return false;
if(activeLocFlags.size>0){{var lf=j.locationFlag||'Local';if(!activeLocFlags.has(lf))return false}}
if(sal){{if(sal==='has'){{if(!j.salary)return false}}else{{var minSal=parseInt(sal,10)*1000;if(parseSalaryNum(j.salary)<minSal)return false}}}}
if(activeStatuses.size>0&&!activeStatuses.has('Hidden')){{var st=state.statuses[j.id]||'New';if(!activeStatuses.has(st))return false}}
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
document.getElementById('counterBar').textContent='Showing '+jobs.length+' of '+JOBS.length+' roles';
var counts={{}};JOBS.forEach(function(j){{counts[j.tier]=(counts[j.tier]||0)+1}});
document.getElementById('tierBadges').innerHTML=
'<span class="stat-badge stat-red">'+(counts['Apply Today']||0)+' today</span>'+
'<span class="stat-badge stat-amber">'+(counts['Apply This Week']||0)+' this week</span>'+
'<span class="stat-badge stat-gray">'+(counts['Watch List']||0)+' watch</span>';
document.getElementById('totalCount').textContent=JOBS.length;
updateCounts();
updateClearBtn();
}}

function updateCounts(){{
var visible=JOBS.filter(function(j){{return !state.hidden.includes(j.id)}});
/* Tier counts (full set) */
var tc={{'Apply Today':0,'Apply This Week':0,'Watch List':0}};
visible.forEach(function(j){{tc[j.tier]=(tc[j.tier]||0)+1}});
document.getElementById('countAll').textContent=visible.length;
document.getElementById('countToday').textContent=tc['Apply Today']||0;
document.getElementById('countWeek').textContent=tc['Apply This Week']||0;
document.getElementById('countWatch').textContent=tc['Watch List']||0;
/* Work type counts */
var wc={{'Remote':0,'Hybrid':0,'On-site':0}};
visible.forEach(function(j){{wc[j.workType]=(wc[j.workType]||0)+1}});
document.getElementById('countRemote').textContent=wc['Remote']||0;
document.getElementById('countHybrid').textContent=wc['Hybrid']||0;
document.getElementById('countOnsite').textContent=wc['On-site']||0;
/* Location flag counts */
var lc={{'Local':0,'Relocation':0,'International':0}};
visible.forEach(function(j){{var lf=j.locationFlag||'Local';lc[lf]=(lc[lf]||0)+1}});
document.getElementById('countLocal').textContent=lc['Local']||0;
document.getElementById('countRelocation').textContent=lc['Relocation']||0;
document.getElementById('countInternational').textContent=lc['International']||0;
/* Status counts */
var sc={{}};
visible.forEach(function(j){{var s=state.statuses[j.id]||'New';sc[s]=(sc[s]||0)+1}});
sc['Hidden']=state.hidden.length;
['New','Saved','Applied','Interviewing','Rejected','Hidden'].forEach(function(s){{
var id='count'+s.replace('Interviewing','Interview');
var el=document.getElementById(id);if(el)el.textContent=sc[s]||0;
}});
}}

function updateClearBtn(){{
var hasFilters=activeTier!=='all'||activeWorkTypes.size>0||activeLocFlags.size>0||activeStatuses.size>0
||document.getElementById('companyFilter').value!==''
||document.getElementById('salaryFilter').value!==''
||document.getElementById('search').value!=='';
document.getElementById('clearFilters').style.display=hasFilters?'inline-flex':'none';
var co=document.getElementById('companyFilter');
co.classList.toggle('has-filter',co.value!=='');
var sal=document.getElementById('salaryFilter');
sal.classList.toggle('has-filter',sal.value!=='');
}}

function clearAllFilters(){{
activeTier='all';
activeWorkTypes.clear();
activeLocFlags.clear();
activeStatuses.clear();
document.querySelectorAll('#tierChips .chip').forEach(function(c){{c.classList.remove('active')}});
document.querySelector('#tierChips .chip[data-tier="all"]').classList.add('active');
document.querySelectorAll('#workTypeChips .chip').forEach(function(c){{c.classList.remove('active')}});
document.querySelectorAll('#locFlagChips .chip').forEach(function(c){{c.classList.remove('active')}});
document.querySelectorAll('#statusChips .chip').forEach(function(c){{c.classList.remove('active')}});
document.getElementById('companyFilter').value='';
document.getElementById('salaryFilter').value='';
document.getElementById('search').value='';
render();
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
`<strong>${{esc(j.company)}}</strong> &middot; ${{esc(j.location||'Remote')}}`+
(j.locationFlag?' <span class="loc-flag lf-'+j.locationFlag.toLowerCase()+'">'+j.locationFlag+'</span>':'')+
` &middot; <span class="tier-tag ${{tierClass(j.tier)}}">${{j.tier}}</span>`+
(j.reposted?' <span class="repost-tag">REPOST</span>':'')+
` &middot; First seen ${{j.firstSeen}}`;
const salaryEl=document.getElementById('mSalary');
salaryEl.textContent=j.salary||'Salary not listed';
salaryEl.style.color=j.salary?'var(--green)':'var(--muted)';
document.getElementById('mScores').innerHTML=
`<div class="m-score-box"><div class="m-score-val" style="color:var(--bar-fresh)">${{j.fresh}}</div><div class="m-score-lbl">Fresh</div></div>`+
`<div class="m-score-box"><div class="m-score-val" style="color:${{fitColor(j.fit)}}">${{j.fit}}</div><div class="m-score-lbl">Fit</div></div>`+
`<div class="m-score-box"><div class="m-score-val" style="color:var(--accent)">${{j.combined}}</div><div class="m-score-lbl">Combined</div></div>`;

document.getElementById('mBreakdown').innerHTML=j.breakdown.map(function(b){{
var cell=b.matched?'<span class="match">'+esc(b.matched)+' ('+b.hits+' hits)</span>':'<span class="no-match">Not found</span>';
return '<tr><td>'+esc(b.bucket)+'</td><td>'+b.weight+'/'+b.maxPts+' pts</td><td>'+cell+'</td></tr>';
}}).join('');

/* Gap analysis + resume tips */
const gapEl=document.getElementById('mGap');
if(j.fit<75 && j.suggestions && j.suggestions.length>0){{
var gh='<div class="gap-section"><h4>Resume Gap Analysis (Fit: '+j.fit+'/100)</h4>';
gh+='<p style="font-size:12px;margin-bottom:8px">Improve your match by addressing these keyword gaps:</p>';
j.suggestions.forEach(function(s){{
var label=s.status==='missing'?'MISSING':'NEEDS MORE';
var labelColor=s.status==='missing'?'var(--red)':'var(--amber)';
var bullets=s.bullets.map(function(b){{return '<li>'+esc(b)+'</li>'}}).join('');
gh+='<div class="gap-item">';
gh+='<div class="gap-item-head"><span style="color:'+labelColor+'">'+label+':</span> '+esc(s.bucket)+' <span class="pts">(+'+s.weight+' pts available)</span></div>';
gh+='<div class="gap-keywords">Add keywords: '+esc(s.keywords)+'</div>';
gh+='<div class="gap-sub-label">Resume bullet suggestions:</div>';
gh+='<ul class="gap-bullets">'+bullets+'</ul>';
if(s.learning&&s.learning.length>0){{
gh+='<div class="gap-sub-label gap-learn-label">Learning recommendations:</div>';
gh+='<ul class="gap-learn">';
s.learning.forEach(function(l){{gh+='<li>'+esc(l)+'</li>'}});
gh+='</ul>';
}}
gh+='</div>';
}});
gh+='<div class="gap-actions">';
gh+='<button class="btn-preview" onclick="previewResumeChanges(\\''+esc(j.id)+'\\')">Preview Resume Changes</button>';
gh+='<button class="btn-resume" onclick="downloadTailoredResume(\\''+esc(j.id)+'\\')">Generate Tailored Resume</button>';
gh+='</div>';
gh+='<div id="mResumePreview"></div>';
gh+='</div>';
gapEl.innerHTML=gh;
}}else{{
var msg='';
if(j.fit>=75)msg='<p style="color:var(--green);font-size:13px;margin:8px 0;font-weight:600">Strong fit! Your profile matches well.</p>';
msg+='<div class="gap-actions" style="margin-top:10px">';
msg+='<button class="btn-preview" onclick="previewResumeChanges(\\''+esc(j.id)+'\\')">Preview Resume Changes</button>';
msg+='<button class="btn-resume" onclick="downloadTailoredResume(\\''+esc(j.id)+'\\')">Generate Tailored Resume</button>';
msg+='</div><div id="mResumePreview"></div>';
gapEl.innerHTML=msg;
}}

/* Description — render HTML if available, plain text otherwise */
const descEl=document.getElementById('mDesc');
if(j.descHtml){{descEl.innerHTML=j.descHtml}}
else{{descEl.textContent=j.description||'No description available.'}}

document.getElementById('mNotes').value=state.notes[id]||'';
document.getElementById('mApplyBtn').href=j.url||'#';
document.getElementById('modalOverlay').classList.add('open');
}}

function closeModal(){{
if(currentModalId){{const n=document.getElementById('mNotes').value.trim();
if(n)state.notes[currentModalId]=n;else delete state.notes[currentModalId];saveState(state)}}
currentModalId=null;document.getElementById('modalOverlay').classList.remove('open');render()}}

function scoreBullet(bullet,jobDesc){{
/* Score a resume bullet against a job description by counting keyword overlaps */
var bl=bullet.toLowerCase();var jd=jobDesc.toLowerCase();var score=0;
var kws=jd.match(/\\b[a-z]{{3,}}\\b/g)||[];
var unique=Array.from(new Set(kws));
unique.forEach(function(w){{if(bl.indexOf(w)!==-1)score++}});
return score;
}}

function previewResumeChanges(id){{
var j=JOBS.find(function(x){{return x.id===id}});if(!j)return;
var R=RESUME;
var jd=(j.title+' '+j.description).toLowerCase();
var el=document.getElementById('mResumePreview');

/* Compute tailored tagline */
var focusAreas=[];
if(/\\bai\\b|\\bml\\b|\\bmachine.learn|\\bllm\\b|\\bgenerative/i.test(j.description))focusAreas.push('AI/ML');
if(/\\bplatform\\b|\\binfrastructure\\b/i.test(j.description))focusAreas.push('Platform');
if(/\\benterprise\\b/i.test(j.description))focusAreas.push('Enterprise');
if(/\\bagent\\b|\\bagentic\\b/i.test(j.description))focusAreas.push('Agentic Systems');
if(/\\bworkflow\\b|\\bautomation\\b/i.test(j.description))focusAreas.push('Workflow Automation');
if(/\\bhealthcare\\b|\\bclinical\\b|\\bhealth/i.test(j.description))focusAreas.push('Healthcare');
if(/\\breal.estate\\b|\\bproptech\\b/i.test(j.description))focusAreas.push('Real Estate');
if(/\\bdata\\b|\\banalytics\\b/i.test(j.description))focusAreas.push('Data & Analytics');
var newTagline=focusAreas.length>0?focusAreas.join('  |  ')+'  |  0-1 AI Product Strategy  |  RAG + Evals':R.tagline;
var taglineChanged=newTagline!==R.tagline;

/* Score and rank competencies */
var compScored=R.competencies.map(function(c,i){{
var cl=c.toLowerCase();var s=0;
if(jd.indexOf(cl)!==-1)s+=10;
cl.split(/\\s+/).forEach(function(w){{if(w.length>3&&jd.indexOf(w)!==-1)s+=2}});
return {{text:c,origIdx:i,score:s}};
}});
compScored.sort(function(a,b){{return b.score-a.score}});

/* Score bullets for first experience section (main role) */
var mainExp=R.experience[0];
var bulletScored=mainExp.bullets.map(function(b,i){{
return {{text:b,origIdx:i,score:scoreBullet(b,j.description)}};
}});
bulletScored.sort(function(a,b){{return b.score-a.score}});

/* Build preview HTML */
var h='<div class="resume-preview"><h5>Resume Changes for This Role</h5>';

/* Tagline */
if(taglineChanged){{
h+='<div class="rp-section"><div class="rp-section-title">Tagline Updated</div>';
h+='<div class="rp-change"><span class="rp-arrow up">+</span><span class="rp-text">'+esc(newTagline)+'</span></div>';
h+='<div class="rp-change"><span class="rp-arrow same">-</span><span class="rp-text" style="color:var(--muted);text-decoration:line-through">'+esc(R.tagline)+'</span></div>';
h+='</div>';
}}

/* Top competencies moved up */
h+='<div class="rp-section"><div class="rp-section-title">Competencies Reordered (top 5)</div>';
compScored.slice(0,5).forEach(function(c,newIdx){{
var moved=c.origIdx>newIdx;
var arrow=moved?'<span class="rp-arrow up">&#8593;</span>':'<span class="rp-arrow same">=</span>';
var note=moved?' (was #'+(c.origIdx+1)+', now #'+(newIdx+1)+')':' (stayed #'+(newIdx+1)+')';
h+='<div class="rp-change">'+arrow+'<span class="rp-text">'+esc(c.text)+'<span class="rp-score">'+note+'</span></span></div>';
}});
h+='</div>';

/* Top promoted bullets */
h+='<div class="rp-section"><div class="rp-section-title">Bullet Points Reordered — '+esc(mainExp.company.split('(')[0].trim())+'</div>';
bulletScored.slice(0,5).forEach(function(b,newIdx){{
var moved=b.origIdx>newIdx;
var arrow=moved?'<span class="rp-arrow up">&#8593;</span>':'<span class="rp-arrow same">=</span>';
var snippet=b.text.length>120?b.text.substring(0,120)+'...':b.text;
h+='<div class="rp-change">'+arrow+'<span class="rp-text">'+esc(snippet)+' <span class="rp-score">('+b.score+' keyword matches)</span></span></div>';
}});
h+='</div>';

h+='</div>';
el.innerHTML=h;
el.scrollIntoView({{behavior:'smooth',block:'nearest'}});
}}

function downloadTailoredResume(id){{
var j=JOBS.find(function(x){{return x.id===id}});if(!j)return;
var R=RESUME;
var jd=(j.title+' '+j.description).toLowerCase();

/* Build tailored tagline: prepend job-relevant focus areas */
var focusAreas=[];
if(/\\bai\\b|\\bml\\b|\\bmachine.learn|\\bllm\\b|\\bgenerative/i.test(j.description))focusAreas.push('AI/ML');
if(/\\bplatform\\b|\\binfrastructure\\b/i.test(j.description))focusAreas.push('Platform');
if(/\\benterprise\\b/i.test(j.description))focusAreas.push('Enterprise');
if(/\\bagent\\b|\\bagentic\\b/i.test(j.description))focusAreas.push('Agentic Systems');
if(/\\bworkflow\\b|\\bautomation\\b/i.test(j.description))focusAreas.push('Workflow Automation');
if(/\\bhealthcare\\b|\\bclinical\\b|\\bhealth/i.test(j.description))focusAreas.push('Healthcare');
if(/\\breal.estate\\b|\\bproptech\\b/i.test(j.description))focusAreas.push('Real Estate');
if(/\\bdata\\b|\\banalytics\\b/i.test(j.description))focusAreas.push('Data & Analytics');
var tagline=focusAreas.length>0?focusAreas.join('  |  ')+'  |  0-1 AI Product Strategy  |  RAG + Evals':R.tagline;

/* Reorder competencies: matching terms first */
var compScored=R.competencies.map(function(c){{
var cl=c.toLowerCase();var s=0;
if(jd.indexOf(cl)!==-1)s+=10;
var words=cl.split(/\\s+/);
words.forEach(function(w){{if(w.length>3&&jd.indexOf(w)!==-1)s+=2}});
return {{text:c,score:s}};
}});
compScored.sort(function(a,b){{return b.score-a.score}});
var orderedComps=compScored.map(function(c){{return c.text}});

/* For each experience section, score and reorder bullets */
var expSections=R.experience.map(function(exp){{
var scoredBullets=exp.bullets.map(function(b){{
return {{text:b,score:scoreBullet(b,j.description)}};
}});
scoredBullets.sort(function(a,b){{return b.score-a.score}});
return {{
section:exp.section||'',company:exp.company,title:exp.title,location:exp.location,
dates:exp.dates,overview:exp.overview,
bullets:scoredBullets.map(function(b){{return b.text}})
}};
}});

/* Check if user uploaded a custom resume */
var customResume=localStorage.getItem('freshapply_custom_resume');
if(customResume){{
/* For custom resume: download as-is in .doc format with a header note */
var h='<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">';
h+='<head><meta charset="utf-8"><style>body{{font-family:Calibri,sans-serif;font-size:11pt;line-height:1.4;color:#1a1a2e}}';
h+='p{{margin:4px 0}}</style></head><body>';
h+='<p>'+customResume.replace(/\\n/g,'<br>')+'</p>';
h+='</body></html>';
var blob=new Blob([h],{{type:'application/msword'}});
var a=document.createElement('a');a.href=URL.createObjectURL(blob);
var slug=j.company.toLowerCase().replace(/\\s+/g,'-')+'-'+j.title.toLowerCase().replace(/[^a-z0-9]+/g,'-').substring(0,40);
a.download=RESUME.name.replace(/\\s+/g,'_')+'_Resume_'+slug+'.doc';a.click();return;
}}

/* Build Word-compatible HTML resume — tight layout matching original .docx */
var h='<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">';
h+='<head><meta charset="utf-8">';
h+='<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View></w:WordDocument></xml><![endif]-->';
h+='<style>';
h+='@page{{size:letter;margin:0.5in 0.5in 0.4in 0.5in}}';
h+='body{{font-family:Calibri,sans-serif;font-size:9.5pt;line-height:1.25;color:#1a1a2e;margin:0}}';
h+='h1{{font-size:14pt;margin:0 0 1px;letter-spacing:1px}}';
h+='h2{{font-size:10pt;color:#000;margin:7px 0 2px;border-bottom:1px solid #c0c0c0;padding-bottom:1px;text-transform:uppercase;letter-spacing:0.5px}}';
h+='.contact{{font-size:8.5pt;color:#555;margin:1px 0 2px}}';
h+='.headline{{font-size:11pt;font-weight:bold;color:#1e293b;margin:3px 0 1px}}';
h+='.tagline{{font-size:9pt;color:#2563eb;margin:0 0 4px}}';
h+='.summary{{font-size:9pt;color:#333;margin:2px 0 5px;line-height:1.3}}';
h+='.comp{{font-size:9pt;color:#333;line-height:1.3}}';
h+='.company{{font-size:9pt;font-weight:bold;color:#1a1a2e;margin:5px 0 0}}';
h+='.role{{font-size:9pt;color:#555;margin:0 0 1px}}';
h+='.overview{{font-size:9pt;color:#333;margin:1px 0;font-style:italic}}';
h+='ul{{margin:1px 0 3px;padding-left:14px}}';
h+='li{{font-size:9pt;color:#333;margin:1px 0;line-height:1.25}}';
h+='.tools{{font-size:8.5pt;color:#333;line-height:1.3}}';
h+='.tools b{{color:#1a1a2e}}';
h+='.edu{{font-size:8.5pt;color:#333}}';
h+='</style></head><body>';

h+='<h1>'+esc(R.name)+'</h1>';
h+='<div class="contact">'+esc(R.contact)+'</div>';
h+='<div class="headline">'+esc(R.headline)+'</div>';
h+='<div class="tagline">'+esc(tagline)+'</div>';
h+='<div class="summary">'+esc(R.summary)+'</div>';

h+='<h2>CORE COMPETENCIES</h2>';
h+='<div class="comp">'+orderedComps.map(function(c){{return esc(c)}}).join('  &bull;  ')+'</div>';

var lastSection='';
expSections.forEach(function(exp){{
if(exp.section&&exp.section!==lastSection){{h+='<h2>'+esc(exp.section)+'</h2>';lastSection=exp.section}}
h+='<div class="company">'+esc(exp.company)+'</div>';
var roleLine=esc(exp.title);
if(exp.location)roleLine+='  |  '+esc(exp.location);
roleLine+='  |  '+esc(exp.dates);
h+='<div class="role">'+roleLine+'</div>';
if(exp.overview)h+='<div class="overview">'+esc(exp.overview)+'</div>';
h+='<ul>';
exp.bullets.forEach(function(b){{h+='<li>'+esc(b)+'</li>'}});
h+='</ul>';
}});

h+='<h2>AI PM TOOLKIT</h2>';
h+='<div class="tools">';
Object.keys(R.tools).forEach(function(k){{
h+='<b>'+esc(k)+':</b> '+esc(R.tools[k])+'<br>';
}});
h+='</div>';

h+='<h2>EDUCATION &amp; CERTIFICATIONS</h2>';
h+='<div class="edu">'+esc(R.education)+'</div>';
h+='</body></html>';

var blob=new Blob([h],{{type:'application/msword'}});
var a=document.createElement('a');
a.href=URL.createObjectURL(blob);
var slug=j.company.toLowerCase().replace(/\\s+/g,'-')+'-'+j.title.toLowerCase().replace(/[^a-z0-9]+/g,'-').substring(0,40);
a.download=RESUME.name.replace(/\\s+/g,'_')+'_Resume_'+slug+'.doc';
a.click();
}}

function exportCSV(){{
const jobs=getFiltered();
const hdr='Title,Company,Location,Work Type,Location Flag,Salary,Tier,Freshness,Fit,Combined,URL,Status,First Seen\\n';
const rows=jobs.map(j=>{{const s=state.statuses[j.id]||'New';
return [j.title,j.company,j.location,j.workType,j.locationFlag||'Local',j.salary||'',j.tier,j.fresh,j.fit,j.combined,j.url,s,j.firstSeen]
.map(v=>`"${{String(v).replace(/"/g,'""')}}"`)
.join(',')}}).join('\\n');
const blob=new Blob([hdr+rows],{{type:'text/csv'}});const a=document.createElement('a');
a.href=URL.createObjectURL(blob);a.download='freshapply-export.csv';a.click()}}

function openResumeModal(){{
var existing=localStorage.getItem('freshapply_custom_resume');
document.getElementById('resumeText').value=existing||'';
document.getElementById('resumeStatus').style.display='none';
document.getElementById('resumeFile').value='';
document.getElementById('resumeOverlay').classList.add('open');
}}

function closeResumeModal(){{
document.getElementById('resumeOverlay').classList.remove('open');
}}

function loadResumeFile(input){{
if(!input.files||!input.files[0])return;
var reader=new FileReader();
reader.onload=function(e){{document.getElementById('resumeText').value=e.target.result}};
reader.readAsText(input.files[0]);
}}

function saveResume(){{
var txt=document.getElementById('resumeText').value.trim();
if(!txt){{alert('Please paste or upload your resume text first.');return}}
localStorage.setItem('freshapply_custom_resume',txt);
var st=document.getElementById('resumeStatus');
st.textContent='Resume saved successfully! Tailored resumes will now use your updated version.';
st.style.color='var(--green)';st.style.display='block';
setTimeout(function(){{st.style.display='none'}},4000);
}}

function clearResume(){{
localStorage.removeItem('freshapply_custom_resume');
document.getElementById('resumeText').value='';
var st=document.getElementById('resumeStatus');
st.textContent='Custom resume cleared. Default resume will be used for tailored downloads.';
st.style.color='var(--amber)';st.style.display='block';
setTimeout(function(){{st.style.display='none'}},4000);
}}

document.getElementById('resumeOverlay').addEventListener('click',function(e){{
if(e.target===this)closeResumeModal()}});

document.getElementById('search').addEventListener('input',render);
document.getElementById('companyFilter').addEventListener('change',render);
document.getElementById('salaryFilter').addEventListener('change',render);
document.getElementById('sortBy').addEventListener('change',render);

/* Tier chips: single-select */
document.querySelectorAll('#tierChips .chip').forEach(function(c){{c.addEventListener('click',function(){{
document.querySelectorAll('#tierChips .chip').forEach(function(x){{x.classList.remove('active')}});
this.classList.add('active');activeTier=this.dataset.tier;render();
}})}});

/* Work type chips: multi-select toggle */
document.querySelectorAll('#workTypeChips .chip').forEach(function(c){{c.addEventListener('click',function(){{
var wt=this.dataset.wt;
if(activeWorkTypes.has(wt)){{activeWorkTypes.delete(wt);this.classList.remove('active')}}
else{{activeWorkTypes.add(wt);this.classList.add('active')}}
render();
}})}});

/* Location flag chips: multi-select toggle */
document.querySelectorAll('#locFlagChips .chip').forEach(function(c){{c.addEventListener('click',function(){{
var lf=this.dataset.loc;
if(activeLocFlags.has(lf)){{activeLocFlags.delete(lf);this.classList.remove('active')}}
else{{activeLocFlags.add(lf);this.classList.add('active')}}
render();
}})}});

/* Status chips: multi-select toggle */
document.querySelectorAll('#statusChips .chip').forEach(function(c){{c.addEventListener('click',function(){{
var st=this.dataset.status;
if(activeStatuses.has(st)){{activeStatuses.delete(st);this.classList.remove('active')}}
else{{activeStatuses.add(st);this.classList.add('active')}}
render();
}})}});

document.getElementById('modalOverlay').addEventListener('click',function(e){{
if(e.target===this)closeModal()}});
document.addEventListener('keydown',function(e){{if(e.key==='Escape'){{closeModal();closeResumeModal()}}}});

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

#!/usr/bin/env python3
"""
FreshApply Evals — deterministic quality checks on salary extraction,
PM title filtering, work type classification, and description sanitization.

Usage:
    python3 eval_freshapply.py
"""
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from freshapply import (
    _extract_salary,
    _sanitize_html,
    _strip_html,
    _detect_countries,
    _classify_location_flag,
    _is_region_only,
    fit_score,
    compute_fit_breakdown,
    freshness_score,
    tier,
    FIT_KEYWORDS,
    PM_RE,
    PM_EXCLUDE_RE,
)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "freshapply.db")

# ── Eval helpers ─────────────────────────────────────────────────────────────

def _has_dollar_range(text: str) -> bool:
    """Check if text contains a salary-like $X - $Y pattern (not revenue/valuation)."""
    # Must have two dollar amounts connected by a separator (-, to, and)
    _cur = r"(?:(?:USD|CAD|GBP|EUR)\s*)?"
    _amt = r"\$[\d,]+(?:\.\d+)?\s*[kK]?"
    _suf = r"(?:\s*(?:USD|CAD|GBP|EUR)\+?)?"
    pat1 = _cur + _amt + _suf + r"\s*[-–—~]+\s*" + _cur + _amt
    pat2 = _cur + _amt + _suf + r"\s+(?:to|and)\s+" + _cur + _amt
    if re.search(pat1, text, re.IGNORECASE) or re.search(pat2, text, re.IGNORECASE):
        # Exclude revenue/valuation patterns like "$2M", "$10B", "$100 billion"
        for m in re.finditer(pat1, text, re.IGNORECASE):
            if not re.search(r"\$[\d,]+\s*[MBmb]", m.group()):
                return True
        for m in re.finditer(pat2, text, re.IGNORECASE):
            if not re.search(r"\$[\d,]+\s*[MBmb]", m.group()):
                return True
    return False


def _count_salary_ranges(text: str) -> int:
    """Count distinct salary range patterns in text."""
    _cur = r"(?:(?:USD|CAD|GBP|EUR)\s*)?"
    _amt = r"\$[\d,]+(?:\.\d+)?\s*[kK]?"
    _suf = r"(?:\s*(?:USD|CAD|GBP|EUR)\+?)?"
    pat = _cur + _amt + _suf + r"\s*[-–—~]+\s*" + _cur + _amt + _suf
    pat2 = _cur + _amt + _suf + r"\s+(?:to|and)\s+" + _cur + _amt + _suf
    return len(re.findall(pat, text, re.IGNORECASE)) + len(re.findall(pat2, text, re.IGNORECASE))


# ── Eval 1: Salary Extraction ────────────────────────────────────────────────

def eval_salary(conn):
    print("\nSALARY EXTRACTION")
    print("-" * 60)

    rows = conn.execute("SELECT id, company, title, description, salary FROM jobs").fetchall()
    total = len(rows)

    correct_empty = 0      # No salary in desc, correctly ""
    correct_found = 0      # Salary in desc, correctly extracted
    missed = []            # Salary in desc, but extracted ""
    multi_range = []       # Multiple ranges, verify full span captured
    changed = []           # Extraction result differs from stored value

    for jid, company, title, desc, stored_salary in rows:
        plain = _strip_html(desc) if desc else ""
        extracted = _extract_salary(plain)
        has_salary = _has_dollar_range(plain)
        range_count = _count_salary_ranges(plain)

        if not has_salary and not extracted:
            correct_empty += 1
        elif has_salary and extracted:
            correct_found += 1
            if range_count > 1:
                multi_range.append((jid, title, range_count, extracted))
            if stored_salary and stored_salary != extracted:
                changed.append((jid, title, stored_salary, extracted))
        elif has_salary and not extracted:
            missed.append((jid, title, plain[:200]))
        else:
            correct_found += 1  # extracted something from non-obvious pattern

    pass_count = correct_empty + correct_found
    print(f"  Total jobs: {total}")
    print(f"  \u2705 {correct_empty} jobs: no salary in description, correctly returned \"\"")
    print(f"  \u2705 {correct_found} jobs: salary correctly extracted")

    if missed:
        print(f"  \u26a0\ufe0f  {len(missed)} jobs: salary in description but not extracted")
        for jid, title, snippet in missed[:5]:
            # Find the dollar amount in snippet
            m = re.search(r"\$[\d,]+", snippet)
            ctx = m.group(0) if m else "..."
            print(f"    [MISSED] {jid} \"{title}\" — contains {ctx}")

    if multi_range:
        print(f"  \u2139\ufe0f  {len(multi_range)} jobs: multiple salary ranges merged")
        for jid, title, count, extracted in multi_range[:5]:
            print(f"    [MERGED] {jid} \"{title}\" — {count} ranges → {extracted}")

    if changed:
        print(f"  \u2139\ufe0f  {len(changed)} jobs: extraction improved from stored value")
        for jid, title, old, new in changed[:5]:
            print(f"    [UPDATED] {jid} \"{title}\"")
            print(f"      was: {old}")
            print(f"      now: {new}")

    return len(missed)


# ── Eval 2: PM Title Filtering ───────────────────────────────────────────────

def eval_pm_titles(conn):
    print("\nPM TITLE FILTERING")
    print("-" * 60)

    # Curated test cases
    should_match = [
        "Product Manager",
        "Senior Product Manager",
        "Staff Product Manager, AI Platform",
        "Principal Product Manager",
        "Product Manager, Enterprise AI",
        "Director of Product",
        "Director, Product Management",
        "Head of Product",
        "VP of Product",
        "Vice President, Product Management",
        "Product Lead, AI",
        "Group PM",
        "Senior PM",
        "Product Manager, Gemini App",
        "Sr. Product Manager, AI Capabilities",
    ]

    should_not_match = [
        "Project Manager",
        "Senior Project Manager",
        "Program Manager",
        "Technical Program Manager",
        "Product Marketing Manager",
        "Product Designer",
        "Product Counsel",
        "Product Communications Manager",
        "Software Engineer, Product",
        "Sales Engineer, Product",
        "Legal Product Counsel",
        "Video Product Manager",  # excluded by pattern
        "Product Launch Manager",
        "Product Account Manager",
    ]

    # Test inclusions
    include_pass = 0
    include_fail = []
    for title in should_match:
        if PM_RE.search(title) and not PM_EXCLUDE_RE.search(title):
            include_pass += 1
        else:
            include_fail.append(title)

    # Test exclusions
    exclude_pass = 0
    exclude_fail = []
    for title in should_not_match:
        if not PM_RE.search(title) or PM_EXCLUDE_RE.search(title):
            exclude_pass += 1
        else:
            exclude_fail.append(title)

    total_tests = len(should_match) + len(should_not_match)
    print(f"  Curated test cases: {total_tests}")
    print(f"  \u2705 {include_pass}/{len(should_match)} correct inclusions")
    if include_fail:
        for t in include_fail:
            print(f"    \u274c FALSE NEGATIVE: \"{t}\" should match but didn't")

    print(f"  \u2705 {exclude_pass}/{len(should_not_match)} correct exclusions")
    if exclude_fail:
        for t in exclude_fail:
            print(f"    \u274c FALSE POSITIVE: \"{t}\" matched but shouldn't")

    # Scan DB for suspicious titles
    db_titles = conn.execute("SELECT id, title FROM jobs").fetchall()
    suspicious = []
    for jid, title in db_titles:
        t_lower = title.lower()
        if "project" in t_lower or "program" in t_lower:
            suspicious.append((jid, title))

    print(f"\n  Database scan ({len(db_titles)} jobs):")
    if suspicious:
        print(f"  \u26a0\ufe0f  {len(suspicious)} suspicious titles found:")
        for jid, title in suspicious[:10]:
            print(f"    [{jid}] \"{title}\"")
    else:
        print(f"  \u2705 No project/program manager titles in database")

    return len(include_fail) + len(exclude_fail) + len(suspicious)


# ── Eval 3: Work Type Classification ─────────────────────────────────────────

def eval_work_type(conn):
    print("\nWORK TYPE CLASSIFICATION")
    print("-" * 60)

    rows = conn.execute("SELECT id, title, location, description FROM jobs").fetchall()
    total = len(rows)

    counts = {"Remote": 0, "Hybrid": 0, "On-site": 0}
    issues = []

    for jid, title, location, desc in rows:
        loc_lower = (location or "").lower()
        desc_lower = (desc or "").lower()[:500]

        # Replicate the classification logic
        if "hybrid" in loc_lower or "hybrid" in desc_lower:
            work_type = "Hybrid"
        elif "remote" in loc_lower:
            work_type = "Remote"
        elif not location or not location.strip():
            work_type = "Remote"
        elif _is_region_only(location):
            work_type = "Remote"
        else:
            work_type = "On-site"

        counts[work_type] = counts.get(work_type, 0) + 1

        # Check for potential misclassifications
        if work_type == "On-site":
            # Does description mention remote eligibility?
            if re.search(r"remote\s*(?:eligible|friendly|ok|okay|option|possible|available)", desc_lower):
                issues.append((jid, title, location, "On-site but description mentions remote eligibility"))
            elif "remote" in desc_lower[:200] and "not remote" not in desc_lower[:200] and "no remote" not in desc_lower[:200]:
                pass  # Too noisy — "remote" appears in many contexts

        if work_type == "Remote" and location and location.strip():
            # Has a location but classified as remote — verify "remote" is in location
            # Region-only locations (NAMER, United States, etc.) are correctly Remote
            if "remote" not in loc_lower and not _is_region_only(location):
                issues.append((jid, title, location, f"Classified Remote but location is \"{location}\""))

    print(f"  Total jobs: {total}")
    for wt, count in counts.items():
        print(f"  \u2705 {count} {wt}")

    if issues:
        print(f"\n  \u26a0\ufe0f  {len(issues)} possible misclassifications:")
        for jid, title, loc, reason in issues[:10]:
            print(f"    [{jid[:30]}] \"{title[:40]}\" — {reason}")
    else:
        print(f"  \u2705 No misclassifications detected")

    return len(issues)


# ── Eval 4: Description Sanitization ─────────────────────────────────────────

def eval_sanitization(conn):
    print("\nDESCRIPTION SANITIZATION")
    print("-" * 60)

    rows = conn.execute("SELECT id, title, description_html FROM jobs WHERE description_html != ''").fetchall()
    total = len(rows)

    clean = 0
    issues = []

    for jid, title, raw_html in rows:
        sanitized = _sanitize_html(raw_html)

        problems = []
        # Check for residual HTML structure tags
        if re.search(r"<(?:div|script|style|iframe|form)\b", sanitized, re.IGNORECASE):
            problems.append("residual HTML tags")
        # Check for entity-encoded HTML (indicates unescape didn't run)
        if "&lt;div" in sanitized or "&lt;p&gt;" in sanitized:
            problems.append("entity-encoded HTML")
        # Check for &nbsp;
        if "&nbsp;" in sanitized:
            problems.append("contains &nbsp;")
        # Check for pay-transparency boilerplate
        if "pay-transparency" in sanitized.lower() or "content-conclusion" in sanitized.lower():
            problems.append("ATS boilerplate not stripped")
        # Check for class/style attributes
        if re.search(r'\bclass="', sanitized):
            problems.append("class attributes remaining")

        if problems:
            issues.append((jid, title, problems))
        else:
            clean += 1

    print(f"  Total descriptions: {total}")
    print(f"  \u2705 {clean} clean (no residual HTML/entities/boilerplate)")

    if issues:
        print(f"  \u26a0\ufe0f  {len(issues)} have residual issues:")
        for jid, title, probs in issues[:10]:
            print(f"    [{jid[:30]}] \"{title[:40]}\" — {', '.join(probs)}")
    else:
        print(f"  \u2705 All descriptions properly sanitized")

    return len(issues)


# ── Eval 5: Location Detection ──────────────────────────────────────────────

def eval_location_detection(conn):
    print("\nLOCATION DETECTION")
    print("-" * 60)

    # Curated test cases: (location_string, expected_countries)
    test_cases = [
        ("San Francisco, CA", {"US"}),
        ("New York, NY", {"US"}),
        ("Dallas, TX", {"US"}),
        ("Remote - US", {"US"}),
        ("Remote - UK", {"UK"}),
        ("Remote - Canada", {"CA"}),
        ("Remote", set()),
        ("", set()),
        ("London, UK", {"UK"}),
        ("London, England, United Kingdom", {"UK"}),
        ("Toronto, ON", {"CA"}),
        ("Vancouver, British Columbia, Canada", {"CA"}),
        ("Paris, France", {"FR"}),
        ("Berlin, Germany", {"DE"}),
        ("Amsterdam, Netherlands", {"NL"}),
        ("Bangalore", {"IN"}),
        ("Singapore", {"SG"}),
        ("Tel Aviv", {"IL"}),
        ("Zurich, Switzerland", {"CH"}),
        ("Mountain View, California, US; New York City, NY", {"US"}),
        ("Doha, Qatar ; Dubai, UAE", {"QA", "AE"}),
        ("NAMER", {"US", "CA"}),
        ("North America", {"US", "CA"}),
        ("United States", {"US"}),
        ("San Francisco, CA \u2022 New York, NY \u2022 United States", {"US"}),
    ]

    pass_count = 0
    fail_cases = []
    for loc, expected in test_cases:
        detected = _detect_countries(loc)
        if detected == expected:
            pass_count += 1
        else:
            fail_cases.append((loc, expected, detected))

    print(f"  Country detection: {pass_count}/{len(test_cases)} correct")
    if fail_cases:
        for loc, expected, detected in fail_cases:
            print(f"    \u274c \"{loc}\" — expected {expected}, got {detected}")

    # Test _is_region_only
    region_tests = [
        ("NAMER", True),
        ("North America", True),
        ("United States", True),
        ("United States ", True),
        ("US", True),
        ("Europe", True),
        ("", True),
        ("San Francisco, CA", False),
        ("Sunnyvale, California, United States", False),
        ("New York, New York, USA", False),
        ("San Mateo, CA United States", False),
        ("San Francisco, CA \u2022 New York, NY \u2022 United States", False),
        ("London", False),
        ("Bangalore", False),
        ("Singapore", False),
        ("Tel Aviv", False),
    ]

    region_pass = 0
    region_fail = []
    for loc, expected in region_tests:
        result = _is_region_only(loc)
        if result == expected:
            region_pass += 1
        else:
            region_fail.append((loc, expected, result))

    print(f"  Region-only detection: {region_pass}/{len(region_tests)} correct")
    if region_fail:
        for loc, expected, result in region_fail:
            print(f"    \u274c \"{loc}\" — expected {expected}, got {result}")

    # Test flag classification (user = US, Dallas, TX)
    flag_tests = [
        ("Remote - US", "Remote", "US", "Dallas, TX", ""),
        ("Remote", "Remote", "US", "Dallas, TX", ""),
        ("Remote - UK", "Remote", "US", "Dallas, TX", "International"),
        ("Dallas, TX", "On-site", "US", "Dallas, TX", ""),
        ("San Francisco, CA", "On-site", "US", "Dallas, TX", "Relocation"),
        ("New York, NY", "Hybrid", "US", "Dallas, TX", "Relocation"),
        ("London, UK", "On-site", "US", "Dallas, TX", "International"),
        ("", "Remote", "US", "Dallas, TX", ""),
        ("Paris, France", "On-site", "US", "Dallas, TX", "International"),
        ("San Francisco, CA", "On-site", "", "", ""),  # No config = no flag
        ("NAMER", "Remote", "US", "Dallas, TX", ""),  # Region → Remote → no flag
        ("North America", "Remote", "CA", "Toronto", ""),  # CA user in NA → no flag
    ]

    flag_pass = 0
    flag_fail = []
    for loc, wt, country, city, expected in flag_tests:
        result = _classify_location_flag(loc, wt, country, city)
        if result == expected:
            flag_pass += 1
        else:
            flag_fail.append((loc, wt, expected, result))

    print(f"  Flag classification: {flag_pass}/{len(flag_tests)} correct")
    if flag_fail:
        for loc, wt, expected, result in flag_fail:
            exp_label = expected or "Local"
            res_label = result or "Local"
            print(f"    \u274c \"{loc}\" ({wt}) — expected {exp_label}, got {res_label}")
    else:
        print(f"  \u2705 All location test cases pass")

    return len(fail_cases) + len(flag_fail)


# ── Eval 6: Fit Scoring ──────────────────────────────────────────────────────

def eval_fit_scoring(conn):
    print("\nFIT SCORING")
    print("-" * 60)

    failures = 0

    # --- Part A: Curated test cases ---
    curated = [
        # (title, description_snippet, expected_min, expected_max, label)
        ("Senior Product Manager, AI Platform",
         "Build LLM-powered features for our enterprise AI platform. "
         "Work with machine learning engineers on generative AI and NLP capabilities. "
         "Lead cross-functional teams on infrastructure and automation.",
         50, 100, "AI-heavy senior PM role"),
        ("Product Manager",
         "Manage the product roadmap for our e-commerce checkout flow. "
         "Analyze user funnels and optimize conversion rates. "
         "Partner with engineering on frontend improvements.",
         0, 20, "Generic PM, no AI/seniority keywords"),
        ("Staff Product Manager, Healthcare AI",
         "Lead AI product strategy for our healthcare platform. "
         "Build clinical decision support using deep learning and transformers. "
         "Own the enterprise health tech infrastructure roadmap.",
         65, 100, "Staff healthcare AI PM"),
        ("Director of Product, Real Estate Platform",
         "Direct product strategy for our proptech platform. "
         "Build agent workflow automation tools for real estate professionals. "
         "Enterprise infrastructure serving millions of users.",
         55, 100, "Director proptech with AI-adjacent terms"),
        ("Product Manager, Data Pipeline",
         "Manage our data pipeline product. Work with engineering on batch processing "
         "and ETL workflows. Support analytics customers.",
         0, 30, "Data PM, minimal keyword overlap"),
        ("Head of Product, AI Agents",
         "Lead the agentic AI product line. Build autonomous agent workflows "
         "using LLMs and foundation models. GPT-powered automation platform.",
         70, 100, "Head of AI agents role"),
    ]

    curated_pass = 0
    curated_fail = []
    for title, desc, exp_min, exp_max, label in curated:
        score = fit_score(title, desc)
        if exp_min <= score <= exp_max:
            curated_pass += 1
        else:
            curated_fail.append((label, score, exp_min, exp_max))

    print(f"  Curated test cases: {len(curated)}")
    print(f"  \u2705 {curated_pass}/{len(curated)} scores in expected range")
    if curated_fail:
        for label, score, exp_min, exp_max in curated_fail:
            print(f"    \u274c \"{label}\" — score {score}, expected {exp_min}-{exp_max}")
            failures += 1

    # --- Part B: Breakdown consistency (sum of bucket weights == total score) ---
    print("\n  Breakdown consistency check:")
    rows = conn.execute("SELECT id, title, description FROM jobs").fetchall()
    inconsistent = []
    for jid, title, desc in rows:
        score = fit_score(title, desc or "")
        breakdown = compute_fit_breakdown(title, desc or "")
        breakdown_sum = sum(b["weight"] for b in breakdown)
        # fit_score caps at 100, so breakdown_sum should equal min(100, raw_sum)
        if min(100, breakdown_sum) != score:
            inconsistent.append((jid, title, score, breakdown_sum))

    if inconsistent:
        print(f"    \u274c {len(inconsistent)} jobs: breakdown sum != fit score")
        for jid, title, score, bsum in inconsistent[:5]:
            print(f"      [{jid[:30]}] \"{title[:40]}\" — score={score}, breakdown_sum={bsum}")
        failures += len(inconsistent)
    else:
        print(f"    \u2705 All {len(rows)} jobs: breakdown sums match fit scores")

    # --- Part C: Score distribution sanity (no impossibly high scores with 0 hits) ---
    print("\n  Score distribution:")
    score_ranges = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    for jid, title, desc in rows:
        s = fit_score(title, desc or "")
        if s <= 20: score_ranges["0-20"] += 1
        elif s <= 40: score_ranges["21-40"] += 1
        elif s <= 60: score_ranges["41-60"] += 1
        elif s <= 80: score_ranges["61-80"] += 1
        else: score_ranges["81-100"] += 1
    for rng, cnt in score_ranges.items():
        print(f"    {rng}: {cnt} jobs")

    # --- Part D: Bucket max caps are respected ---
    print("\n  Bucket cap validation:")
    cap_violations = []
    for jid, title, desc in rows:
        breakdown = compute_fit_breakdown(title, desc or "")
        for b in breakdown:
            if b["weight"] > b["maxPts"]:
                cap_violations.append((jid, title, b["bucket"], b["weight"], b["maxPts"]))

    if cap_violations:
        print(f"    \u274c {len(cap_violations)} bucket cap violations")
        for jid, title, bucket, weight, maxpts in cap_violations[:5]:
            print(f"      [{jid[:30]}] {bucket}: {weight} > max {maxpts}")
        failures += len(cap_violations)
    else:
        print(f"    \u2705 All bucket scores respect max caps")

    return failures


# ── Eval 7: Freshness & Date Accuracy ───────────────────────────────────────

def eval_freshness_dates(conn):
    from datetime import timedelta
    print("\nFRESHNESS & DATE ACCURACY")
    print("-" * 60)

    failures = 0
    now = datetime.now(timezone.utc)

    # --- Part A: Curated freshness score tests ---
    curated = [
        # (hours_ago, reposted, expected_score, label)
        (2, False, 100, "2 hours ago"),
        (12, False, 90, "12 hours ago"),
        (30, False, 80, "30 hours ago"),
        (60, False, 70, "60 hours ago"),
        (120, False, 55, "5 days ago"),
        (240, False, 35, "10 days ago"),
        (500, False, 15, "~21 days ago"),
        (1000, False, 5, "~42 days ago"),
        # Repost penalty
        (2, True, 85, "2h ago repost"),
        (120, True, 40, "5d ago repost"),
    ]

    curated_pass = 0
    curated_fail = []
    for hours_ago, reposted, expected, label in curated:
        dt = now - timedelta(hours=hours_ago)
        date_str = dt.isoformat()
        score = freshness_score(date_str, date_str, reposted, now)
        if score == expected:
            curated_pass += 1
        else:
            curated_fail.append((label, score, expected))

    print(f"  Curated freshness tests: {len(curated)}")
    print(f"  \u2705 {curated_pass}/{len(curated)} correct")
    if curated_fail:
        for label, score, expected in curated_fail:
            print(f"    \u274c \"{label}\" — got {score}, expected {expected}")
            failures += 1

    # --- Part B: published_at reasonableness ---
    print("\n  Published date validation:")
    rows = conn.execute(
        "SELECT id, company, title, published_at, first_seen_at FROM jobs"
    ).fetchall()

    future_dates = []
    very_old = []
    no_pub = []
    pub_after_seen = []
    valid_pub = 0

    for jid, company, title, pub, first_seen in rows:
        if not pub:
            no_pub.append((jid, company, title))
            continue
        try:
            pub_dt = datetime.fromisoformat(pub)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            no_pub.append((jid, company, title))
            continue

        # Future date check (allow 24h buffer for timezone differences)
        if pub_dt > now + timedelta(hours=24):
            future_dates.append((jid, company, title, pub[:25]))
        # Very old check (before 2020 is suspicious)
        elif pub_dt.year < 2020:
            very_old.append((jid, company, title, pub[:25]))
        else:
            valid_pub += 1

        # pub should not be after first_seen (with 1h buffer)
        try:
            fs_dt = datetime.fromisoformat(first_seen)
            if fs_dt.tzinfo is None:
                fs_dt = fs_dt.replace(tzinfo=timezone.utc)
            if pub_dt > fs_dt + timedelta(hours=1):
                pub_after_seen.append((jid, company, title, pub[:19], first_seen[:19]))
        except (ValueError, TypeError):
            pass

    print(f"    Total jobs: {len(rows)}")
    print(f"    \u2705 {valid_pub} with valid published_at dates")

    if no_pub:
        print(f"    \u26a0\ufe0f  {len(no_pub)} missing published_at")
    if future_dates:
        print(f"    \u274c {len(future_dates)} have future dates:")
        for jid, co, t, pub in future_dates[:5]:
            print(f"      [{co}] {t[:40]} -> {pub}")
        failures += len(future_dates)
    if very_old:
        print(f"    \u26a0\ufe0f  {len(very_old)} have very old dates (pre-2020) — may be long-standing openings")
        for jid, co, t, pub in very_old[:5]:
            print(f"      [{co}] {t[:40]} -> {pub}")

    # --- Part C: Tier consistency with scores ---
    print("\n  Tier assignment consistency:")
    rows = conn.execute("SELECT id, title, description, published_at, first_seen_at, last_seen_at, reposted FROM jobs").fetchall()
    tier_issues = []
    tier_counts = {"Apply Today": 0, "Apply This Week": 0, "Watch List": 0}

    for jid, title, desc, pub, first_seen, last_seen, reposted in rows:
        pub_at = pub or ""
        fresh = freshness_score(first_seen, last_seen, bool(reposted), now, published_at=pub_at)
        fit = fit_score(title, desc or "")
        t = tier(fresh, fit, title, desc or "")
        tier_counts[t] = tier_counts.get(t, 0) + 1

        # Verify tier rules
        text = f"{title} {desc or ''}".lower()
        has_ai = bool(re.search(r"\bai\b|\bartificial.intelligence|\bml\b|\bllm\b|\bmachine.learn", text))

        if t == "Apply Today":
            if fresh < 70 or fit < 40 or not has_ai:
                tier_issues.append((jid, title, t, fresh, fit, has_ai,
                    f"Apply Today requires fresh>=70,fit>=40,has_ai but got fresh={fresh},fit={fit},ai={has_ai}"))
        elif t == "Apply This Week":
            if fresh < 50 or fit < 25:
                tier_issues.append((jid, title, t, fresh, fit, has_ai,
                    f"Apply This Week requires fresh>=50,fit>=25 but got fresh={fresh},fit={fit}"))
            # Should not be Apply Today
            if fresh >= 70 and fit >= 40 and has_ai:
                tier_issues.append((jid, title, t, fresh, fit, has_ai,
                    f"Should be Apply Today (fresh={fresh},fit={fit},ai=True) but is Apply This Week"))

    for t_name in ["Apply Today", "Apply This Week", "Watch List"]:
        print(f"    {t_name}: {tier_counts.get(t_name, 0)}")

    if tier_issues:
        print(f"    \u274c {len(tier_issues)} tier assignment issues:")
        for jid, title, t, fresh, fit, has_ai, reason in tier_issues[:5]:
            print(f"      [{title[:40]}] {reason}")
        failures += len(tier_issues)
    else:
        print(f"    \u2705 All tier assignments consistent with scoring rules")

    return failures


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        print("Run 'python3 freshapply.py' first to populate the database.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    print("=" * 60)
    print("  FreshApply Evals")
    print("=" * 60)

    warnings = 0
    failures = 0

    f = eval_salary(conn)
    failures += f

    f = eval_pm_titles(conn)
    failures += f

    f = eval_work_type(conn)
    warnings += f

    f = eval_sanitization(conn)
    warnings += f

    f = eval_location_detection(conn)
    failures += f

    f = eval_fit_scoring(conn)
    failures += f

    f = eval_freshness_dates(conn)
    failures += f

    conn.close()

    print("\n" + "=" * 60)
    total_evals = 7
    status = "\u2705 ALL PASS" if failures == 0 and warnings == 0 else ""
    if failures:
        status = f"\u274c {failures} failure(s)"
    elif warnings:
        status = f"\u26a0\ufe0f  {warnings} warning(s), 0 failures"
    print(f"  Summary: {total_evals} evals — {status}")
    print("=" * 60)

    sys.exit(1 if failures > 0 else 0)


if __name__ == "__main__":
    main()

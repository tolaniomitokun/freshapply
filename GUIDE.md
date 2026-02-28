# FreshApply — Setup Guide

Build your own AI PM job dashboard in under 5 minutes. No coding required.

---

## What You'll Get

A personal, interactive dashboard that:
- Tracks 300+ PM and AI PM roles across 65+ companies (OpenAI, Anthropic, Stripe, Databricks, etc.)
- Scores each job on **freshness** (how new) and **fit** (how well it matches your background)
- Labels jobs as **Today**, **This Week**, or **1 Week+** so you know what to prioritize
- Flags jobs that would require **relocation** or are **international**
- Generates a **tailored resume** for any job with one click
- Runs entirely on your laptop — your data never leaves your machine

---

## Step 1: Install Python (if you don't have it)

### Mac
Open **Terminal** (search for "Terminal" in Spotlight) and type:

```
python3 --version
```

If you see a version number (3.10 or higher), you're good — skip to Step 2.

If not, install it:
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Click the big yellow **Download** button
3. Open the downloaded file and follow the installer

### Windows
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Click the big yellow **Download** button
3. **Important**: On the first installer screen, check the box that says **"Add Python to PATH"**
4. Click **Install Now**

To verify, open **Command Prompt** (search for "cmd") and type:
```
python --version
```

---

## Step 2: Download FreshApply

### Option A: Download as ZIP (easiest)
1. Go to [github.com/tolaniomitokun/freshapply](https://github.com/tolaniomitokun/freshapply)
2. Click the green **Code** button
3. Click **Download ZIP**
4. Unzip the folder somewhere you'll remember (e.g., your Desktop)

### Option B: Clone with Git (if you have Git installed)
```
git clone https://github.com/tolaniomitokun/freshapply.git
```

---

## Step 3: Set Up Your Resume

This step personalizes the dashboard to your background.

1. Open the `freshapply` folder
2. Find the file called `resume.example.json`
3. Make a copy and rename it to `resume.json`
   - **Mac**: In Finder, right-click → Duplicate, then rename
   - **Windows**: Right-click → Copy, then Paste, then rename
4. Open `resume.json` in any text editor (TextEdit on Mac, Notepad on Windows, or VS Code if you have it)
5. Replace the placeholder text with your real information

### What to fill in

The most important fields:

| Field | Example | Why it matters |
|-------|---------|----------------|
| `name` | `"Jane Smith"` | Used on tailored resumes |
| `country` | `"US"` | Flags international jobs |
| `city` | `"New York, NY"` | Flags jobs requiring relocation |
| `headline` | `"AI PRODUCT MANAGER"` | Your title on tailored resumes |
| `experience` | Your real jobs + bullets | Reordered per job for best match |

**Tips:**
- Keep the JSON format exactly as-is — just replace the text between the quote marks `" "`
- If you're not sure about formatting, just update `name`, `country`, `city`, and `headline` — the rest is optional
- Don't delete any commas, brackets, or braces

### Quick check

Your `resume.json` should start like this (with your info):
```json
{
  "name": "JANE SMITH",
  "country": "US",
  "city": "New York, NY",
  "contact": "New York, NY  |  jane@email.com  |  555-555-5555",
  ...
```

---

## Step 4: Run It

Open **Terminal** (Mac) or **Command Prompt** (Windows).

Navigate to the freshapply folder:

```
cd ~/Desktop/freshapply
```

> **Note**: Replace `~/Desktop/freshapply` with wherever you saved the folder. On Windows, it might be `cd C:\Users\YourName\Desktop\freshapply`

Then run:

```
python3 freshapply.py
```

> **Windows users**: If `python3` doesn't work, try `python freshapply.py` instead.

This will take 2-3 minutes. You'll see it scanning companies:

```
Scraping greenhouse: anthropic ... 12 PM roles
Scraping greenhouse: openai ... 8 PM roles
...
✅ Dashboard written → digests/dashboard-2026-02-28.html
```

---

## Step 5: Open Your Dashboard

1. Go to the `digests` folder inside `freshapply`
2. Double-click the file named `dashboard-YYYY-MM-DD.html` (today's date)
3. It opens in your browser — that's your dashboard!

---

## Using the Dashboard

### Cards
Each card is a job posting with:
- **Title** and **company**
- **Today** / **This Week** / **1 Week+** label — how urgently to apply
- **Freshness score** — how recently it was posted
- **Fit score** — how well it matches your resume keywords
- **Salary** (when listed in the job posting)
- **Relocation** / **International** badges based on your location

### Filters (top bar)
- **Search** — type to find jobs by title, company, or location
- **Tier chips** — click Today, This Week, or 1 Week+ to filter
- **Work type** — filter by Remote, Hybrid, or On-site
- **Location** — filter by Local, Relocation, or International
- **Status** — filter by New, Saved, Applied, Interviewing, Rejected
- **Company dropdown** — filter by specific company
- **Salary dropdown** — filter by minimum salary

### Job Details
Click any card to open the full details:
- Full job description
- Score breakdown showing exactly which keywords matched
- **Resume Gap Analysis** — shows which keywords from this job are missing from your resume, with suggested bullet points
- **Generate Tailored Resume** — downloads a Word-compatible resume reordered for this specific job
- **Notes** — add personal notes (interview prep, referral contacts, etc.)

### Tracking Applications
Use the dropdown on each card to mark jobs as:
- **Saved** — bookmarked for later
- **Applied** — you've submitted an application
- **Interviewing** — you're in the process
- **Rejected** — didn't work out

All your statuses, notes, and hidden jobs are saved in your browser automatically.

### Upload Your Resume
Click **Upload Resume** in the top bar to paste or upload your resume text. This does two things:
1. **Recalculates fit scores** based on your actual resume content
2. Uses your resume for **tailored resume downloads**

---

## Updating Daily

Run the same command each day to get fresh jobs:

```
python3 freshapply.py
```

New jobs get added. Existing jobs update their freshness scores. Your statuses and notes are preserved.

If you just want to regenerate the dashboard without re-scraping (faster):

```
python3 freshapply.py --digest
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `python3: command not found` | Try `python` instead, or install Python from python.org |
| `No module named ...` | You shouldn't see this — FreshApply has zero dependencies. Make sure you're using Python 3.10+ |
| `resume.json` errors | Make sure your JSON is valid — check for missing commas or quotes. You can paste it into [jsonlint.com](https://jsonlint.com) to validate |
| Dashboard is blank | Check that the `digests/` folder has a `.html` file. Re-run `python3 freshapply.py` |
| Old dates showing | The file is named by UTC date. If it's late evening, tomorrow's file may already exist — check for the latest one |

---

## Companies Tracked (65+)

The dashboard scrapes public job APIs from:

**AI-Native**: Anthropic, OpenAI, Cohere, Character.AI, DeepMind, ElevenLabs, Mistral, Perplexity, Scale AI, Stability AI, Together AI, and more

**AI-Heavy Tech**: Airbnb, Coinbase, Cursor, Databricks, Datadog, Figma, Notion, Ramp, Replit, Stripe, Zapier, and more

Want to add a company? Open `freshapply.py` and add their ATS board slug to the company list at the top of the file.

---

*Built with Python. No API keys, no accounts, no cloud. Your data stays on your machine.*

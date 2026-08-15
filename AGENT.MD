# Job Scraper — Implementation Spec

## Goal
Build a Python scraper that runs on GitHub Actions 3-4x/day, checks a fixed list of
company career pages for new job postings (diffed against previously-seen jobs), and
emails a summary when new postings are found. No Telegram, no Slack — email only.

---

## 1. Project structure

```
job-scraper/
├── .github/workflows/scrape.yml
├── scraper/
│   ├── __init__.py
│   ├── main.py              # entrypoint, orchestrates all companies
│   ├── companies_tier1.py   # Tier 1: API/SSR companies (fast, no browser)
│   ├── companies_tier2.py   # Tier 2: Playwright-required companies
│   ├── diff.py              # load/save seen_jobs.json, compute new postings
│   ├── notify.py            # email sending
│   └── models.py            # JobPosting dataclass
├── data/
│   └── seen_jobs.json       # committed back to repo each run — DO NOT gitignore
├── requirements.txt
└── README.md
```

## 2. Data model

```python
# scraper/models.py
from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class JobPosting:
    company: str          # e.g. "Amazon"
    job_id: str           # unique ID from source, used for dedup
    title: str
    location: Optional[str]
    url: Optional[str]    # link to the posting if available
    posted_date: Optional[str]  # ISO date string if available, else None
    tier: str             # "1" or "2"

    def dedup_key(self) -> str:
        return f"{self.company}:{self.job_id}"
```

## 3. Diffing logic (`scraper/diff.py`)

- `data/seen_jobs.json` is a flat JSON object: `{"Amazon:12345": {"first_seen": "2026-08-15T10:00:00Z", "title": "..."}, ...}`
- On each run:
  1. Load existing `seen_jobs.json` (empty dict if missing).
  2. For each company scraper, fetch current postings.
  3. For each posting, compute `dedup_key()`. If not in the loaded dict → it's NEW.
  4. Collect all NEW postings into a list for the email.
  5. Add all current postings (new and old) into the dict with `first_seen` timestamp
     (only set `first_seen` for genuinely new keys; keep existing timestamp for old keys).
  6. **Prune**: remove any key from the dict that wasn't seen in the last 30 days AND
     wasn't in this run's results — keeps the file from growing forever. (Simple approach:
     only keep keys that appeared in this run OR the last N runs; simplest is to just
     keep everything — job boards rarely exceed a few thousand entries total across
     25 companies, so pruning is optional, skip it in v1 unless file size becomes an issue.)
  7. Write the updated dict back to `data/seen_jobs.json`.
  8. If NEW postings list is non-empty → call `notify.send_email(new_postings)`.
  9. Commit `data/seen_jobs.json` back to the repo (done in the GitHub Actions workflow
     step, not in Python).

- Wrap each company's fetch in try/except — one company failing (site down, API changed)
  must NOT crash the whole run. Log the error, skip that company, continue with the rest.
  Collect a list of "failed companies" and include it as a small note at the bottom of
  the email if non-empty, so the user knows to check that source manually.

## 4. Email notification (`scraper/notify.py`)

Use Python's built-in `smtplib` with an SMTP provider — recommend Gmail SMTP with an
**App Password** (not the main Gmail password) since it's free and simple for personal use.

```python
import smtplib
from email.mime.text import MIMEText
import os

def send_email(new_postings: list[JobPosting], failed_companies: list[str]):
    if not new_postings:
        return

    body_lines = [f"{len(new_postings)} new job posting(s) found:\n"]
    # group by company
    by_company = {}
    for p in new_postings:
        by_company.setdefault(p.company, []).append(p)

    for company, jobs in sorted(by_company.items()):
        body_lines.append(f"\n=== {company} ({len(jobs)}) ===")
        for j in jobs:
            loc = f" — {j.location}" if j.location else ""
            date = f" ({j.posted_date})" if j.posted_date else ""
            body_lines.append(f"  • {j.title}{loc}{date}")
            if j.url:
                body_lines.append(f"    {j.url}")

    if failed_companies:
        body_lines.append(f"\n\n⚠ Failed to check: {', '.join(failed_companies)}")

    body = "\n".join(body_lines)

    msg = MIMEText(body)
    msg["Subject"] = f"Job Alert: {len(new_postings)} new posting(s)"
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = os.environ["SMTP_TO"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["SMTP_FROM"], os.environ["SMTP_PASSWORD"])
        server.send_message(msg)
```

- Required env vars / GitHub Secrets: `SMTP_FROM`, `SMTP_PASSWORD` (Gmail App Password —
  generate at https://myaccount.google.com/apppasswords, requires 2FA enabled on the
  Google account), `SMTP_TO` (can be the same address, or a different inbox).
- If `new_postings` is empty, send nothing (no point getting 4 empty emails a day).
  Optionally add a once-daily "heartbeat" email confirming the scraper ran with 0 new
  postings, but default to silent-on-empty for v1.

## 5. Tier 1 companies — exact sources to fetch

All of these are plain HTTP GET/POST returning JSON or HTML. No browser needed.
Use the `requests` library with a normal browser User-Agent header
(`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36`) to avoid trivial
bot-blocking, and a timeout of 15s per request.

### Greenhouse companies (identical pattern — write ONE reusable function)

```
GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
```

| Company | board_token |
|---|---|
| Harness | `harnessinc` |
| Razorpay | `razorpaysoftwareprivatelimited` |
| Arcesium | `arcesiumllc` |

Response JSON shape: `{"jobs": [{"id": ..., "title": ..., "location": {"name": ...}, "absolute_url": ..., "updated_at": ..., "first_published": ...}, ...]}`
Use `id` as `job_id`, `updated_at` as `posted_date`, `absolute_url` as `url`.

### Jibe (Phenom-adjacent) companies — identical pattern

```
GET https://{domain}/api/jobs?page=1&sortBy=relevance&descending=false&internal=false
```

| Company | domain |
|---|---|
| S&P Global | `careers.spglobal.com` |
| Schneider Electric | `careers.se.com` |

Response JSON shape: `{"jobs": [{"data": {"req_id": ..., "title": ..., "slug": ..., ...}}]}`.
Paginate by incrementing `page=` until an empty `jobs` array is returned (check for a
`meta.total` or similar field in the actual response and cap pagination accordingly —
inspect the live response structure since this wasn't fully captured; there is no
`updated_at`/date field confirmed for this platform, treat `job_id = req_id` and leave
`posted_date = None`).

### Eightfold companies — identical pattern

```
GET https://{domain}/api/pcsx/search?domain={domain}&query=&location=&start=0
```

| Company | domain (also used as the `domain` query param) |
|---|---|
| Microsoft | `apply.careers.microsoft.com` (query param `domain=microsoft.com`) |
| NVIDIA | `jobs.nvidia.com` (query param `domain=nvidia.com`) |
| Qualcomm | `careers.qualcomm.com` (query param `domain=qualcomm.com`) |

Response JSON shape: `{"data": {"positions": [{"id": ..., "displayJobId": ..., "name": ..., "locations": [...], "postedTs": <epoch>, "positionUrl": ...}], "count": <total>}}`.
Paginate by incrementing `start=` by the page size (check `count` in response) until
`start >= count`. Use `id` as `job_id`, `name` as `title`, `postedTs` (epoch seconds) →
convert to ISO date for `posted_date`, build full URL as `https://{domain}{positionUrl}`.

### Oracle Fusion Cloud companies — identical pattern

```
GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={site},limit=25,sortBy=POSTING_DATES_DESC,offset={offset}
```

| Company | host | siteNumber |
|---|---|---|
| JPMorgan | `jpmc.fa.oraclecloud.com` | `CX_1001` |
| Texas Instruments | `edbz.fa.us2.oraclecloud.com` | `CX` |

Response JSON shape: `{"items": [{"TotalJobsCount": <int>, "requisitionList": [{"Id": ..., "Title": ..., "PostedDate": "YYYY-MM-DD", "PrimaryLocationCountry": ...}]}]}`.
Paginate using `offset=` in steps of 25 until `offset >= TotalJobsCount` (read
`TotalJobsCount` from `items[0]`). Use `Id` as `job_id`, `Title` as `title`,
`PostedDate` as `posted_date`.

### Workday CxS companies

```
POST https://{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
Content-Type: application/json
Body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
```

| Company | Full endpoint |
|---|---|
| Salesforce | `https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site/jobs` |

Response JSON shape: `{"total": <int>, "jobPostings": [{"title": ..., "externalPath": ..., "postedOn": "Posted Yesterday" (relative string), "bulletFields": ["<req_id>"]}]}`.
Paginate by incrementing `offset` by 20 until `offset >= total`. Use `bulletFields[0]`
as `job_id` (fallback to hashing `externalPath` if empty), build `url` as
`https://salesforce.wd12.myworkdayjobs.com/External_Career_Site{externalPath}`.
Note: `postedOn` is a relative string ("Posted Today", "Posted 3 Days Ago") — do not
try to parse this into a precise date; store it as a raw string in `posted_date` or
leave it as informational-only, since diffing is already handled by `job_id`.

### Custom / one-off companies

**Amazon**
```
GET https://www.amazon.jobs/en/search.json?result_limit=100&sort=recent&base_query=software%20engineer&country=IND&offset={offset}
```
Loop with `offset` in steps of 100, using `hits` field in response for total count.
Response: `{"hits": <int>, "jobs": [{"id_icims": ..., "title": ..., "posted_date": "Month DD, YYYY", "job_path": ...}]}`.
`job_id = id_icims`, `url = "https://www.amazon.jobs" + job_path`.
Run this query once per role/keyword you care about (e.g. once for `base_query=software%20engineer`), adjust `base_query` and `country` as needed.

**Atlassian**
```
GET https://www.atlassian.com/endpoint/careers/listings
```
Single GET, no pagination — returns the full list of all open jobs company-wide as a
JSON array: `[{"id": ..., "title": ..., "portalJobPost": {"updatedDate": "YYYY-MM-DD HH:MM AM/PM", "portalUrl": ...}, "locations": [...]}, ...]`.
`job_id = id`, `posted_date = portalJobPost.updatedDate`, `url = portalJobPost.portalUrl`.

**Apple**
```
GET https://jobs.apple.com/en-us/search?location=india-INDC
```
This is server-rendered HTML. Fetch the page, then extract job data from an inline
`<script>` tag containing `window.__staticRouterHydrationData = JSON.parse("...")` —
the value is a JSON string that itself needs a second `json.loads()` after unescaping.
Locate the `searchResults` or job list array inside that parsed structure (fields seen:
`positionId`, `postingDate`, `postingTitle`, `postDateInGMT`, `transformedPostingTitle`).
Build `url` as `https://jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}`.
Adjust `location=` query param per region of interest; call once per region.

**Databricks**
```
GET https://www.databricks.com/company/careers/open-positions
```
Fully server-rendered plain HTML, single page, no pagination (entire board is on one
page). Parse with BeautifulSoup: job titles and locations appear as repeated text
blocks under category headers (Engineering, Sales, etc.) — inspect the actual DOM
structure with browser devtools before writing selectors, since no stable IDs were
captured during research. There is no numeric job ID or posting date visible in this
markup — use a hash of `(title, location)` as a synthetic `job_id`, and leave
`posted_date = None`. This means "new" postings will be detected the first time a
distinct title+location combination appears.

**Uber**
```
GET https://jobs.uber.com/en/jobs/
```
Server-rendered HTML, plain GET returns full HTML with job titles/locations/teams
already present. Parse with BeautifulSoup — inspect DOM structure for stable
selectors/IDs before implementing (not fully captured during research). Likely
paginated (~69 pages seen); check for a `?page=N` query param pattern by clicking
"Next" in a browser and observing the URL, or look for pagination links in the HTML.
No numeric job ID confirmed — fall back to hash of `(title, location, team)` as
synthetic `job_id` if no ID field is found in the markup.

**Intuit**
```
GET https://jobs.intuit.com/search-jobs?k=software%20engineer&l=&orgIds=27595
```
Server-rendered HTML (TalentBrew platform). Parse job titles/locations directly from
HTML. Add `&sortBy=Date` or similar (check the actual UI sort dropdown options — the
"Date Posted" sort exists but the exact query param wasn't captured) to fetch newest
first. Paginate via `&startrow=10`, `&startrow=20` etc. (guess based on common
TalentBrew patterns — verify against the live site) or via the "Load more" mechanism if
`startrow` doesn't work. No stable job ID confirmed in visible markup — inspect for a
`data-job-id` attribute on each result card; fall back to hash of `(title, location)`
if none found. Run once per keyword of interest (adjust `k=`).

**EA**
```
GET https://jobs.ea.com/en_US/careers/SearchJobs
```
Server-rendered HTML (Avature platform). Parse job titles/locations/Role IDs directly —
"Role ID" was visible in page text during research (e.g. "Role ID 215905"), which is a
strong, stable `job_id` candidate. Locate this in the HTML via BeautifulSoup selectors
(inspect actual markup — not fully captured). Check for a `?jobRecordsPerPage=` or
similar pagination query param by browsing the live site.

**Incepto**
```
GET https://join.com/companies/incepto-medical
```
Server-rendered Next.js page. Parse job data from the `<script id="__NEXT_DATA__">` tag
(standard Next.js JSON, no unescaping needed unlike Apple's). Path into the JSON:
`props.pageProps.initialState.jobs.items` — this was empty (0 open roles) at last
check, so implement it but expect it may often return an empty list; that's correct
behavior, not a bug.

**D.E. Shaw**
```
Step 1: GET https://www.deshaw.com/careers
Step 2: GET https://www.deshaw.com/_next/data/{buildId}/en/careers.json
```
The `buildId` (e.g. `ZWwCFNzEKkk17ccy1IDCA`) changes every time D.E. Shaw redeploys
their site, so it CANNOT be hardcoded. On every run: fetch step 1's HTML, extract the
buildId from the `<script id="__NEXT_DATA__">` JSON (`buildId` field at the top level of
that JSON), then construct and fetch the step 2 URL using the fresh buildId. Parse job
listings from the resulting JSON — inspect its structure directly since the schema
wasn't captured in detail (expect fields like title, location, department, and possibly
a job ID or slug per posting).

## 5b. Adobe & Warner Bros Discovery (Phenom People, POST /widgets) — DEFER

These use `POST https://{domain}/widgets` with a request body that requires proper
browser-based reverse engineering (open Chrome DevTools → Network tab → filter by
`/widgets` → search on the live site → copy the exact request payload as cURL). This
was not fully captured. **Do not attempt to guess the payload** — implement these two
as a follow-up task once the payload shape is confirmed manually. Skip them in v1;
comment them out in the company list with a `# TODO: needs /widgets payload from devtools`.

Domains: `careers.adobe.com` (Adobe), `careers.wbd.com` (Warner Bros Discovery).

## 5c. Tower Research Capital — verify before trusting

```
GET https://api.smartrecruiters.com/v1/companies/TowerResearchCapitalLLC/postings
```
This endpoint works and requires no auth, but returned only 2 postings from 2015/2017
during research — almost certainly meaning Tower's *current* live postings are NOT on
SmartRecruiters (they may have migrated to Greenhouse based on secondary evidence).
Implement this endpoint, but flag it: if a scrape returns fewer than 5 total postings
company-wide, log a warning (not an error) so it surfaces in the "failed/suspect
companies" section of the email, prompting a manual re-check of where Tower's careers
page actually points.

## 6. Tier 2 companies — Playwright required (separate, less-frequent job)

These need a headless browser because their data endpoints require session cookies
or POST bodies not yet reverse-engineered. Implement using `playwright` (Python,
`sync_playwright` or `async_playwright`), running headless Chromium.

Run this Tier 2 job **once per day**, not 3-4x/day, in a separate GitHub Actions
workflow (or a separate job step gated by a cron schedule check) to keep runtime and
Action-minutes low, since Playwright startup is slow (~30-60s/site).

| Company | URL to load | Notes |
|---|---|---|
| Nutanix | `https://careers.nutanix.com/en/jobs/` | Umbraco/PageUp job board. Load the page with Playwright, wait for job list to render (`page.wait_for_selector` on a job-card CSS class — inspect live), then extract job title/location/link from the rendered DOM. |
| ServiceNow | `https://careers.servicenow.com/jobs/` | Same Umbraco/PageUp platform as Nutanix — same approach. |
| Goldman Sachs | `https://www.goldmansachs.com/careers/students/positions` → click through to "Open Roles" link found on page | Next.js SSR with no JSON API found; render with Playwright and parse the DOM directly, or intercept Playwright's network requests during page load and check if a hidden JSON API call appears (`page.on("response", ...)`) — worth checking again live since v1 research may have missed it. |
| Google | `https://www.google.com/about/careers/applications/jobs/results/` | Render with Playwright, apply a search query via the UI or URL query param `?q=...`, wait for results to load, then parse the DOM directly. Do NOT attempt to call the `batchexecute` RPC endpoint — its wire format is proprietary and not worth reverse engineering. |

For each, since no stable job IDs were captured, use a hash of `(title, location)` as
the synthetic `job_id`, same as the Databricks/Uber fallback approach in Tier 1.

## 7. Companies to leave out of v1 (unresolved)

- **Cohesity** — careers page was under maintenance during research; ATS platform
  unknown. Skip entirely in v1; add a TODO comment.
- **Concentrix** — migrated to a WordPress site with a custom "JDQ" plugin; data
  endpoint not identified. Skip entirely in v1; add a TODO comment.

## 8. GitHub Actions workflow

Create two workflow files.

**`.github/workflows/scrape_tier1.yml`** — runs Tier 1 companies 4x/day:

```yaml
name: scrape-tier1
on:
  schedule:
    - cron: '0 3,8,12,16 * * *'   # adjust times to your local waking hours in UTC
  workflow_dispatch: {}

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt
      - run: python -m scraper.main --tier 1
        env:
          SMTP_FROM: ${{ secrets.SMTP_FROM }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          SMTP_TO: ${{ secrets.SMTP_TO }}
      - name: Commit updated seen_jobs.json
        run: |
          git config user.name "job-scraper-bot"
          git config user.email "bot@users.noreply.github.com"
          git add data/seen_jobs.json
          git diff --staged --quiet || git commit -m "chore: update seen jobs (tier1)"
          git push
```

**`.github/workflows/scrape_tier2.yml`** — runs Tier 2 companies once/day:

```yaml
name: scrape-tier2
on:
  schedule:
    - cron: '0 5 * * *'   # once daily
  workflow_dispatch: {}

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt
      - run: playwright install --with-deps chromium
      - run: python -m scraper.main --tier 2
        env:
          SMTP_FROM: ${{ secrets.SMTP_FROM }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          SMTP_TO: ${{ secrets.SMTP_TO }}
      - name: Commit updated seen_jobs.json
        run: |
          git config user.name "job-scraper-bot"
          git config user.email "bot@users.noreply.github.com"
          git add data/seen_jobs.json
          git diff --staged --quiet || git commit -m "chore: update seen jobs (tier2)"
          git push
```

Both jobs write to the same `data/seen_jobs.json`; since they run on different
schedules there's low risk of a git push race, but if it happens, a simple retry
(`git pull --rebase && git push`) in the commit step is a safe addition.

## 9. requirements.txt

```
requests>=2.31
beautifulsoup4>=4.12
playwright>=1.40
python-dateutil>=2.8
```

## 10. GitHub Secrets to configure (repo Settings → Secrets and variables → Actions)

- `SMTP_FROM` — the Gmail address sending the alert
- `SMTP_PASSWORD` — a Gmail **App Password** (not the account password); generate at
  https://myaccount.google.com/apppasswords (requires 2-Step Verification enabled)
- `SMTP_TO` — destination inbox (can be same as SMTP_FROM)

## 11. Build order (do this incrementally, don't build everything then test once)

1. Set up project structure, `models.py`, `diff.py` with unit-testable pure functions.
2. Implement and manually test ONE Greenhouse company end-to-end (e.g. Harness) —
   confirm fetch → parse → diff → email works before adding more companies.
3. Add the rest of the Greenhouse companies (trivial, same function).
4. Add Jibe, Eightfold, Oracle Fusion, Workday groups one at a time, testing each
   against the live API before moving to the next.
5. Add the custom/one-off Tier 1 companies (Amazon, Atlassian, Apple, Databricks,
   Uber, Intuit, EA, Incepto, D.E. Shaw) one at a time — these need actual HTML
   inspection against the live site since exact selectors weren't captured for several.
6. Wire up the GitHub Actions workflow for Tier 1, confirm a scheduled/manual run
   works end-to-end including the email and the git commit-back step.
7. Implement Tier 2 (Playwright) companies one at a time, in the second workflow.
8. Leave Adobe, WBD, Cohesity, Concentrix as explicit TODOs — do not guess at their
   implementations.

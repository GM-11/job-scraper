# Job Scraper — Consolidated Source Spec

All sources below were verified live via browser network inspection. Build each
company as its own function under a shared interface (see `models.py` /
`diff.py` structure from the earlier spec if starting fresh, or slot these into
the existing scraper). Group identical-platform companies into ONE reusable
function per platform rather than duplicating code per company.

Use `requests` with a normal browser User-Agent header
(`Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36`), 15s timeout,
and wrap every company's fetch in try/except so one failure doesn't kill the
whole run.

---

## GROUP 1 — Greenhouse (identical pattern, one function)

```
GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
```

| Company | board_token |
|---|---|
| Harness | `harnessinc` |
| Razorpay | `razorpaysoftwareprivatelimited` |
| Arcesium | `arcesiumllc` |
| Tower Research Capital | `towerresearchcapital` |

Response: `{"jobs": [{"id": ..., "title": ..., "location": {"name": ...}, "absolute_url": ..., "updated_at": ..., "first_published": ...}]}`.
`job_id = id`, `posted_date = updated_at`, `url = absolute_url`. Single call, no
pagination needed — Greenhouse returns the full list at once.

**Note:** Tower Research Capital was previously misidentified as using
SmartRecruiters (`TowerResearchCapitalLLC`) — that identifier returns only 2
stale 2015/2017 postings and must NOT be used. The Greenhouse token above is
confirmed correct (79 live postings, current as of implementation date).

---

## GROUP 2 — Jibe (identical pattern, one function)

```
GET https://{domain}/api/jobs?page=1&sortBy=relevance&descending=false&internal=false
```

| Company | domain |
|---|---|
| S&P Global | `careers.spglobal.com` |
| Schneider Electric | `careers.se.com` |
| DocuSign | `careers.docusign.com` |

Response: `{"jobs": [{"data": {"req_id": ..., "title": ..., "slug": ..., "description": ...}}]}`.
`job_id = req_id`. No confirmed date field in the response — treat as
undated and rely on `job_id` diffing alone. Paginate by incrementing `page=`
until `jobs` comes back empty.

---

## GROUP 3 — Eightfold (identical pattern, one function)

```
GET https://{domain}/api/pcsx/search?domain={domain}&query=&location=&start=0
```

| Company | domain (also used as query param) |
|---|---|
| Microsoft | `apply.careers.microsoft.com` (param: `domain=microsoft.com`) |
| NVIDIA | `jobs.nvidia.com` (param: `domain=nvidia.com`) |
| Qualcomm | `careers.qualcomm.com` (param: `domain=qualcomm.com`) |

Response: `{"data": {"positions": [{"id": ..., "name": ..., "locations": [...], "postedTs": <epoch_seconds>, "positionUrl": ...}], "count": <total>}}`.
`job_id = id`, `title = name`, `posted_date` = convert `postedTs` epoch to
ISO date, `url = "https://{domain}" + positionUrl`. Paginate by incrementing
`start=` by page size until `start >= count`.

---

## GROUP 4 — Oracle Fusion Cloud (identical pattern, one function)

```
GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={site},limit=25,sortBy=POSTING_DATES_DESC,offset={offset}
```

| Company | host | site |
|---|---|---|
| JPMorgan | `jpmc.fa.oraclecloud.com` | `CX_1001` |
| Texas Instruments | `edbz.fa.us2.oraclecloud.com` | `CX` |
| American Express | `egug.fa.us2.oraclecloud.com` | `CX_1` |
| Oracle | `eeho.fa.us2.oraclecloud.com` | `CX_1` |

Response: `{"items": [{"TotalJobsCount": <int>, "requisitionList": [{"Id": ..., "Title": ..., "PostedDate": "YYYY-MM-DD", "PrimaryLocationCountry": ...}]}]}`.
`job_id = Id`, `posted_date = PostedDate`. Read `TotalJobsCount` from
`items[0]`, paginate via `offset` in steps of 25 until `offset >= TotalJobsCount`.

---

## GROUP 5 — Workday CxS (identical pattern, one function)

```
POST https://{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
Content-Type: application/json
Body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
```

| Company | Full endpoint |
|---|---|
| Salesforce | `salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site/jobs` |
| Thomson Reuters | `thomsonreuters.wd5.myworkdayjobs.com/wday/cxs/thomsonreuters/External_Career_Site/jobs` |
| Visa | `visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/jobs` |

Response: `{"total": <int>, "jobPostings": [{"title": ..., "externalPath": ..., "postedOn": "Posted Today"/"Posted N Days Ago" (relative string), "bulletFields": [...]}]}`.
`job_id` = last element of `bulletFields` (the req ID) — fall back to a hash
of `externalPath` if `bulletFields` is empty. `url` = tenant base URL +
`externalPath`. `postedOn` is a relative string, not a parseable date — store
it as informational text only; rely on `job_id` for diffing, not date parsing.
Paginate by incrementing `offset` by 20 until `offset >= total`.

**If adding more Workday companies later:** the tenant subdomain and site name
vary per company and must be discovered by visiting the company's careers page
and watching for a redirect to `{tenant}.wd{N}.myworkdayjobs.com/{site}` — do
not guess these values.

---

## GROUP 6 — Server-rendered HTML, one page per company (no shared function — each needs its own parser)

| Company | URL | Notes |
|---|---|---|
| Apple | `GET jobs.apple.com/en-us/search?location=india-INDC` | Parse the inline `<script>` containing `window.__staticRouterHydrationData = JSON.parse("...")`. The value is a JSON string requiring a second `json.loads()` after unescaping. Fields inside: `positionId`, `postingDate`, `postingTitle`, `postDateInGMT`, `transformedPostingTitle`. Build `url` as `jobs.apple.com/en-us/details/{positionId}/{transformedPostingTitle}`. Adjust `location=` per region, one call per region of interest. |
| Databricks | `GET www.databricks.com/company/careers/open-positions` | Entire board on one page, plain HTML, no pagination. No stable job ID or date visible in markup — use a hash of `(title, location)` as synthetic `job_id`; `posted_date = None`. Inspect live DOM for selectors before implementing (not fully captured during research). |
| Uber | `GET jobs.uber.com/en/jobs/` | Plain GET returns full HTML with jobs baked in — confirmed via direct `fetch()`, no separate XHR fires. No confirmed job ID field — use hash of `(title, location, team)` as synthetic `job_id`. Check for `?page=N` pagination pattern on the live site before implementing. |
| Intuit | `GET jobs.intuit.com/search-jobs?k={keyword}&l=&orgIds=27595` | TalentBrew platform, SSR. No date field visible in list view despite a "Date Posted" sort option existing — inspect live UI for the actual sort query param. No confirmed stable job ID in markup — inspect for a `data-job-id` attribute; fall back to hash of `(title, location)`. Run once per keyword of interest. |
| EA | `GET jobs.ea.com/en_US/careers/SearchJobs` | Avature platform, SSR, confirmed no separate API call fires. "Role ID" is visible in page text (e.g. "Role ID 215905") — use this as the stable `job_id` if it can be reliably parsed from markup near each job listing. |
| Wells Fargo | `GET www.wellsfargojobs.com/en/jobs/` | SSR, 1,753 jobs confirmed live. **Important:** the `/umbraco/jobboard/LatestJobs/GetJobs` widget endpoint does NOT work as a standalone call (404s without full page session) — always use this `/en/jobs/` page directly, not the widget URL. Check pagination pattern on live "Next" button. |

---

## GROUP 7 — Next.js / embedded-JSON SSR (each needs its own parser)

| Company | URL | Notes |
|---|---|---|
| Incepto | `GET join.com/companies/incepto-medical` | Parse `<script id="__NEXT_DATA__">` (standard Next.js JSON, no unescaping needed). Path: `props.pageProps.initialState.jobs.items`. Was empty (0 open roles) at last check — that's expected behavior, not a bug; implement it to handle empty lists gracefully. |
| D.E. Shaw | Two-step: (1) `GET www.deshaw.com/careers` (2) `GET www.deshaw.com/_next/data/{buildId}/en/careers.json` | The `buildId` changes on every D.E. Shaw redeploy and CANNOT be hardcoded. On every run: fetch step 1's HTML, extract `buildId` from the `__NEXT_DATA__` JSON's top-level `buildId` field, then build and fetch step 2's URL with that fresh value. |

---

## GROUP 8 — Atlassian (custom, single company)

```
GET https://www.atlassian.com/endpoint/careers/listings
```

Single unauthenticated GET, no pagination — returns the full company-wide job
list as one JSON array: `[{"id": ..., "title": ..., "portalJobPost": {"updatedDate": "YYYY-MM-DD HH:MM AM/PM", "portalUrl": ...}, "locations": [...]}]`.
`job_id = id`, `posted_date = portalJobPost.updatedDate`, `url = portalJobPost.portalUrl`.

---

## GROUP 9 — Amazon (custom, single company)

```
GET https://www.amazon.jobs/en/search.json?result_limit=100&sort=recent&base_query={keyword}&country={country}&offset={offset}
```

Response: `{"hits": <int>, "jobs": [{"id_icims": ..., "title": ..., "posted_date": "Month DD, YYYY", "job_path": ...}]}`.
`job_id = id_icims`, `url = "https://www.amazon.jobs" + job_path`. Paginate by
incrementing `offset` in steps of 100 using `hits` as the total. Run once per
keyword/country combination of interest.

---

## SPECIAL CASE — Phenom People `/widgets` cluster (harder, needs session handling)

```
POST https://{domain}/widgets
Content-Type: application/json
X-CSRF-TOKEN: <session-bound token — see below>
```

| Company | domain |
|---|---|
| Adobe | `careers.adobe.com` |
| Warner Bros Discovery | `careers.wbd.com` |
| HPE | `careers.hpe.com` |
| Mastercard | `careers.mastercard.com` |

**Confirmed working body shape** (captured live from HPE):
```json
{
  "sortBy": "",
  "subsearch": "{keyword}",
  "from": 0,
  "jobs": true,
  "counts": true,
  "all_fields": ["category","country","state","city","type","postalCode","remote"],
  "pageName": "search-results1",
  "size": 10,
  "clearAll": false,
  "jdsource": "facets",
  "isSliderEnable": false,
  "pageId": "page15",
  "siteType": "external",
  "keywords": "",
  "global": true,
  "selected_fields": {},
  "lang": "en_us",
  "deviceType": "desktop",
  "country": "us",
  "refNum": "{REF_CODE}",
  "ddoKey": "eagerLoadRefineSearchSession"
}
```
`refNum` varies per company (confirmed `HPE1US` for HPE, `MASRUS` for
Mastercard — visible in a `content-us.phenompeople.com/api/{REF_CODE}/...`
network call on each company's page; check this for Adobe/WBD before use).
`pageId` may also vary per company/page — verify against a live capture rather
than assuming HPE's value applies everywhere.

**THE BLOCKER — confirmed, not yet solved:** `X-CSRF-TOKEN` is session-bound.
A token captured from one request fails validation (`{"tokenAvailable": false}`)
when reused in a fresh, cookie-less request. This means:

1. A stateless single `requests.post()` call will NOT work.
2. Implementation requires a `requests.Session()`:
   - First GET the company's search-results page (e.g.
     `careers.hpe.com/us/en/search-results`) to establish cookies and receive
     the token.
   - The token is very likely delivered via either a response header or an
     embedded value in the initial HTML — this was NOT fully confirmed during
     research because the research tool used couldn't inspect response headers
     on cookie-bearing requests. **This is the one open question the coding
     agent needs to resolve empirically**, and `requests` (unlike the browser
     tool used for this research) can freely inspect `response.headers` and
     `response.cookies` — this should be quick to nail down directly in
     Python: fetch the page, print `dict(response.headers)` and
     `response.cookies.get_dict()`, look for anything token/CSRF-shaped, then
     confirm by using it in the follow-up POST.
   - Once found, carry the session's cookies into the `/widgets` POST with the
     extracted token in the `X-CSRF-TOKEN` header.

**Recommendation:** implement Group 1-9 first (all confirmed, straightforward).
Treat this Phenom cluster as a distinct, harder task — get ONE company (HPE
suggested, since its body shape is already fully captured) working end-to-end
with the session/token flow, verify it returns real job data, then reuse the
same session logic for Adobe/WBD/Mastercard by swapping `domain` and `refNum`.

---

## Tier 2 — Requires Playwright (separate, less-frequent job; run 1x/day not 4x/day)

| Company | URL to load | Notes |
|---|---|---|
| Nutanix | `careers.nutanix.com/en/jobs/` | Umbraco/PageUp job board. `GetRecentJobs`/`LatestJobs` endpoints need session cookies not obtainable via plain `requests` — render with Playwright, wait for job cards to load, parse DOM directly. |
| ServiceNow | `careers.servicenow.com/jobs/` | Same Umbraco/PageUp platform and blocker as Nutanix — same approach. |
| Goldman Sachs | `www.goldmansachs.com/careers/students/positions` → click through to "Open Roles" | Next.js SSR, no JSON API surfaced during research — render with Playwright and parse DOM, or intercept network requests during page load in case a hidden JSON API appears (`page.on("response", ...)` in Playwright) — worth re-checking live since this may have been missed. |
| Google | `www.google.com/about/careers/applications/jobs/results/?q={keyword}` | Uses a `batchexecute` RPC with proprietary wire format — do NOT attempt to parse it. Render with Playwright, apply search via URL param, wait for results, parse DOM directly. |
| GlobalLogic | Real board: `careers.hitachi.com/search/globallogic/jobs` | Behind Cloudflare Turnstile bot-challenge — needs Playwright with a realistic browser fingerprint; may still be inconsistent since Turnstile actively targets automation. A SmartRecruiters identifier (`GlobalLogic4`) exists but is STALE (2018 data, only 8 postings) — do not use it. |

For all Tier 2 companies, since no stable job IDs were captured, use a hash of
`(title, location)` as the synthetic `job_id` for diffing purposes.

---

## Excluded — unresolved, do not implement blind

| Company | Status |
|---|---|
| Cohesity | Careers page was in scheduled maintenance during research; underlying ATS never identified. Skip with a `# TODO` comment; re-check manually later. |
| Concentrix | Migrated off Workday to a WordPress site with a custom "JDQ" plugin; data endpoint never found. Skip with a `# TODO` comment. |

---

## Build order

1. Implement Groups 1–5 first (12 companies, all single reusable functions per
   platform, all fully verified with confirmed response shapes).
2. Implement Groups 6–9 next (7 companies, each needs individual HTML/JSON
   parsing, several need live DOM inspection to finalize selectors).
3. Wire up diffing + email notification (per the earlier IMPLEMENTATION_SPEC.md
   if that's still the base) and confirm one full run end-to-end before adding
   more sources.
4. Tackle the Phenom `/widgets` cluster (4 companies) as its own task — solve
   the session/token flow for HPE first, then generalize.
5. Add Tier 2 Playwright companies (5 companies) in a separate, less-frequent
   scheduled job.
6. Leave Cohesity and Concentrix out entirely until manually re-checked.

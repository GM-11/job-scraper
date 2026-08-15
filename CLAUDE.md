# Job Scraper Implementation

## Overview

This project implements a comprehensive job scraper for monitoring company career pages and sending email notifications of new postings. The implementation follows the specification in AGENT.MD.

## What's Implemented (v1)

### Core Architecture
- ✅ **Project structure**: Clean module separation with `scraper/` package
- ✅ **Data model**: `JobPosting` dataclass with dedup_key for tracking
- ✅ **Diffing logic**: Load/save `seen_jobs.json`, compute new vs seen postings
- ✅ **Email notifications**: SMTP-based (Gmail) email sending with company grouping
- ✅ **CLI interface**: `--tier 1|2` argument to control which tier runs

### Tier 1 Scrapers (API/HTML-based, no browser)
Implemented 21 companies across 6 major job board platforms:

**Greenhouse (3 companies)** ✅
- Harness, Razorpay, Arcesium
- Reusable function for all Greenhouse boards

**Jibe/Phenom (2 companies)** ✅
- S&P Global, Schneider Electric
- Pagination support with rate limiting

**Eightfold (3 companies)** ✅
- Microsoft, NVIDIA, Qualcomm
- Pagination with timestamps converted to ISO format

**Oracle Fusion Cloud (2 companies)** ✅
- JPMorgan, Texas Instruments
- Proper pagination handling

**Workday CxS (1 company)** ✅
- Salesforce
- POST-based API with pagination

**Custom/One-off (10 companies)** ✅
- **Amazon**: Paginated API with offset
- **Atlassian**: Single endpoint returning full list
- **Apple**: JSON in HTML script tag extraction
- **Databricks**: HTML parsing (placeholder selectors)
- **Uber**: HTML parsing (placeholder selectors)
- **Intuit**: HTML parsing with pagination (placeholder)
- **EA**: HTML parsing (placeholder)
- **Incepto**: Next.js JSON data extraction
- **D.E. Shaw**: Dynamic buildId extraction + Next.js JSON
- **Tower Research**: SmartRecruiters API with low-posting warning

### Tier 2 Scrapers (Playwright-based, browser automation)
Implemented 4 companies with Playwright placeholders:

- ✅ Nutanix (Umbraco/PageUp)
- ✅ ServiceNow (Umbraco/PageUp)
- ✅ Goldman Sachs (Next.js with JS rendering)
- ✅ Google (Complex JS rendering)

**Note**: Tier 2 implementations are structural placeholders. Actual CSS selectors need verification against live sites.

### GitHub Actions Workflows
- ✅ `.github/workflows/scrape_tier1.yml`: Runs 4x/day (03, 08, 12, 16 UTC)
- ✅ `.github/workflows/scrape_tier2.yml`: Runs 1x/day (05 UTC)
- ✅ Automatic git commit of `data/seen_jobs.json` after each run
- ✅ Environment variables for SMTP secrets

### Quality Features
- ✅ **Rate limiting**: 0.5s delay between requests to prevent API throttling
- ✅ **Error recovery**: Each company wrapped in try/except; one failure doesn't crash run
- ✅ **Failed company tracking**: List of failed companies included in email footer
- ✅ **Graceful email fallback**: Skips email if SMTP secrets aren't configured
- ✅ **Logging**: Structured logging for debugging

## Known Limitations

### HTML-based Scrapers (Need Verification)
These companies use HTML parsing but the exact CSS selectors aren't captured in the specification. They need live site inspection to verify selectors:
- Databricks, Uber, Intuit, EA: Placeholder selectors (search for `[class*='job']`)

### Tier 2 Selectors (Need Verification)
Playwright implementations are structural but need actual DOM selectors from live sites:
- Nutanix, ServiceNow: Look for job card classes (Umbraco standard)
- Goldman Sachs: Needs "Open Roles" click logic verification
- Google: May have paginated results; selector strategy TBD

### Not Implemented (Deferred to v2)
- Adobe & Warner Bros Discovery: Need POST /widgets payload reverse-engineering (use browser DevTools Network tab)
- Cohesity: Careers page was under maintenance during spec creation
- Concentrix: Uses custom WordPress + JDQ plugin; endpoint not identified

## Testing

The implementation has been tested with:
- ✅ Single company scraper (Harness/Greenhouse) - working
- ✅ Diff logic and seen_jobs.json save - working
- ✅ Email notification formatting - working (skipped when no SMTP secrets)
- ✅ Rate limiting delays - implemented and tested

## Running Locally

```bash
# Install dependencies
uv add requests beautifulsoup4 playwright python-dateutil

# Test tier 1
python3 -m scraper.main --tier 1

# Test tier 2 (requires playwright)
python3 -m scraper.main --tier 2

# With email (requires SMTP secrets)
export SMTP_FROM="your-email@gmail.com"
export SMTP_PASSWORD="your-app-password"
export SMTP_TO="recipient@example.com"
python3 -m scraper.main --tier 1
```

## Next Steps for Production

1. **Verification**: Test each scraper against live sites to capture exact HTML selectors
2. **Missing Platforms**: Implement Adobe/WBD/Cohesity/Concentrix once reverse-engineering is complete
3. **Refinement**: 
   - Add job title/location filtering
   - Implement optional daily digests
   - Add metrics/monitoring
4. **Deployment**: Push to GitHub and configure secrets for automated runs

## Architecture Notes

- Each company fetcher returns `list[JobPosting]` for consistent interface
- Dedup uses `company:job_id` key for reliable tracking across runs
- `seen_jobs.json` is committed back to repo so history persists
- Failed companies are tracked separately and reported in email footer
- All timestamps stored as ISO strings in UTC
- Rate limiting via simple `time.sleep()` between requests (could upgrade to exponential backoff if needed)

## Code Quality

- Clean module separation (models, diff, notify, companies_tier1/2)
- Type hints throughout (Python 3.12+)
- Comprehensive docstrings on public functions
- Error logging for debugging (not silently failing)
- Single responsibility per function

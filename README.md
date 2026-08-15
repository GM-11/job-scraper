# Job Scraper

A Python job scraper that monitors company career pages for new job postings and sends email notifications. Runs on GitHub Actions 3-4x per day.

## Features

- **Tier 1 Scraping**: Fast HTTP-based scrapers for 21+ companies using various job board platforms
  - Greenhouse (Harness, Razorpay, Arcesium)
  - Jibe/Phenom (S&P Global, Schneider Electric)
  - Eightfold (Microsoft, NVIDIA, Qualcomm)
  - Oracle Fusion (JPMorgan, Texas Instruments)
  - Workday CxS (Salesforce)
  - Custom APIs & HTML parsing (Amazon, Atlassian, Apple, Databricks, Uber, Intuit, EA, Incepto, D.E. Shaw, Tower Research)

- **Tier 2 Scraping**: Playwright-based browser automation for JavaScript-heavy sites
  - Nutanix, ServiceNow, Goldman Sachs, Google (runs once daily)

- **Smart Diffing**: Compares current job listings against historical data to detect only new postings
- **Email Notifications**: Sends formatted emails grouped by company when new jobs are found
- **Error Recovery**: Continues scraping other companies even if one fails

## Setup

### 1. Install dependencies

```bash
uv add requests beautifulsoup4 playwright python-dateutil
# or: pip install -r requirements.txt
```

### 2. Configure GitHub Secrets

Add these to your repository's GitHub Actions secrets:

- `SMTP_FROM`: Gmail address sending notifications
- `SMTP_PASSWORD`: Gmail App Password (not your account password)
- `SMTP_TO`: Recipient email address

To generate a Gmail App Password:
1. Enable 2-Step Verification on your Google Account
2. Go to https://myaccount.google.com/apppasswords
3. Select "Mail" and "Windows Computer"
4. Copy the 16-character password

### 3. Deploy

Push this repo to GitHub. The workflows will automatically:

- **Tier 1**: Run at 03:00, 08:00, 12:00, 16:00 UTC (adjust cron times in `.github/workflows/scrape_tier1.yml`)
- **Tier 2**: Run daily at 05:00 UTC (adjust in `.github/workflows/scrape_tier2.yml`)
- Commit updated job data back to `data/seen_jobs.json`
- Email new postings to `SMTP_TO`

## Local Testing

```bash
# Test Tier 1 (without email)
python3 -m scraper.main --tier 1

# Test Tier 2 (requires playwright; without email)
python3 -m scraper.main --tier 2
```

To send emails locally, set environment variables:

```bash
export SMTP_FROM="your-email@gmail.com"
export SMTP_PASSWORD="your-app-password"
export SMTP_TO="recipient@example.com"
python3 -m scraper.main --tier 1
```

## Project Structure

```
job-scraper/
├── scraper/
│   ├── models.py           # JobPosting dataclass
│   ├── diff.py             # Load/save seen_jobs.json, diffing logic
│   ├── notify.py           # Email sending
│   ├── companies_tier1.py   # HTTP-based company scrapers
│   ├── companies_tier2.py   # Playwright-based company scrapers
│   └── main.py             # CLI entrypoint
├── data/
│   └── seen_jobs.json      # Committed back each run
├── .github/workflows/
│   ├── scrape_tier1.yml    # Runs 4x/day
│   └── scrape_tier2.yml    # Runs once/day
└── requirements.txt
```

## Implementation Notes

### Tier 1 Companies
Most are implemented. Some require live site inspection for exact HTML selectors:
- Databricks, Uber, Intuit, EA: HTML parsing (selectors need verification)
- Apple: JSON in script tag extraction
- D.E. Shaw: Dynamic buildId extraction from Next.js

### Tier 2 Companies
Playwright implementations are placeholders. Actual selectors need to be verified by:
1. Opening the page in a browser
2. Inspecting the DOM for stable job-card selectors
3. Testing the wait conditions and query strategies

### Not Implemented (v1)
- **Adobe**: Needs POST /widgets payload reverse-engineering
- **Warner Bros Discovery**: Same as Adobe
- **Cohesity**: Careers page was under maintenance
- **Concentrix**: Uses custom WordPress plugin; endpoint unknown

These can be added as future tasks once the required API details are captured.

## Rate Limiting

The scraper includes delays between requests to avoid rate limiting:
- 0.5s between paginated requests
- 0.5s between company fetches

Adjust `REQUEST_DELAY` in `scraper/companies_tier1.py` if needed.

## Troubleshooting

### "SMTP credentials not configured"
This is expected when testing locally without environment variables. The scraper will complete successfully but skip email sending.

### 429 Too Many Requests
Some APIs rate-limit aggressively. Increase `REQUEST_DELAY` in companies_tier1.py.

### No new postings showing up
Check that `data/seen_jobs.json` exists and is being saved. Run with logging enabled:
```bash
python3 -c "import logging; logging.basicConfig(level=logging.DEBUG); exec(open('scraper/main.py').read())"
```

## Future Improvements

1. Add Cohesity and Concentrix once endpoints are identified
2. Implement Adobe/WBD POST /widgets scrapers
3. Add more company-specific parsers
4. Implement optional digest emails (e.g., daily summary)
5. Add filtering by job title/location keywords

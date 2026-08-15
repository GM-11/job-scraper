import hashlib
import logging
from typing import Optional

from .models import JobPosting


logger = logging.getLogger(__name__)

# "networkidle" hangs/times out on pages with persistent background
# connections (analytics beacons, chat widgets, polling) - these career
# sites all do this. "domcontentloaded" plus an explicit wait for a job
# element is far more reliable.
GOTO_TIMEOUT_MS = 20000
SELECTOR_TIMEOUT_MS = 15000


def hash_job_id(title: str, location: Optional[str] = None, extra: Optional[str] = None) -> str:
    """Create a synthetic job_id from title and location."""
    parts = [title]
    if location:
        parts.append(location)
    if extra:
        parts.append(extra)
    key = "|".join(parts)
    return hashlib.md5(key.encode()).hexdigest()[:16]


def _extract_from_job_cards(page, card_selector: str) -> list[dict]:
    """Query job cards on the page and pull title/location text from each."""
    results = []
    for job_elem in page.query_selector_all(card_selector):
        title_elem = job_elem.query_selector("[class*='title']")
        location_elem = job_elem.query_selector("[class*='location']")
        title = title_elem.inner_text().strip() if title_elem else ""
        location = location_elem.inner_text().strip() if location_elem else None
        if title:
            results.append({"title": title, "location": location})
    return results


def fetch_nutanix() -> list[JobPosting]:
    """Fetch jobs from Nutanix (Umbraco/PageUp platform)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping Nutanix")
        return []

    postings = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://careers.nutanix.com/en/jobs/", wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)

            try:
                page.wait_for_selector("[class*='job']", timeout=SELECTOR_TIMEOUT_MS)
            except Exception:
                logger.warning("Nutanix: job elements never appeared, page structure may have changed")

            for job in _extract_from_job_cards(page, "[class*='job-card']"):
                postings.append(JobPosting(
                    company="Nutanix",
                    job_id=hash_job_id(job["title"], job["location"]),
                    title=job["title"],
                    location=job["location"],
                    url=None,
                    posted_date=None,
                    tier="2"
                ))

            browser.close()
    except Exception as e:
        logger.error(f"Error fetching Nutanix jobs: {e}")

    return postings


def fetch_servicenow() -> list[JobPosting]:
    """Fetch jobs from ServiceNow (Umbraco/PageUp platform)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping ServiceNow")
        return []

    postings = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://careers.servicenow.com/jobs/", wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)

            try:
                page.wait_for_selector("[class*='job']", timeout=SELECTOR_TIMEOUT_MS)
            except Exception:
                logger.warning("ServiceNow: job elements never appeared, page structure may have changed")

            for job in _extract_from_job_cards(page, "[class*='job-card']"):
                postings.append(JobPosting(
                    company="ServiceNow",
                    job_id=hash_job_id(job["title"], job["location"]),
                    title=job["title"],
                    location=job["location"],
                    url=None,
                    posted_date=None,
                    tier="2"
                ))

            browser.close()
    except Exception as e:
        logger.error(f"Error fetching ServiceNow jobs: {e}")

    return postings


def fetch_goldman_sachs() -> list[JobPosting]:
    """Fetch jobs from Goldman Sachs."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping Goldman Sachs")
        return []

    postings = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(
                "https://www.goldmansachs.com/careers/students/positions",
                wait_until="domcontentloaded",
                timeout=GOTO_TIMEOUT_MS
            )

            try:
                open_roles_link = page.query_selector("a:has-text('Open Roles')")
                if open_roles_link:
                    open_roles_link.click()
                    page.wait_for_load_state("domcontentloaded", timeout=GOTO_TIMEOUT_MS)
            except Exception:
                pass

            try:
                page.wait_for_selector("[class*='job']", timeout=SELECTOR_TIMEOUT_MS)
            except Exception:
                logger.warning("Goldman Sachs: job elements never appeared, page structure may have changed")

            for job in _extract_from_job_cards(page, "[class*='job']"):
                postings.append(JobPosting(
                    company="Goldman Sachs",
                    job_id=hash_job_id(job["title"], job["location"]),
                    title=job["title"],
                    location=job["location"],
                    url=None,
                    posted_date=None,
                    tier="2"
                ))

            browser.close()
    except Exception as e:
        logger.error(f"Error fetching Goldman Sachs jobs: {e}")

    return postings


def fetch_google() -> list[JobPosting]:
    """Fetch jobs from Google."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping Google")
        return []

    postings = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(
                "https://www.google.com/about/careers/applications/jobs/results/",
                wait_until="domcontentloaded",
                timeout=GOTO_TIMEOUT_MS
            )

            try:
                page.wait_for_selector("[class*='job']", timeout=SELECTOR_TIMEOUT_MS)
            except Exception:
                logger.warning("Google: job elements never appeared, page structure may have changed")

            for job in _extract_from_job_cards(page, "[class*='job-card']"):
                postings.append(JobPosting(
                    company="Google",
                    job_id=hash_job_id(job["title"], job["location"]),
                    title=job["title"],
                    location=job["location"],
                    url=None,
                    posted_date=None,
                    tier="2"
                ))

            browser.close()
    except Exception as e:
        logger.error(f"Error fetching Google jobs: {e}")

    return postings


# Registry of all tier 2 company fetchers
TIER2_COMPANIES = {
    "Nutanix": fetch_nutanix,
    "ServiceNow": fetch_servicenow,
    "Goldman Sachs": fetch_goldman_sachs,
    "Google": fetch_google,
}


def fetch_all_tier2() -> tuple[list[JobPosting], list[str]]:
    """Fetch all tier 2 companies, return postings and list of failed companies."""
    all_postings = []
    failed = []

    for company_name, fetcher in TIER2_COMPANIES.items():
        try:
            postings = fetcher()
            all_postings.extend(postings)
        except Exception as e:
            logger.error(f"Failed to fetch {company_name}: {e}")
            failed.append(company_name)

    return all_postings, failed

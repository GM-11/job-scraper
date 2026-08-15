import hashlib
import logging
from typing import Optional

from .models import JobPosting


logger = logging.getLogger(__name__)


def hash_job_id(title: str, location: Optional[str] = None, extra: Optional[str] = None) -> str:
    """Create a synthetic job_id from title and location."""
    parts = [title]
    if location:
        parts.append(location)
    if extra:
        parts.append(extra)
    key = "|".join(parts)
    return hashlib.md5(key.encode()).hexdigest()[:16]


def fetch_nutanix() -> list[JobPosting]:
    """Fetch jobs from Nutanix (Umbraco/PageUp platform)."""
    # This requires playwright to be imported
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
            page.goto("https://careers.nutanix.com/en/jobs/", wait_until="networkidle")

            # Wait for job cards to render (adjust selector based on actual DOM)
            try:
                page.wait_for_selector("[class*='job']", timeout=5000)
            except Exception:
                pass

            # Parse job listings from DOM (placeholder - needs actual selector inspection)
            jobs = page.query_selector_all("[class*='job-card']")
            for job_elem in jobs:
                title_elem = job_elem.query_selector("[class*='title']")
                location_elem = job_elem.query_selector("[class*='location']")

                title = title_elem.inner_text() if title_elem else ""
                location = location_elem.inner_text() if location_elem else None

                if title:
                    postings.append(JobPosting(
                        company="Nutanix",
                        job_id=hash_job_id(title, location),
                        title=title,
                        location=location,
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
            page.goto("https://careers.servicenow.com/jobs/", wait_until="networkidle")

            # Wait for job cards to render
            try:
                page.wait_for_selector("[class*='job']", timeout=5000)
            except Exception:
                pass

            # Parse job listings (placeholder - needs actual selector inspection)
            jobs = page.query_selector_all("[class*='job-card']")
            for job_elem in jobs:
                title_elem = job_elem.query_selector("[class*='title']")
                location_elem = job_elem.query_selector("[class*='location']")

                title = title_elem.inner_text() if title_elem else ""
                location = location_elem.inner_text() if location_elem else None

                if title:
                    postings.append(JobPosting(
                        company="ServiceNow",
                        job_id=hash_job_id(title, location),
                        title=title,
                        location=location,
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
            page.goto("https://www.goldmansachs.com/careers/students/positions", wait_until="networkidle")

            # Click through to "Open Roles" if needed
            try:
                open_roles_link = page.query_selector("a:has-text('Open Roles')")
                if open_roles_link:
                    open_roles_link.click()
                    page.wait_for_load_state("networkidle")
            except Exception:
                pass

            # Parse job listings from rendered page (placeholder)
            jobs = page.query_selector_all("[class*='job']")
            for job_elem in jobs:
                title_elem = job_elem.query_selector("[class*='title']")
                location_elem = job_elem.query_selector("[class*='location']")

                title = title_elem.inner_text() if title_elem else ""
                location = location_elem.inner_text() if location_elem else None

                if title:
                    postings.append(JobPosting(
                        company="Goldman Sachs",
                        job_id=hash_job_id(title, location),
                        title=title,
                        location=location,
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
            page.goto("https://www.google.com/about/careers/applications/jobs/results/", wait_until="networkidle")

            # Optionally apply search query via URL or UI
            # For now, just parse what's visible

            # Wait for job results to load
            try:
                page.wait_for_selector("[class*='job']", timeout=5000)
            except Exception:
                pass

            # Parse job listings (placeholder)
            jobs = page.query_selector_all("[class*='job-card']")
            for job_elem in jobs:
                title_elem = job_elem.query_selector("[class*='title']")
                location_elem = job_elem.query_selector("[class*='location']")

                title = title_elem.inner_text() if title_elem else ""
                location = location_elem.inner_text() if location_elem else None

                if title:
                    postings.append(JobPosting(
                        company="Google",
                        job_id=hash_job_id(title, location),
                        title=title,
                        location=location,
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

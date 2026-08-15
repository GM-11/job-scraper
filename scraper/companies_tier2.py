import hashlib
import logging
from typing import Optional
from urllib.parse import urljoin

from .models import JobPosting


logger = logging.getLogger(__name__)

# "networkidle" hangs/times out on pages with persistent background
# connections (analytics beacons, chat widgets, polling) - these career
# sites all do this. "domcontentloaded" plus an explicit wait for a job
# element is far more reliable.
GOTO_TIMEOUT_MS = 30000
SELECTOR_TIMEOUT_MS = 15000

# Cap on pages fetched per PageUp-based board (Nutanix, ServiceNow) so a
# large board can't turn one run into dozens of full page loads.
MAX_PAGEUP_PAGES = 3


def hash_job_id(title: str, location: Optional[str] = None, extra: Optional[str] = None) -> str:
    """Create a synthetic job_id from title and location."""
    parts = [title]
    if location:
        parts.append(location)
    if extra:
        parts.append(extra)
    key = "|".join(parts)
    return hashlib.md5(key.encode()).hexdigest()[:16]


def _fetch_pageup_board(base_url: str, company: str, max_pages: int = MAX_PAGEUP_PAGES) -> list[JobPosting]:
    """Fetch jobs from a PageUp-platform board (Nutanix, ServiceNow share this
    exact DOM structure: div.card-job containers with an a.js-view-job link,
    a job-meta location element, and ?page=N pagination)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning(f"Playwright not installed, skipping {company}")
        return []

    postings = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )

            for page_num in range(1, max_pages + 1):
                url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
                page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)

                try:
                    page.wait_for_selector("div.card-job", timeout=SELECTOR_TIMEOUT_MS)
                except Exception:
                    logger.warning(f"{company}: no job cards on page {page_num}, stopping pagination")
                    break

                cards = page.query_selector_all("div.card-job")
                if not cards:
                    break

                for card in cards:
                    title_el = card.query_selector("a.js-view-job")
                    if not title_el:
                        continue
                    title = title_el.inner_text().strip()
                    href = title_el.get_attribute("href") or ""
                    job_url = urljoin(base_url, href) if href else None

                    location_el = card.query_selector("[class*='job-meta-location'], ul.job-meta li")
                    location = location_el.inner_text().strip() if location_el else None

                    job_id = card.get_attribute("data-id")
                    if not job_id:
                        actions_el = card.query_selector("[data-id]")
                        job_id = actions_el.get_attribute("data-id") if actions_el else None
                    if not job_id:
                        job_id = hash_job_id(title, location)

                    postings.append(JobPosting(
                        company=company,
                        job_id=job_id,
                        title=title,
                        location=location,
                        url=job_url,
                        posted_date=None,
                        tier="2"
                    ))

                if len(cards) < 20:
                    break

            browser.close()
    except Exception as e:
        logger.error(f"Error fetching {company} jobs: {e}")

    return postings


def fetch_nutanix() -> list[JobPosting]:
    """Fetch jobs from Nutanix (Umbraco/PageUp platform)."""
    return _fetch_pageup_board("https://careers.nutanix.com/en/jobs/", "Nutanix")


def fetch_servicenow() -> list[JobPosting]:
    """Fetch jobs from ServiceNow (Umbraco/PageUp platform)."""
    return _fetch_pageup_board("https://careers.servicenow.com/jobs/", "ServiceNow")


def fetch_goldman_sachs() -> list[JobPosting]:
    """Fetch jobs from Goldman Sachs.

    The AGENT.MD URL (goldmansachs.com/careers/students/positions) 404s -
    Goldman Sachs migrated their whole careers site to higher.gs.com. The
    /campus page is the closest equivalent to the old "students/positions"
    page (student/graduate roles).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping Goldman Sachs")
        return []

    postings = []
    base_url = "https://higher.gs.com"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page.goto(f"{base_url}/campus", wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)

            try:
                page.wait_for_selector("a[href^='/roles/']", timeout=SELECTOR_TIMEOUT_MS)
            except Exception:
                logger.warning("Goldman Sachs: no role links appeared, page structure may have changed")

            links = page.query_selector_all("a[href^='/roles/']")
            for link in links:
                href = link.get_attribute("href") or ""
                if not href.startswith("/roles/"):
                    continue
                job_id = href.split("/roles/")[-1].strip("/")

                title_el = link.query_selector("span")
                title = title_el.inner_text().strip() if title_el else link.inner_text().strip()

                location_el = link.query_selector("[data-testid='location']")
                location = location_el.inner_text().strip().replace("\n", " ") if location_el else None

                if title and job_id:
                    postings.append(JobPosting(
                        company="Goldman Sachs",
                        job_id=job_id,
                        title=title,
                        location=location,
                        url=urljoin(base_url, href),
                        posted_date=None,
                        tier="2"
                    ))

            browser.close()
    except Exception as e:
        logger.error(f"Error fetching Goldman Sachs jobs: {e}")

    return postings


def fetch_google() -> list[JobPosting]:
    """Fetch jobs from Google.

    Google's class names are largely obfuscated/shared across unrelated
    elements (a "Help link" button shares a class with job links), so job
    cards are located by the stable href pattern (jobs/results/<id>-<slug>)
    rather than by class name.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping Google")
        return []

    postings = []
    base_url = "https://www.google.com/about/careers/applications/"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page.goto(
                "https://www.google.com/about/careers/applications/jobs/results/",
                wait_until="domcontentloaded",
                timeout=GOTO_TIMEOUT_MS
            )

            try:
                page.wait_for_selector("a[href*='jobs/results/']", timeout=SELECTOR_TIMEOUT_MS)
            except Exception:
                logger.warning("Google: no job links appeared, page structure may have changed")

            job_links = page.query_selector_all("a[href*='jobs/results/']")
            seen_ids = set()
            for link in job_links:
                href = link.get_attribute("href") or ""
                if "jobs/results/" not in href:
                    continue
                slug = href.split("jobs/results/")[-1].strip("/")
                job_id = slug.split("-")[0]
                if not job_id.isdigit() or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                card = link.evaluate_handle("el => el.closest('div.sMn82b') || el.parentElement")
                title_el = card.as_element().query_selector("h3") if card.as_element() else None
                title = title_el.inner_text().strip() if title_el else link.inner_text().strip()

                location_el = card.as_element().query_selector("[class*='r0wTof']") if card.as_element() else None
                location = location_el.inner_text().strip() if location_el else None

                if title:
                    postings.append(JobPosting(
                        company="Google",
                        job_id=job_id,
                        title=title,
                        location=location,
                        url=urljoin(base_url, href),
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

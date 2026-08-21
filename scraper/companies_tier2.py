import hashlib
import json
import logging
import multiprocessing
import os
import signal
from queue import Empty
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

# Hard wall-clock cap per company, enforced by running the fetch in a forked
# child process the parent can forcibly kill. GOTO_TIMEOUT_MS/SELECTOR_TIMEOUT_MS
# only bound individual Playwright network calls - they don't help when a
# bot-challenge page (e.g. GlobalLogic's Cloudflare check) pins the renderer's
# JS thread, which can make later calls like query_selector_all() or
# browser.close() block indefinitely (they take no timeout in the sync API).
#
# A SIGALRM-based timeout in-process was tried first and did not reliably
# unstick this: Playwright's sync API dispatches calls through a background
# thread, and it's not guaranteed a signal gets delivered/handled promptly
# while the main thread is blocked waiting on that dispatch. Running the
# fetch in a separate OS process sidesteps that entirely - the parent
# reclaims control via a real OS-level wait (Queue.get(timeout=...)) no
# matter what the child is stuck on, and can then kill it outright.
COMPANY_TIMEOUT_S = 90

# Grace period for the child to exit on its own after we've already read its
# result off the queue, before we give up waiting and kill it anyway.
_JOIN_GRACE_S = 5


class _CompanyTimeout(Exception):
    """Raised when a single company fetch exceeds COMPANY_TIMEOUT_S."""


def _company_worker(fetcher, result_queue: "multiprocessing.Queue") -> None:
    # New session/process group, so the parent can kill this fetcher AND any
    # chromium subprocess it spawned as one unit (os.killpg) if it hangs -
    # terminate()/kill() on the Process object alone only signals this pid,
    # leaving an orphaned chromium behind.
    os.setsid()
    try:
        result_queue.put(fetcher())
    except Exception as e:
        result_queue.put(e)


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_with_timeout(fetcher, seconds: int = COMPANY_TIMEOUT_S) -> list[JobPosting]:
    ctx = multiprocessing.get_context("fork")
    result_queue = ctx.Queue()
    process = ctx.Process(target=_company_worker, args=(fetcher, result_queue))
    process.start()

    try:
        result = result_queue.get(timeout=seconds)
    except Empty:
        still_running = process.is_alive()
        _kill_process_group(process.pid)
        process.join(_JOIN_GRACE_S)
        if still_running:
            raise _CompanyTimeout()
        raise RuntimeError(
            f"worker process exited (code {process.exitcode}) without a result"
        )

    process.join(_JOIN_GRACE_S)
    if process.is_alive():
        _kill_process_group(process.pid)
        process.join(_JOIN_GRACE_S)

    if isinstance(result, Exception):
        raise result
    return result


def hash_job_id(
    title: str, location: str | None = None, extra: str | None = None
) -> str:
    """Create a synthetic job_id from title and location."""
    parts = [title]
    if location:
        parts.append(location)
    if extra:
        parts.append(extra)
    key = "|".join(parts)
    return hashlib.md5(key.encode()).hexdigest()[:16]


def _fetch_pageup_board(
    base_url: str, company: str, max_pages: int = MAX_PAGEUP_PAGES
) -> list[JobPosting]:
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
            logger.info(f"{company}: launching browser")
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )

            for page_num in range(1, max_pages + 1):
                url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
                logger.info(f"{company}: navigating to {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
                logger.info(f"{company}: page {page_num} loaded, waiting for job cards")

                try:
                    page.wait_for_selector("div.card-job", timeout=SELECTOR_TIMEOUT_MS)
                except Exception:
                    logger.warning(
                        f"{company}: no job cards on page {page_num}, stopping pagination"
                    )
                    break

                cards = page.query_selector_all("div.card-job")
                logger.info(f"{company}: page {page_num} has {len(cards)} job cards")
                if not cards:
                    break

                for card in cards:
                    title_el = card.query_selector("a.js-view-job")
                    if not title_el:
                        continue
                    title = title_el.inner_text().strip()
                    href = title_el.get_attribute("href") or ""
                    job_url = urljoin(base_url, href) if href else None

                    location_el = card.query_selector(
                        "[class*='job-meta-location'], ul.job-meta li"
                    )
                    location = location_el.inner_text().strip() if location_el else None

                    job_id = hash_job_id(title, location)

                    postings.append(
                        JobPosting(
                            company=company,
                            job_id=job_id,
                            title=title,
                            location=location,
                            url=job_url,
                            posted_date=None,
                            tier="2",
                        )
                    )

                if len(cards) < 20:
                    break

            logger.info(f"{company}: closing browser")
            browser.close()
            logger.info(f"{company}: browser closed")
    except Exception as e:
        logger.error(f"Error fetching {company} jobs: {e}")

    logger.info(f"{company}: {len(postings)} jobs fetched")
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
            logger.info("Goldman Sachs: launching browser")
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            logger.info(f"Goldman Sachs: navigating to {base_url}/campus")
            page.goto(
                f"{base_url}/campus",
                wait_until="domcontentloaded",
                timeout=GOTO_TIMEOUT_MS,
            )
            logger.info("Goldman Sachs: page loaded, waiting for role links")

            try:
                page.wait_for_selector(
                    "a[href^='/roles/']", timeout=SELECTOR_TIMEOUT_MS
                )
            except Exception:
                logger.warning(
                    "Goldman Sachs: no role links appeared, page structure may have changed"
                )
                logger.info("Goldman Sachs: closing browser")
                browser.close()
                logger.info("Goldman Sachs: browser closed")
                return postings

            links = page.query_selector_all("a[href^='/roles/']")
            logger.info(f"Goldman Sachs: found {len(links)} role links")
            for link in links:
                href = link.get_attribute("href") or ""
                if not href.startswith("/roles/"):
                    continue
                title_el = link.query_selector("span")
                title = (
                    title_el.inner_text().strip()
                    if title_el
                    else link.inner_text().strip()
                )

                location_el = link.query_selector("[data-testid='location']")
                location = (
                    location_el.inner_text().strip().replace("\n", " ")
                    if location_el
                    else None
                )

                if title:
                    postings.append(
                        JobPosting(
                            company="Goldman Sachs",
                            job_id=hash_job_id(title, location),
                            title=title,
                            location=location,
                            url=urljoin(base_url, href),
                            posted_date=None,
                            tier="2",
                        )
                    )

            logger.info("Goldman Sachs: closing browser")
            browser.close()
            logger.info("Goldman Sachs: browser closed")
    except Exception as e:
        logger.error(f"Error fetching Goldman Sachs jobs: {e}")

    logger.info(f"Goldman Sachs: {len(postings)} jobs fetched")
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
    results_url = "https://www.google.com/about/careers/applications/jobs/results/"
    try:
        with sync_playwright() as p:
            logger.info("Google: launching browser")
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            logger.info(f"Google: navigating to {results_url}")
            page.goto(
                results_url,
                wait_until="domcontentloaded",
                timeout=GOTO_TIMEOUT_MS,
            )
            logger.info("Google: page loaded, waiting for job links")

            try:
                page.wait_for_selector(
                    "a[href*='jobs/results/']", timeout=SELECTOR_TIMEOUT_MS
                )
            except Exception:
                logger.warning(
                    "Google: no job links appeared, page structure may have changed"
                )
                logger.info("Google: closing browser")
                browser.close()
                logger.info("Google: browser closed")
                return postings

            job_links = page.query_selector_all("a[href*='jobs/results/']")
            logger.info(f"Google: found {len(job_links)} candidate job links")
            seen_ids = set()
            for link in job_links:
                href = link.get_attribute("href") or ""
                if "jobs/results/" not in href:
                    continue

                card = link.evaluate_handle(
                    "el => el.closest('div.sMn82b') || el.parentElement"
                ).as_element()
                title_el = card.query_selector("h3") if card else None
                title = (
                    title_el.inner_text().strip()
                    if title_el
                    else link.inner_text().strip()
                )

                location_el = card.query_selector("[class*='r0wTof']") if card else None
                location = location_el.inner_text().strip() if location_el else None

                job_id = hash_job_id(title, location)
                if title and job_id not in seen_ids:
                    seen_ids.add(job_id)
                    postings.append(
                        JobPosting(
                            company="Google",
                            job_id=job_id,
                            title=title,
                            location=location,
                            url=urljoin(base_url, href),
                            posted_date=None,
                            tier="2",
                        )
                    )

            logger.info("Google: closing browser")
            browser.close()
            logger.info("Google: browser closed")
    except Exception as e:
        logger.error(f"Error fetching Google jobs: {e}")

    logger.info(f"Google: {len(postings)} jobs fetched")
    return postings


def fetch_natwest() -> list[JobPosting]:
    """Fetch NatWest's Talemetry jobs.json API via a Playwright warm-up.

    The API (jobs.natwestgroup.com/search/jobs.json) sits behind Cloudflare
    Turnstile - a bare requests.get() gets a 403 "Just a moment..." challenge
    page even on the JSON endpoint. A real headless browser clears it after a
    few seconds with no extra interaction needed, so this navigates straight
    to the JSON URL itself (each page's whole response is a JSON document) and
    parses whatever text the page renders, instead of scraping HTML markup.
    Detail URLs aren't in the JSON; they're reconstructed from the confirmed
    /jobs/{talemetry_job_id}-{permalink} pattern used by the site's own search
    page.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping NatWest")
        return []

    postings = []
    base_url = "https://jobs.natwestgroup.com"
    try:
        with sync_playwright() as p:
            logger.info("NatWest: launching browser")
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )

            page_num = 1
            total_entries = None
            per_page = None
            while page_num <= 30:
                url = f"{base_url}/search/jobs.json?search_type=talemetry&page={page_num}"
                logger.info(f"NatWest: navigating to {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
                # Turnstile resolves client-side within a few seconds of load;
                # there's no DOM element to wait on since the response is bare JSON.
                page.wait_for_timeout(6000)

                try:
                    body_text = page.inner_text("pre")
                except Exception:
                    body_text = page.inner_text("body")

                try:
                    data = json.loads(body_text)
                except json.JSONDecodeError:
                    logger.warning(
                        f"NatWest: page {page_num} did not return JSON "
                        "(Turnstile likely didn't clear), stopping"
                    )
                    break

                if total_entries is None:
                    total_entries = data.get("total_entries", 0)
                    per_page = data.get("per_page", 25)

                entries = data.get("entries", [])
                logger.info(f"NatWest: page {page_num} has {len(entries)} entries")
                if not entries:
                    break

                for entry in entries:
                    location = entry.get("location") or {}
                    loc_parts = [
                        location.get("locality"),
                        location.get("region_full") or location.get("region_abbr"),
                        location.get("country"),
                    ]
                    loc_str = ", ".join(v for v in loc_parts if v) or None

                    job_id = str(entry.get("talemetry_job_id") or entry.get("id") or "")
                    permalink = entry.get("permalink", "")
                    job_url = (
                        f"{base_url}/jobs/{job_id}-{permalink}" if job_id else None
                    )

                    postings.append(
                        JobPosting(
                            company="NatWest",
                            job_id=job_id,
                            title=entry.get("title", ""),
                            location=loc_str,
                            url=job_url,
                            posted_date=None,
                            tier="2",
                        )
                    )

                page_num += 1
                if (
                    per_page
                    and total_entries is not None
                    and (page_num - 1) * per_page >= total_entries
                ):
                    break

            logger.info("NatWest: closing browser")
            browser.close()
            logger.info("NatWest: browser closed")
    except Exception as e:
        logger.error(f"Error fetching NatWest jobs: {e}")

    logger.info(f"NatWest: {len(postings)} jobs fetched")
    return postings


# GlobalLogic (careers.hitachi.com) was removed: its board sits behind a
# Cloudflare bot challenge that headless Playwright never clears, so every
# run reliably burned ~15-20s for zero jobs. Re-add only with a real
# anti-detection approach (e.g. playwright-stealth) if this is worth revisiting.


# Registry of all tier 2 company fetchers
TIER2_COMPANIES = {
    "Nutanix": fetch_nutanix,
    "ServiceNow": fetch_servicenow,
    "Goldman Sachs": fetch_goldman_sachs,
    "Google": fetch_google,
    "NatWest": fetch_natwest,
}


def fetch_all_tier2() -> tuple[list[JobPosting], list[str]]:
    """Fetch all tier 2 companies, return postings and list of failed companies."""
    all_postings = []
    failed = []

    for company_name, fetcher in TIER2_COMPANIES.items():
        logger.info(
            f"=== Starting {company_name} (hard timeout {COMPANY_TIMEOUT_S}s) ==="
        )
        try:
            postings = _run_with_timeout(fetcher)
            all_postings.extend(postings)
        except _CompanyTimeout:
            logger.error(
                f"{company_name}: exceeded {COMPANY_TIMEOUT_S}s, skipping "
                "(likely a hung/CPU-pinned browser page)"
            )
            failed.append(company_name)
        except Exception as e:
            logger.error(f"Failed to fetch {company_name}: {e}")
            failed.append(company_name)

    return all_postings, failed

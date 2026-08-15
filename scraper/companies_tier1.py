import hashlib
import json
import logging
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .models import JobPosting

logger = logging.getLogger(__name__)
TIMEOUT = 5
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_DELAY = 0.3  # Delay between requests in seconds
MAX_PAGES = 60  # Safety cap on pagination loops so a runaway API can't hang the job


class FetchTimeoutError(Exception):
    """Raised when a request exceeds TIMEOUT, so the whole company fetch can
    be queued and retried once after the other companies have run."""


def fetch_with_retry(
    url: str, method: str = "GET", json_data: dict | None = None, timeout: int = TIMEOUT
) -> dict | None:
    """Fetch URL with error handling. Raises FetchTimeoutError on timeout so
    callers can requeue the whole company for a single retry pass."""
    try:
        headers = {"User-Agent": USER_AGENT}
        if method == "POST":
            response = requests.post(
                url, json=json_data, headers=headers, timeout=timeout
            )
        else:
            response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json() if response.text else None
        return data
    except requests.Timeout:
        logger.debug(f"Timeout (>{timeout}s) fetching {url}")
        raise FetchTimeoutError(f"Timed out fetching {url}")
    except requests.ConnectionError as e:
        logger.debug(f"Connection error: {url} - {e}")
        return None
    except Exception as e:
        logger.debug(f"Error fetching {url}: {type(e).__name__}: {str(e)[:100]}")
        return None


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


# === GREENHOUSE COMPANIES ===


def fetch_greenhouse(board_token: str, company: str) -> list[JobPosting]:
    """Fetch jobs from Greenhouse API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    data = fetch_with_retry(url)
    if not data or "jobs" not in data:
        logger.warning(f"No jobs found for {company} (Greenhouse)")
        return []

    postings = []
    for job in data.get("jobs", []):
        location = job.get("location", {})
        location_name = location.get("name") if isinstance(location, dict) else None

        postings.append(
            JobPosting(
                company=company,
                job_id=str(job.get("id")),
                title=job.get("title", ""),
                location=location_name,
                url=job.get("absolute_url"),
                posted_date=job.get("updated_at"),
                tier="1",
            )
        )
    return postings


def fetch_harness() -> list[JobPosting]:
    return fetch_greenhouse("harnessinc", "Harness")


def fetch_razorpay() -> list[JobPosting]:
    return fetch_greenhouse("razorpaysoftwareprivatelimited", "Razorpay")


def fetch_arcesium() -> list[JobPosting]:
    return fetch_greenhouse("arcesiumllc", "Arcesium")


# === JIBE/PHENOM COMPANIES ===


def fetch_jibe(domain: str, company: str) -> list[JobPosting]:
    """Fetch jobs from Jibe/Phenom platform."""
    postings = []
    page = 1
    while page <= MAX_PAGES:
        url = f"https://{domain}/api/jobs?page={page}&sortBy=relevance&descending=false&internal=false"
        data = fetch_with_retry(url)
        if not data or "jobs" not in data:
            break

        jobs = data.get("jobs", [])
        if not jobs:
            break

        for job in jobs:
            job_data = job.get("data", {})
            postings.append(
                JobPosting(
                    company=company,
                    job_id=str(job_data.get("req_id", "")),
                    title=job_data.get("title", ""),
                    location=None,
                    url=None,
                    posted_date=None,
                    tier="1",
                )
            )
        page += 1
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_sp_global() -> list[JobPosting]:
    return fetch_jibe("careers.spglobal.com", "S&P Global")


def fetch_schneider_electric() -> list[JobPosting]:
    return fetch_jibe("careers.se.com", "Schneider Electric")


# === EIGHTFOLD COMPANIES ===


def fetch_eightfold(domain: str, query_domain: str, company: str) -> list[JobPosting]:
    """Fetch jobs from Eightfold platform."""
    postings = []
    start = 0
    pages_fetched = 0
    while pages_fetched < MAX_PAGES:
        pages_fetched += 1
        url = f"https://{domain}/api/pcsx/search?domain={query_domain}&query=&location=&start={start}"
        data = fetch_with_retry(url)
        if not data or "data" not in data:
            break

        response_data = data.get("data", {})
        positions = response_data.get("positions", [])
        total_count = response_data.get("count", 0)

        if not positions:
            break

        for pos in positions:
            posted_ts = pos.get("postedTs", 0)
            posted_date = None
            if posted_ts:
                try:
                    posted_date = datetime.fromtimestamp(posted_ts).isoformat()
                except Exception:
                    pass

            url_path = pos.get("positionUrl", "")
            full_url = f"https://{domain}{url_path}" if url_path else None

            locations = pos.get("locations") or []
            location = ", ".join(locations) if locations else None

            postings.append(
                JobPosting(
                    company=company,
                    job_id=str(pos.get("id")),
                    title=pos.get("name", ""),
                    location=location,
                    url=full_url,
                    posted_date=posted_date,
                    tier="1",
                )
            )

        start += len(positions)
        if start >= total_count:
            break
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_microsoft() -> list[JobPosting]:
    return fetch_eightfold("apply.careers.microsoft.com", "microsoft.com", "Microsoft")


def fetch_nvidia() -> list[JobPosting]:
    return fetch_eightfold("jobs.nvidia.com", "nvidia.com", "NVIDIA")


def fetch_qualcomm() -> list[JobPosting]:
    return fetch_eightfold("careers.qualcomm.com", "qualcomm.com", "Qualcomm")


# === ORACLE FUSION COMPANIES ===


def fetch_oracle_fusion(host: str, site_number: str, company: str) -> list[JobPosting]:
    """Fetch jobs from Oracle Fusion Cloud."""
    postings = []
    offset = 0
    total_count = None
    pages_fetched = 0

    while pages_fetched < MAX_PAGES:
        pages_fetched += 1
        url = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList"
            f"&finder=findReqs;siteNumber={site_number},limit=25,sortBy=POSTING_DATES_DESC,offset={offset}"
        )

        data = fetch_with_retry(url)
        if not data or "items" not in data:
            break

        items = data.get("items", [])
        if not items:
            break

        first_item = items[0]
        if total_count is None:
            total_count = first_item.get("TotalJobsCount", 0)

        requisition_list = first_item.get("requisitionList", [])
        if not requisition_list:
            break

        for req in requisition_list:
            postings.append(
                JobPosting(
                    company=company,
                    job_id=str(req.get("Id")),
                    title=req.get("Title", ""),
                    location=req.get("PrimaryLocationCountry"),
                    url=None,
                    posted_date=req.get("PostedDate"),
                    tier="1",
                )
            )

        offset += 25
        if offset >= (total_count or 0):
            break
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_jpmorgan() -> list[JobPosting]:
    return fetch_oracle_fusion("jpmc.fa.oraclecloud.com", "CX_1001", "JPMorgan")


def fetch_texas_instruments() -> list[JobPosting]:
    return fetch_oracle_fusion("edbz.fa.us2.oraclecloud.com", "CX", "Texas Instruments")


# === WORKDAY COMPANIES ===


def fetch_workday_cxs(endpoint_url: str, company: str) -> list[JobPosting]:
    """Fetch jobs from Workday CxS platform."""
    postings = []
    offset = 0
    total = None
    pages_fetched = 0

    while pages_fetched < MAX_PAGES:
        pages_fetched += 1
        data = fetch_with_retry(
            endpoint_url,
            method="POST",
            json_data={
                "appliedFacets": {},
                "limit": 20,
                "offset": offset,
                "searchText": "",
            },
        )
        if not data:
            break

        if total is None:
            total = data.get("total", 0)

        job_postings = data.get("jobPostings", [])
        if not job_postings:
            break

        for posting in job_postings:
            bullet_fields = posting.get("bulletFields", [])
            job_id = (
                bullet_fields[0]
                if bullet_fields
                else hash_job_id(posting.get("title", ""))
            )

            external_path = posting.get("externalPath", "")
            url = (
                f"https://salesforce.wd12.myworkdayjobs.com/External_Career_Site{external_path}"
                if external_path
                else None
            )

            postings.append(
                JobPosting(
                    company=company,
                    job_id=job_id,
                    title=posting.get("title", ""),
                    location=None,
                    url=url,
                    posted_date=posting.get("postedOn"),
                    tier="1",
                )
            )

        offset += 20
        if offset >= (total or 0):
            break
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_salesforce() -> list[JobPosting]:
    return fetch_workday_cxs(
        "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site/jobs",
        "Salesforce",
    )


# === CUSTOM/ONE-OFF COMPANIES ===


def fetch_amazon() -> list[JobPosting]:
    """Fetch jobs from Amazon."""
    postings = []
    offset = 0
    pages_fetched = 0
    while pages_fetched < MAX_PAGES:
        pages_fetched += 1
        url = (
            f"https://www.amazon.jobs/en/search.json?result_limit=100&sort=recent"
            f"&base_query=software%20engineer&country=IND&offset={offset}"
        )
        data = fetch_with_retry(url)
        if not data:
            break

        jobs = data.get("jobs", [])
        if not jobs:
            break

        total_hits = data.get("hits", 0)

        for job in jobs:
            postings.append(
                JobPosting(
                    company="Amazon",
                    job_id=str(job.get("id_icims")),
                    title=job.get("title", ""),
                    location=None,
                    url=f"https://www.amazon.jobs{job.get('job_path', '')}",
                    posted_date=job.get("posted_date"),
                    tier="1",
                )
            )

        offset += 100
        if offset >= total_hits:
            break
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_atlassian() -> list[JobPosting]:
    """Fetch jobs from Atlassian."""
    url = "https://www.atlassian.com/endpoint/careers/listings"
    data = fetch_with_retry(url)
    if not data:
        return []

    postings = []
    for item in data if isinstance(data, list) else []:
        portal_job_post = item.get("portalJobPost", {})
        locations = item.get("locations") or []
        location = ", ".join(locations) if locations else None

        postings.append(
            JobPosting(
                company="Atlassian",
                job_id=str(item.get("id")),
                title=item.get("title", ""),
                location=location,
                url=portal_job_post.get("portalUrl"),
                posted_date=portal_job_post.get("updatedDate"),
                tier="1",
            )
        )

    return postings


def fetch_apple() -> list[JobPosting]:
    """Fetch jobs from Apple."""
    postings = []
    url = "https://jobs.apple.com/en-us/search?location=india-INDC"

    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        html = response.text

        # Extract __staticRouterHydrationData from HTML
        match = re.search(
            r"window\.__staticRouterHydrationData\s*=\s*(\{.*?\})", html, re.DOTALL
        )
        if not match:
            logger.warning("Could not find hydration data in Apple jobs page")
            return []

        json_str = match.group(1)
        # Parse the JSON (may need unescaping)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Could not parse Apple jobs JSON")
            return []

        # Navigate to search results (structure varies, this is a best guess)
        # This needs refinement based on actual page structure
        def extract_jobs_recursive(obj):
            jobs = []
            if isinstance(obj, dict):
                if "searchResults" in obj and isinstance(obj["searchResults"], list):
                    for result in obj["searchResults"]:
                        if isinstance(result, dict):
                            pos_id = result.get("positionId")
                            if pos_id:
                                title = result.get(
                                    "transformedPostingTitle"
                                ) or result.get("postingTitle", "")
                                posting_date = result.get(
                                    "postDateInGMT"
                                ) or result.get("postingDate")
                                url = f"https://jobs.apple.com/en-us/details/{pos_id}/{result.get('transformedPostingTitle', 'job')}"
                                jobs.append(
                                    JobPosting(
                                        company="Apple",
                                        job_id=str(pos_id),
                                        title=title,
                                        location=None,
                                        url=url,
                                        posted_date=posting_date,
                                        tier="1",
                                    )
                                )
                for v in obj.values():
                    jobs.extend(extract_jobs_recursive(v))
            elif isinstance(obj, list):
                for item in obj:
                    jobs.extend(extract_jobs_recursive(item))
            return jobs

        postings = extract_jobs_recursive(data)
    except requests.Timeout:
        logger.warning(f"Apple: request timed out (>{TIMEOUT}s)")
        raise FetchTimeoutError("Apple timed out")
    except Exception as e:
        logger.error(f"Apple: {e}")

    return postings


def fetch_databricks() -> list[JobPosting]:
    """Fetch jobs from Databricks."""
    url = "https://www.databricks.com/company/careers/open-positions"
    postings = []

    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for job listings (this is a placeholder - actual selectors need verification)
        job_elements = soup.find_all(
            class_=re.compile(r"job|position|opening", re.IGNORECASE)
        )
        logger.info(f"Databricks: found {len(job_elements)} potential job elements")

        for elem in job_elements[
            :100
        ]:  # Limit to first 100 to avoid processing overhead
            title = elem.get_text(strip=True)
            if title and len(title) > 3:
                job_id = hash_job_id(title)
                postings.append(
                    JobPosting(
                        company="Databricks",
                        job_id=job_id,
                        title=title,
                        location=None,
                        url=None,
                        posted_date=None,
                        tier="1",
                    )
                )
    except requests.Timeout:
        logger.error("Databricks: request timeout")
    except Exception as e:
        logger.error(f"Databricks: {e}")

    return postings


def fetch_uber() -> list[JobPosting]:
    """Fetch jobs from Uber."""
    url = "https://jobs.uber.com/en/jobs/"
    postings = []

    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        job_elements = soup.find_all(
            class_=re.compile(r"job|position|opening", re.IGNORECASE)
        )
        logger.info(f"Uber: found {len(job_elements)} potential job elements")

        for elem in job_elements[:100]:
            title = elem.get_text(strip=True)
            if title and len(title) > 3:
                job_id = hash_job_id(title)
                postings.append(
                    JobPosting(
                        company="Uber",
                        job_id=job_id,
                        title=title,
                        location=None,
                        url=None,
                        posted_date=None,
                        tier="1",
                    )
                )
    except requests.Timeout:
        logger.error("Uber: request timeout")
    except Exception as e:
        logger.error(f"Uber: {e}")

    return postings


def fetch_intuit() -> list[JobPosting]:
    """Fetch jobs from Intuit."""
    url = "https://jobs.intuit.com/search-jobs?k=software%20engineer&l=&orgIds=27595"
    postings = []

    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        job_elements = soup.find_all(
            class_=re.compile(r"job|position|opening", re.IGNORECASE)
        )
        logger.info(f"Intuit: found {len(job_elements)} potential job elements")

        for elem in job_elements[:100]:
            title = elem.get_text(strip=True)
            if title and len(title) > 3:
                job_id = hash_job_id(title)
                postings.append(
                    JobPosting(
                        company="Intuit",
                        job_id=job_id,
                        title=title,
                        location=None,
                        url=None,
                        posted_date=None,
                        tier="1",
                    )
                )
    except requests.Timeout:
        logger.error("Intuit: request timeout")
    except Exception as e:
        logger.error(f"Intuit: {e}")

    return postings


def fetch_ea() -> list[JobPosting]:
    """Fetch jobs from EA."""
    url = "https://jobs.ea.com/en_US/careers/SearchJobs"
    postings = []

    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        job_elements = soup.find_all(
            class_=re.compile(r"job|position|opening", re.IGNORECASE)
        )
        logger.info(f"EA: found {len(job_elements)} potential job elements")

        for elem in job_elements[:100]:
            title = elem.get_text(strip=True)
            if title and len(title) > 3:
                job_id = hash_job_id(title)
                postings.append(
                    JobPosting(
                        company="EA",
                        job_id=job_id,
                        title=title,
                        location=None,
                        url=None,
                        posted_date=None,
                        tier="1",
                    )
                )
    except requests.Timeout:
        logger.error("EA: request timeout")
    except Exception as e:
        logger.error(f"EA: {e}")

    return postings


def fetch_incepto() -> list[JobPosting]:
    """Fetch jobs from Incepto."""
    url = "https://join.com/companies/incepto-medical"
    postings = []

    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for __NEXT_DATA__ script tag
        next_data = soup.find("script", {"id": "__NEXT_DATA__"})
        if next_data:
            try:
                data = json.loads(next_data.string)
                jobs = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("initialState", {})
                    .get("jobs", {})
                    .get("items", [])
                )
                for job in jobs:
                    postings.append(
                        JobPosting(
                            company="Incepto",
                            job_id=str(
                                job.get("id", hash_job_id(job.get("title", "")))
                            ),
                            title=job.get("title", ""),
                            location=None,
                            url=None,
                            posted_date=None,
                            tier="1",
                        )
                    )
            except json.JSONDecodeError:
                logger.warning("Could not parse Incepto JSON")
    except requests.Timeout:
        logger.warning(f"Incepto: request timed out (>{TIMEOUT}s)")
        raise FetchTimeoutError("Incepto timed out")
    except Exception as e:
        logger.error(f"Error fetching Incepto jobs: {e}")

    return postings


def fetch_deshaw() -> list[JobPosting]:
    """Fetch jobs from D.E. Shaw."""
    postings = []

    try:
        # Step 1: Get buildId from main careers page
        response = requests.get(
            "https://www.deshaw.com/careers",
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        next_data = soup.find("script", {"id": "__NEXT_DATA__"})
        if not next_data:
            logger.warning("Could not find __NEXT_DATA__ in D.E. Shaw careers page")
            return []

        try:
            data = json.loads(next_data.string)
            build_id = data.get("buildId")
        except json.JSONDecodeError:
            logger.warning("Could not parse D.E. Shaw __NEXT_DATA__")
            return []

        if not build_id:
            logger.warning("Could not extract buildId from D.E. Shaw page")
            return []

        # Step 2: Fetch job data using buildId
        url = f"https://www.deshaw.com/_next/data/{build_id}/en/careers.json"
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        # Navigate to job listings in the response structure
        # This needs inspection of actual response
        def extract_jobs_from_deshaw(obj):
            jobs = []
            if isinstance(obj, dict):
                if "jobs" in obj and isinstance(obj["jobs"], list):
                    for job in obj["jobs"]:
                        jobs.append(
                            JobPosting(
                                company="D.E. Shaw",
                                job_id=str(
                                    job.get("id", hash_job_id(job.get("title", "")))
                                ),
                                title=job.get("title", ""),
                                location=job.get("location"),
                                url=None,
                                posted_date=None,
                                tier="1",
                            )
                        )
                for v in obj.values():
                    jobs.extend(extract_jobs_from_deshaw(v))
            elif isinstance(obj, list):
                for item in obj:
                    jobs.extend(extract_jobs_from_deshaw(item))
            return jobs

        postings = extract_jobs_from_deshaw(data)
    except requests.Timeout:
        logger.warning(f"D.E. Shaw: request timed out (>{TIMEOUT}s)")
        raise FetchTimeoutError("D.E. Shaw timed out")
    except Exception as e:
        logger.error(f"Error fetching D.E. Shaw jobs: {e}")

    return postings


# === TOWER RESEARCH CAPITAL ===


def fetch_tower_research() -> list[JobPosting]:
    """Fetch jobs from Tower Research Capital (SmartRecruiters)."""
    url = (
        "https://api.smartrecruiters.com/v1/companies/TowerResearchCapitalLLC/postings"
    )
    data = fetch_with_retry(url)
    if not data:
        return []

    postings = []
    postings_list = data.get("content", []) if isinstance(data, dict) else []

    for posting in postings_list:
        postings.append(
            JobPosting(
                company="Tower Research Capital",
                job_id=str(posting.get("id")),
                title=posting.get("name", ""),
                location=None,
                url=posting.get("url"),
                posted_date=None,
                tier="1",
            )
        )

    # Flag if suspiciously few postings
    if len(postings) < 5:
        logger.warning(
            f"Tower Research Capital returned only {len(postings)} postings; verify source"
        )

    return postings


# Registry of all tier 1 company fetchers.
# All "confirmed" sources from AGENT.MD (companies with a documented, working
# API/JSON response shape). Pagination loops are capped by MAX_PAGES so a
# large board (e.g. Microsoft) can't stall the run.
#
# Excluded (per AGENT.MD, these are NOT confirmed - selectors/payloads were
# never captured against the live site, so implementing them would mean
# guessing): Databricks, Uber, Intuit, EA, Adobe, Warner Bros Discovery,
# Cohesity, Concentrix.
TIER1_COMPANIES = {
    "Harness": fetch_harness,
    "Razorpay": fetch_razorpay,
    "Arcesium": fetch_arcesium,
    "S&P Global": fetch_sp_global,
    "Schneider Electric": fetch_schneider_electric,
    "Microsoft": fetch_microsoft,
    "NVIDIA": fetch_nvidia,
    "Qualcomm": fetch_qualcomm,
    "JPMorgan": fetch_jpmorgan,
    "Texas Instruments": fetch_texas_instruments,
    "Salesforce": fetch_salesforce,
    "Amazon": fetch_amazon,
    "Atlassian": fetch_atlassian,
    "Apple": fetch_apple,
    "Incepto": fetch_incepto,
    "D.E. Shaw": fetch_deshaw,
    "Tower Research Capital": fetch_tower_research,
}


def fetch_all_tier1() -> tuple[list[JobPosting], list[str]]:
    """Fetch all tier 1 companies, return postings and list of failed companies.

    Every request is capped at TIMEOUT (5s). A company whose fetch times out
    is queued and retried once, after all other companies have been
    attempted; a second timeout on retry marks it as failed.
    """
    all_postings = []
    failed = []
    retry_queue = []

    for company_name, fetcher in TIER1_COMPANIES.items():
        logger.debug(f"Fetching {company_name}...")
        try:
            postings = fetcher()
            logger.debug(f"  {company_name}: {len(postings)} jobs")
            all_postings.extend(postings)
        except FetchTimeoutError:
            logger.warning(
                f"⏱ {company_name}: timed out (>{TIMEOUT}s), queued for retry"
            )
            retry_queue.append((company_name, fetcher))
        except Exception as e:
            logger.error(f"✗ {company_name}: {e}")
            failed.append(company_name)
        time.sleep(REQUEST_DELAY)

    if retry_queue:
        logger.debug(
            f"Retrying {len(retry_queue)} timed-out compan{'y' if len(retry_queue) == 1 else 'ies'}..."
        )
        for company_name, fetcher in retry_queue:
            logger.debug(f"Retrying {company_name}...")
            try:
                postings = fetcher()
                logger.debug(
                    f"  {company_name}: {len(postings)} jobs (retry succeeded)"
                )
                all_postings.extend(postings)
            except FetchTimeoutError:
                logger.error(
                    f"✗ {company_name}: timed out again (>{TIMEOUT}s), giving up"
                )
                failed.append(company_name)
            except Exception as e:
                logger.error(f"✗ {company_name}: {e}")
                failed.append(company_name)
            time.sleep(REQUEST_DELAY)

    return all_postings, failed

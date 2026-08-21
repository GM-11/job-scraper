import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from .models import JobPosting

logger = logging.getLogger(__name__)
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_DELAY = 0.5
MAX_PAGES = 60  # Safety cap on pagination loops so a runaway API can't hang the job


REQUEST_HEADERS = {"User-Agent": USER_AGENT}


class FetchTimeoutError(Exception):
    """Raised when a request exceeds TIMEOUT, so the whole company fetch can
    be queued and retried once after the other companies have run."""


def fetch_with_retry(
    url: str, method: str = "GET", json_data: dict | None = None, timeout: int = TIMEOUT
) -> dict | None:
    """Fetch URL with error handling. Raises FetchTimeoutError on timeout so
    callers can requeue the whole company for a single retry pass."""
    logger.info(f"{method} {url}")
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
    """Create a stable synthetic job ID when the job board exposes none."""
    parts = [title]
    if location:
        parts.append(location)
    if extra:
        parts.append(extra)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def fetch_html(url: str, *, session: requests.Session | None = None) -> str | None:
    """Get an HTML page using the common headers and timeout."""
    logger.info(f"GET {url}")
    client = session or requests
    try:
        response = client.get(url, headers=REQUEST_HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.Timeout as exc:
        raise FetchTimeoutError(f"Timed out fetching {url}") from exc
    except requests.RequestException as exc:
        logger.warning("Could not fetch %s: %s", url, exc)
        return None


def text_or_none(element) -> str | None:
    """Return normalized element text, or None for absent/empty elements."""
    if not element:
        return None
    text = " ".join(element.get_text(" ", strip=True).split())
    return text or None


def parse_job_links(
    html: str,
    *,
    company: str,
    base_url: str,
    link_pattern: str,
    location_selectors: tuple[str, ...] = (),
    role_id_pattern: str | None = None,
    extra_selector: str | None = None,
) -> list[JobPosting]:
    """Parse SSR job-list links without promoting page navigation to jobs.

    This intentionally relies on a job-detail URL pattern, which is much more
    stable than presentation classes. Job IDs use a board-provided role ID when
    available, otherwise a title/location(/team) hash as required by the spec.
    """
    soup = BeautifulSoup(html, "html.parser")
    postings: list[JobPosting] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = link.get("href")
        if not isinstance(href, str) or not re.search(
            link_pattern, href, re.IGNORECASE
        ):
            continue
        title = text_or_none(link)
        if not title or len(title) < 3:
            continue
        card = link.find_parent(["article", "li", "tr", "div"])
        location = None
        for selector in location_selectors:
            location = text_or_none(card.select_one(selector) if card else None)
            if location:
                break
        card_text = text_or_none(card) or title
        role_match = (
            re.search(role_id_pattern, card_text, re.IGNORECASE)
            if role_id_pattern
            else None
        )
        extra = text_or_none(
            card.select_one(extra_selector) if card and extra_selector else None
        )
        job_id = (
            role_match.group(1) if role_match else hash_job_id(title, location, extra)
        )
        if job_id in seen:
            continue
        seen.add(job_id)
        postings.append(
            JobPosting(
                company=company,
                job_id=str(job_id),
                title=title,
                location=location,
                url=urljoin(base_url, href),
                posted_date=None,
                tier="1",
            )
        )
    return postings


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


def fetch_tower_research() -> list[JobPosting]:
    """Fetch Tower Research Capital from its confirmed Greenhouse board."""
    return fetch_greenhouse("towerresearchcapital", "Tower Research Capital")


def fetch_coinbase() -> list[JobPosting]:
    """Fetch Coinbase jobs.

    The careers page (coinbase.com/en-in/careers/positions) looks like a
    custom SSR page but its own embedded state (suspenseBridgeData ->
    "Positions" -> departments[].jobs[]) shows job IDs in Greenhouse's
    numeric format and absolute_urls of the form
    coinbase.com/careers/positions/{id}?gh_jid={id} - confirmed live against
    the public Greenhouse API under board token "coinbase" (170 jobs).
    """
    return fetch_greenhouse("coinbase", "Coinbase")


def fetch_phonepe() -> list[JobPosting]:
    return fetch_greenhouse("phonepe", "PhonePe")


# === LEVER COMPANIES ===


def fetch_lever(company_slug: str, company: str) -> list[JobPosting]:
    """Fetch jobs from the Lever API."""
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    data = fetch_with_retry(url)
    if not data:
        logger.warning(f"No jobs found for {company} (Lever)")
        return []

    postings = []
    for job in data if isinstance(data, list) else []:
        created_at = job.get("createdAt")
        posted_date = None
        if created_at:
            try:
                posted_date = (
                    datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
                    .date()
                    .isoformat()
                )
            except Exception:
                pass

        postings.append(
            JobPosting(
                company=company,
                job_id=str(job.get("id", "")),
                title=job.get("text", ""),
                location=job.get("categories", {}).get("location"),
                url=job.get("hostedUrl"),
                posted_date=posted_date,
                tier="1",
            )
        )
    return postings


def fetch_paytm() -> list[JobPosting]:
    return fetch_lever("paytm", "Paytm")


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
            location = job_data.get("full_location") or job_data.get("short_location")
            if not location:
                city_state_country = [
                    job_data.get(k) for k in ("city", "state", "country")
                ]
                location = ", ".join(v for v in city_state_country if v) or None
            postings.append(
                JobPosting(
                    company=company,
                    job_id=str(job_data.get("req_id", "")),
                    title=job_data.get("title", ""),
                    location=location,
                    url=job_data.get("apply_url"),
                    posted_date=job_data.get("posted_date"),
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


def fetch_docusign() -> list[JobPosting]:
    return fetch_jibe("careers.docusign.com", "DocuSign")


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
                    posted_date = (
                        datetime.fromtimestamp(posted_ts, tz=timezone.utc)
                        .date()
                        .isoformat()
                    )
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


def fetch_ericsson() -> list[JobPosting]:
    return fetch_eightfold("jobs.ericsson.com", "ericsson.com", "Ericsson")


def fetch_morgan_stanley() -> list[JobPosting]:
    return fetch_eightfold(
        "morganstanley.eightfold.ai", "morganstanley.com", "Morgan Stanley"
    )


def fetch_paypal() -> list[JobPosting]:
    return fetch_eightfold("paypal.eightfold.ai", "paypal.com", "PayPal")


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
                    location=req.get("PrimaryLocation")
                    or req.get("PrimaryLocationCountry"),
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


def fetch_american_express() -> list[JobPosting]:
    return fetch_oracle_fusion(
        "egug.fa.us2.oraclecloud.com", "CX_1", "American Express"
    )


def fetch_oracle() -> list[JobPosting]:
    return fetch_oracle_fusion("eeho.fa.us2.oraclecloud.com", "CX_1", "Oracle")


def fetch_exl() -> list[JobPosting]:
    return fetch_oracle_fusion(
        "fa-ewjt-saasfaprod1.fa.ocs.oraclecloud.com", "CX_2", "EXL"
    )


# === WORKDAY COMPANIES ===


def fetch_workday_cxs(
    endpoint_url: str, base_url: str, company: str
) -> list[JobPosting]:
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
                str(bullet_fields[-1])
                if bullet_fields
                else hash_job_id(posting.get("externalPath", ""))
            )

            external_path = posting.get("externalPath", "")
            url = f"{base_url}{external_path}" if external_path else None

            location = posting.get("locationsText")
            if not location or re.match(r"^\d+\s*Locations?$", location, re.IGNORECASE):
                # locationsText is a vague "N Locations" for multi-site postings;
                # the primary site is still encoded in the URL path, e.g.
                # "/job/India---Mumbai/..." -> "India, Mumbai"
                path_parts = external_path.strip("/").split("/") if external_path else []
                path_location = path_parts[1] if len(path_parts) > 1 else ""
                if path_location:
                    location = path_location.replace("---", ", ").replace("-", " ")

            postings.append(
                JobPosting(
                    company=company,
                    job_id=job_id,
                    title=posting.get("title", ""),
                    location=location,
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
        "https://salesforce.wd12.myworkdayjobs.com",
        "Salesforce",
    )


def fetch_thomson_reuters() -> list[JobPosting]:
    return fetch_workday_cxs(
        "https://thomsonreuters.wd5.myworkdayjobs.com/wday/cxs/thomsonreuters/External_Career_Site/jobs",
        "https://thomsonreuters.wd5.myworkdayjobs.com",
        "Thomson Reuters",
    )


def fetch_visa() -> list[JobPosting]:
    return fetch_workday_cxs(
        "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/jobs",
        "https://visa.wd5.myworkdayjobs.com",
        "Visa",
    )


def fetch_autodesk() -> list[JobPosting]:
    return fetch_workday_cxs(
        "https://autodesk.wd1.myworkdayjobs.com/wday/cxs/autodesk/Ext/jobs",
        "https://autodesk.wd1.myworkdayjobs.com",
        "Autodesk",
    )


def fetch_kyndryl() -> list[JobPosting]:
    """Fetch both Kyndryl Workday tenants: the general professional board
    plus the separate early-career-specific board (distinct postings, not a
    subset of the professional one)."""
    postings = fetch_workday_cxs(
        "https://kyndryl.wd5.myworkdayjobs.com/wday/cxs/kyndryl/KyndrylProfessionalCareers/jobs",
        "https://kyndryl.wd5.myworkdayjobs.com",
        "Kyndryl",
    )
    postings += fetch_workday_cxs(
        "https://kyndryl.wd5.myworkdayjobs.com/wday/cxs/kyndryl/KyndrylEarlyCareers/jobs",
        "https://kyndryl.wd5.myworkdayjobs.com",
        "Kyndryl",
    )
    return postings


# === CUSTOM/ONE-OFF COMPANIES ===


def fetch_rippling() -> list[JobPosting]:
    """Fetch Rippling's public Algolia search index (stateless, no session
    needed - the API key is a public search-only key)."""
    url = (
        "https://6fnax3tbef-dsn.algolia.net/1/indexes/*/queries"
        "?x-algolia-api-key=416caa4690f002ff6fe4a2097623640b"
        "&x-algolia-application-id=6FNAX3TBEF"
    )
    postings = []
    seen: set[str] = set()
    hits_per_page = 100
    page = 0
    total_pages = None
    pages_fetched = 0

    while pages_fetched < MAX_PAGES:
        pages_fetched += 1
        body = {
            "requests": [
                {
                    "indexName": "careers_en-US_production",
                    "params": f"hitsPerPage={hits_per_page}&query=&page={page}",
                }
            ]
        }
        data = fetch_with_retry(url, method="POST", json_data=body)
        results = (data or {}).get("results") or []
        if not results:
            break

        result = results[0]
        hits = result.get("hits", [])
        if not hits:
            break
        if total_pages is None:
            total_pages = result.get("nbPages", 0)

        for hit in hits:
            # jobId repeats across a single posting's per-location hits;
            # objectID (jobId + location slug) is the unique per-record key.
            object_id = hit.get("objectID") or hash_job_id(hit.get("name", ""))
            if object_id in seen:
                continue
            seen.add(object_id)

            locations = hit.get("locationNames") or []
            location = ", ".join(locations) if locations else None

            postings.append(
                JobPosting(
                    company="Rippling",
                    job_id=str(object_id),
                    title=hit.get("name", ""),
                    location=location,
                    url=hit.get("url"),
                    posted_date=None,
                    tier="1",
                )
            )

        page += 1
        if total_pages is not None and page >= total_pages:
            break
        time.sleep(REQUEST_DELAY)

    return postings


# Accenture (Elasticsearch board via /api/accenture/elastic/findjobs) was
# removed: it posts thousands of near-identical "Custom Software Engineer"
# listings with no seniority/entry-level wording, so almost every one lands
# in filter_entry_level's "ambiguous" bucket and triggers an individual JD
# fetch - one run scanned 19705 postings dominated by Accenture postings
# alone, turning the filter step into tens of minutes of sequential HTTP
# requests. Re-add only alongside a fix that doesn't fan out a JD fetch per
# templated posting (e.g. a tighter keyword, a per-company ambiguous-title
# cap, or deduping by title+JD-template before filtering).


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
                    location=job.get("normalized_location") or job.get("location"),
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

    logger.info(f"Apple: GET {url}")
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        html = response.text

        # Extract __staticRouterHydrationData from HTML. Apple wraps the data
        # as a JSON-encoded string passed to JSON.parse(...), so it must be
        # decoded twice: once for the JS string literal, once for the JSON payload.
        match = re.search(
            r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((\".*?\")\)\s*;",
            html,
            re.DOTALL,
        )
        if not match:
            logger.warning("Could not find hydration data in Apple jobs page")
            return []

        try:
            data = json.loads(json.loads(match.group(1)))
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
                                locations = result.get("locations") or []
                                location = (
                                    ", ".join(
                                        loc.get("name", "")
                                        for loc in locations
                                        if loc.get("name")
                                    )
                                    or None
                                )
                                url = f"https://jobs.apple.com/en-us/details/{pos_id}/{result.get('transformedPostingTitle', 'job')}"
                                jobs.append(
                                    JobPosting(
                                        company="Apple",
                                        job_id=str(pos_id),
                                        title=title,
                                        location=location,
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
    """Fetch the Databricks SSR board using its job-detail links."""
    url = "https://www.databricks.com/company/careers/open-positions"
    html = fetch_html(url)
    return (
        parse_job_links(
            html,
            company="Databricks",
            base_url=url,
            link_pattern=r"/(company/careers/open-positions|careers)/.+",
            location_selectors=("[class*='location']", "[data-testid*='location']"),
        )
        if html
        else []
    )


def fetch_uber() -> list[JobPosting]:
    """Fetch Uber's server-rendered job list."""
    url = "https://jobs.uber.com/en/jobs/"
    html = fetch_html(url)
    return (
        parse_job_links(
            html,
            company="Uber",
            base_url=url,
            link_pattern=r"/en/jobs/[^/?#]+",
            location_selectors=("[class*='location']", "[data-testid*='location']"),
            extra_selector="[class*='team']",
        )
        if html
        else []
    )


def fetch_intuit(keyword: str = "software engineer") -> list[JobPosting]:
    """Fetch Intuit's TalentBrew SSR search results for one keyword."""
    url = f"https://jobs.intuit.com/search-jobs?k={quote(keyword)}&l=&orgIds=27595"
    html = fetch_html(url)
    if not html:
        return []
    postings = parse_job_links(
        html,
        company="Intuit",
        base_url=url,
        link_pattern=r"/(job|job-details)/",
        location_selectors=("[class*='location']", ".job-location"),
    )
    return postings


def fetch_ea() -> list[JobPosting]:
    """Fetch EA's Avature SSR board and retain its visible Role ID."""
    url = "https://jobs.ea.com/en_US/careers/SearchJobs"
    html = fetch_html(url)
    return (
        parse_job_links(
            html,
            company="EA",
            base_url=url,
            link_pattern=r"/job/|/careers/JobDetail",
            location_selectors=("[class*='location']",),
            role_id_pattern=r"Role\s*ID\s*[:#]?\s*(\d+)",
        )
        if html
        else []
    )


def fetch_incepto() -> list[JobPosting]:
    """Fetch jobs from Incepto."""
    url = "https://join.com/companies/incepto-medical"
    postings = []

    logger.info(f"Incepto: GET {url}")
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
                data = json.loads(next_data.get_text())
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
        logger.info("D.E. Shaw: GET https://www.deshaw.com/careers")
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
            data = json.loads(next_data.get_text())
            build_id = data.get("buildId")
        except json.JSONDecodeError:
            logger.warning("Could not parse D.E. Shaw __NEXT_DATA__")
            return []

        if not build_id:
            logger.warning("Could not extract buildId from D.E. Shaw page")
            return []

        # Step 2: Fetch job data using buildId
        url = f"https://www.deshaw.com/_next/data/{build_id}/en/careers.json"
        logger.info(f"D.E. Shaw: GET {url}")
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


def fetch_wells_fargo() -> list[JobPosting]:
    """Fetch Wells Fargo's SSR board, never its session-bound widget endpoint."""
    url = "https://www.wellsfargojobs.com/en/jobs/"
    html = fetch_html(url)
    return (
        parse_job_links(
            html,
            company="Wells Fargo",
            base_url=url,
            link_pattern=r"/en/jobs/[^/?#]+",
            location_selectors=("[class*='location']",),
        )
        if html
        else []
    )


def fetch_nike() -> list[JobPosting]:
    """Fetch Nike's Paradox.ai-powered SSR board.

    The site's own location query params (?location=India) are ignored by
    its SSR - filtering only happens client-side via JS after hydration.
    filter[country][0]=India is the one param confirmed (via direct
    inspection) to actually change the server-rendered result set.

    Doesn't use parse_job_links: each job card has two separate anchors
    pointing at the same /job/R-xxxx URL (the title link and a "View Job"
    apply button), which would otherwise be counted as two postings - one
    with the real title, one titled "View Job".
    """
    url = "https://careers.nike.com/jobs?filter%5Bcountry%5D%5B0%5D=India"
    html = fetch_html(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    postings = []
    seen: set[str] = set()
    for link in soup.select("a.results-list__item-title--link[href]"):
        href = link.get("href")
        title = text_or_none(link)
        if not href or not title:
            continue

        card = link.find_parent("li")
        location = text_or_none(
            card.select_one(".results-list__item-street--label") if card else None
        )

        job_id_match = re.search(r"/job/(R-\d+)", href)
        job_id = job_id_match.group(1) if job_id_match else hash_job_id(title, location)
        if job_id in seen:
            continue
        seen.add(job_id)

        postings.append(
            JobPosting(
                company="Nike",
                job_id=job_id,
                title=title,
                location=location,
                url=urljoin(url, href),
                posted_date=None,
                tier="1",
            )
        )
    return postings


def fetch_ibm_search(company: str, keyword: str | None = None) -> list[JobPosting]:
    """Fetch IBM's careers search (also used for HashiCorp, which has no
    standalone board post-acquisition - its "View open positions" link on
    hashicorp.com points straight at this same search with q=hashicorp).

    The page itself is client-rendered with no data in its HTML/__NEXT_DATA__;
    it's backed by a raw Elasticsearch-style query DSL POST to
    www-api.ibm.com/search/api/v2 (appId "careers", scopes ["careers2"]),
    found via live network inspection - no auth needed. `dcdate` (confirmed
    via the UI's "Newest To Oldest" sort, which sends sort dcdate:desc) is the
    posted-date field; keyword search uses the same simple_query_string
    clause the site's own search box sends.
    """
    url = "https://www-api.ibm.com/search/api/v2"
    postings = []
    offset = 0
    page_size = 100
    total = None
    pages_fetched = 0

    must = (
        [
            {
                "simple_query_string": {
                    "query": keyword,
                    "fields": [
                        "keywords^1",
                        "body^1",
                        "url^2",
                        "description^2",
                        "h1s_content^2",
                        "title^3",
                        "field_text_01",
                    ],
                }
            }
        ]
        if keyword
        else []
    )

    while pages_fetched < MAX_PAGES:
        pages_fetched += 1
        payload = {
            "appId": "careers",
            "scopes": ["careers2"],
            "query": {"bool": {"must": must}},
            "size": page_size,
            "from": offset,
            "sort": [{"dcdate": "desc"}, {"_score": "desc"}],
            "lang": "zz",
            "localeSelector": {},
            "sm": {"query": keyword or "", "lang": "zz"},
            "_source": ["_id", "title", "url", "field_keyword_19", "dcdate"],
        }
        data = fetch_with_retry(url, method="POST", json_data=payload)
        hits = (data or {}).get("hits", {})
        if total is None:
            total = hits.get("total", {}).get("value", 0)

        results = hits.get("hits", [])
        if not results:
            break

        for hit in results:
            source = hit.get("_source", {})
            job_url = source.get("url")
            id_match = re.search(r"jobId=(\d+)", job_url or "")
            job_id = id_match.group(1) if id_match else hit.get("_id", "")
            postings.append(
                JobPosting(
                    company=company,
                    job_id=str(job_id),
                    title=source.get("title", ""),
                    location=source.get("field_keyword_19"),
                    url=job_url,
                    posted_date=source.get("dcdate"),
                    tier="1",
                )
            )

        offset += len(results)
        if offset >= (total or 0):
            break
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_ibm() -> list[JobPosting]:
    return fetch_ibm_search("IBM")


def fetch_hashicorp() -> list[JobPosting]:
    return fetch_ibm_search("HashiCorp", keyword="hashicorp")


def fetch_moodys() -> list[JobPosting]:
    """Fetch Moody's TalentBrew SSR board, paginating via ?p=N.

    Each job link carries its numeric job ID directly as a data-job-id
    attribute (data-job-id also appears on an unrelated "save job" button in
    the same card, so the selector is scoped to the title link specifically).
    An out-of-range page still returns 200 with a much smaller page and zero
    matches for that selector, which is what actually stops pagination.
    """
    postings = []
    page = 1
    while page <= MAX_PAGES:
        url = f"https://careers.moodys.com/en/search-jobs?p={page}"
        html = fetch_html(url)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        links = soup.select("a.search-results-list__job-link[data-job-id]")
        if not links:
            break

        for link in links:
            job_id = link.get("data-job-id")
            title = text_or_none(link)
            if not job_id or not title:
                continue
            card = link.find_parent("li", class_="search-results-list__item")
            location = text_or_none(card.select_one(".job-location") if card else None)
            href = link.get("href", "")
            postings.append(
                JobPosting(
                    company="Moody's",
                    job_id=str(job_id),
                    title=title,
                    location=location,
                    url=urljoin(url, href) if href else None,
                    posted_date=None,
                    tier="1",
                )
            )

        page += 1
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_siemens_portal(keyword: str, company: str) -> list[JobPosting]:
    """Fetch a Siemens Avature-portal board filtered by keyword (used for
    Brightly, a Siemens subsidiary with no standalone careers board - its
    listings live on Siemens' own global portal under a keyword filter).

    folderRecordsPerPage is accepted by the server but doesn't change the
    actual page size - it always returns 6 results per page and exposes real
    pagination via folderOffset (confirmed live: requesting
    folderRecordsPerPage=500 still returned only 6 articles, while the page's
    own "next" links all carried folderRecordsPerPage=6&folderOffset=N).
    """
    postings = []
    offset = 0
    page_size = 6
    pages_fetched = 0

    while pages_fetched < MAX_PAGES:
        pages_fetched += 1
        url = (
            f"https://jobs.siemens.com/en_US/externaljobs/SearchJobs/{quote(keyword)}/"
            f"?listFilterMode=1&folderRecordsPerPage={page_size}&folderOffset={offset}"
        )
        html = fetch_html(url)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        articles = soup.select("article.article--result")
        if not articles:
            break

        for article in articles:
            link = article.select_one("h3 a.link")
            title = text_or_none(link)
            href = link.get("href") if link else None
            if not title or not href:
                continue

            job_id_el = article.select_one(".list-item-jobId")
            job_id_text = text_or_none(job_id_el) or ""
            job_id_match = re.search(r"(\d+)", job_id_text)
            job_id = job_id_match.group(1) if job_id_match else hash_job_id(title, href)

            location = text_or_none(article.select_one(".list-item-location"))

            postings.append(
                JobPosting(
                    company=company,
                    job_id=job_id,
                    title=title,
                    location=location,
                    url=href,
                    posted_date=None,
                    tier="1",
                )
            )

        if len(articles) < page_size:
            break
        offset += page_size
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_brightly() -> list[JobPosting]:
    return fetch_siemens_portal("brightly", "Brightly")


def fetch_standard_chartered() -> list[JobPosting]:
    """Fetch Standard Chartered's SAP SuccessFactors (Jobs2Web/CSB) board via
    its recruiting/v1/jobs search API, pre-filtered to India server-side."""
    postings = []
    page = 0
    while page < MAX_PAGES:
        data = fetch_with_retry(
            "https://jobs.standardchartered.com/services/recruiting/v1/jobs",
            method="POST",
            json_data={
                "keywords": "",
                "locale": "en_GB",
                "location": "India",
                "pageNumber": page,
                "sortBy": "recent",
            },
        )
        if not data:
            break

        results = data.get("jobSearchResult", [])
        if not results:
            break

        for item in results:
            job = item.get("response", {})
            job_id = str(job.get("id", ""))
            title = job.get("unifiedStandardTitle", "")
            locations = job.get("jobLocationShort") or []
            location = ", ".join(loc.strip() for loc in locations if loc.strip()) or None
            locale = (job.get("supportedLocales") or ["en_GB"])[0]
            # urlTitle is a pre-slugified, already-percent-encoded version of
            # the title (hyphens, "/" stripped) - unifiedStandardTitle can
            # contain a literal "/" (e.g. "... (India/China) ..."), which
            # 404s even when percent-encoded, so it can't be used directly.
            url_title = job.get("urlTitle") or ""
            url = (
                f"https://jobs.standardchartered.com/job/{url_title}/{job_id}-{locale}"
                if url_title and job_id
                else None
            )
            postings.append(
                JobPosting(
                    company="Standard Chartered",
                    job_id=job_id,
                    title=title,
                    location=location,
                    url=url,
                    posted_date=job.get("unifiedStandardStart"),
                    tier="1",
                )
            )

        total_jobs = data.get("totalJobs", 0)
        page += 1
        if page * 10 >= total_jobs:
            break
        time.sleep(REQUEST_DELAY)

    return postings


# === JIO (ASP.NET WEBFORMS) ===

# Jio's careers site is legacy ASP.NET WebForms: pagination is a postback
# (__doPostBack) driven by a dropdown, not a URL param, so each subsequent
# page requires resubmitting the full form state (__VIEWSTATE and friends)
# with an updated page number - there's no bare GET for "page 2". Despite
# that, no browser/JS engine is actually needed: the postback is a plain
# form POST that `requests` can replicate directly.
JIO_TECH_CATEGORIES = {"Engineering & Technology", "IT & Systems", "Infrastructure"}


def _jio_form_fields(soup: BeautifulSoup) -> dict[str, str]:
    """Snapshot every field in the page's <form> so a postback can resubmit
    the full WebForms state (server validates __VIEWSTATE/__EVENTVALIDATION
    against exactly what it last rendered)."""
    fields: dict[str, str] = {}
    form = soup.find("form")
    if not form:
        return fields
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name or inp.get("type") in ("submit", "button", "image"):
            continue
        fields[name] = inp.get("value", "")
    for select in form.find_all("select"):
        name = select.get("name")
        if not name:
            continue
        chosen = select.find("option", selected=True) or select.find("option")
        fields[name] = chosen.get("value", "") if chosen else ""
    return fields


def _parse_jio_jobs(html: str) -> list[JobPosting]:
    soup = BeautifulSoup(html, "html.parser")
    title_links = soup.find_all("a", id=re.compile(r"_hylUser_\d+$"))
    location_spans = soup.find_all("span", id=re.compile(r"_Label2_\d+$"))
    date_spans = soup.find_all("span", id=re.compile(r"_Label1_\d+$"))

    postings = []
    for link, loc_span, date_span in zip(title_links, location_spans, date_spans):
        text = text_or_none(link) or ""
        match = re.search(r"^(.*)\(\s*(\d+)\s*\)\s*$", text)
        title = match.group(1).strip() if match else text
        job_id = match.group(2) if match else hash_job_id(text)
        href = link.get("href", "")
        # Jio operates exclusively within India, but its postings are spread
        # across small towns (e.g. "Sambalpur", "Yavatmal") that aren't in
        # the shared India-city whitelist used for global companies - tag
        # the country explicitly rather than growing that whitelist for one
        # single-country source.
        city = text_or_none(loc_span)
        location = f"{city}, India" if city else "India"
        postings.append(
            JobPosting(
                company="Jio",
                job_id=job_id,
                title=title,
                location=location,
                url=urljoin("https://careers.jio.com/", href) if href else None,
                posted_date=text_or_none(date_span),
                tier="1",
            )
        )
    return postings


def _fetch_jio_category(session: requests.Session, url: str) -> list[JobPosting]:
    postings = []
    logger.info(f"Jio: GET {url}")
    try:
        response = session.get(url, headers=REQUEST_HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.Timeout as exc:
        raise FetchTimeoutError(f"Jio timed out fetching {url}") from exc
    except requests.RequestException as exc:
        logger.warning(f"Jio: could not fetch {url}: {exc}")
        return postings

    html = response.text
    postings.extend(_parse_jio_jobs(html))

    soup = BeautifulSoup(html, "html.parser")
    pager_select = soup.find("select", attrs={"name": re.compile(r"PageDropDownList$")})
    if not pager_select:
        return postings
    pager_field = pager_select.get("name")
    total_pages = len(pager_select.find_all("option"))

    for page_num in range(2, min(total_pages, MAX_PAGES) + 1):
        fields = _jio_form_fields(soup)
        fields["__EVENTTARGET"] = pager_field
        fields["__EVENTARGUMENT"] = ""
        fields[pager_field] = str(page_num)
        logger.info(f"Jio: POST {url} (page {page_num}/{total_pages})")
        try:
            response = session.post(url, data=fields, headers=REQUEST_HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.Timeout as exc:
            raise FetchTimeoutError(f"Jio timed out fetching page {page_num}") from exc
        except requests.RequestException as exc:
            logger.warning(f"Jio: could not fetch page {page_num} of {url}: {exc}")
            break

        html = response.text
        postings.extend(_parse_jio_jobs(html))
        soup = BeautifulSoup(html, "html.parser")
        time.sleep(REQUEST_DELAY)

    return postings


def fetch_jio() -> list[JobPosting]:
    """Fetch Jio's tech-relevant job categories, paginating each via its
    ASP.NET WebForms postback mechanism."""
    base = "https://careers.jio.com"
    session = requests.Session()
    postings = []

    try:
        cat_url = (
            f"{base}/frmJobCategories.aspx?func=/wASbQn4xyQ=&loc=/wASbQn4xyQ="
            f"&expreq=/wASbQn4xyQ=&flag=/wASbQn4xyQ="
        )
        logger.info(f"Jio: GET {cat_url}")
        response = session.get(cat_url, headers=REQUEST_HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        category_urls = []
        for desc_input in soup.select("input[id*='hdDescription']"):
            name = desc_input.get("value", "")
            if name not in JIO_TECH_CATEGORIES:
                continue
            link = desc_input.find_next("a", href=re.compile(r"frmfuncwisejob\.aspx"))
            if link and link.get("href"):
                category_urls.append(urljoin(base + "/", link["href"]))

        logger.info(f"Jio: {len(category_urls)} tech categories found")
        for cat_url in category_urls:
            postings.extend(_fetch_jio_category(session, cat_url))
            time.sleep(REQUEST_DELAY)
    except requests.Timeout as exc:
        raise FetchTimeoutError("Jio timed out") from exc
    except requests.RequestException as exc:
        logger.warning(f"Jio: {exc}")

    return postings


def extract_csrf_token(response: requests.Response) -> str | None:
    """Find the session-bound Phenom CSRF token from headers, cookies, or HTML."""
    for key, value in response.headers.items():
        if "csrf" in key.lower() or "xsrf" in key.lower():
            return value
    for key, value in response.cookies.items():
        if "csrf" in key.lower() or "xsrf" in key.lower():
            return value
    patterns = (
        r'<meta[^>]+(?:name|id)=["\'][^"\']*(?:csrf|xsrf)[^"\']*["\'][^>]+content=["\']([^"\']+)',
        r'["\'](?:csrfToken|csrf_token|X-CSRF-TOKEN)["\']\s*[:=]\s*["\']([^"\']+)',
    )
    for pattern in patterns:
        match = re.search(pattern, response.text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def fetch_phenom(
    domain: str, company: str, ref_num: str, page_id: str
) -> list[JobPosting]:
    """Fetch a Phenom board with its session cookies and CSRF token."""
    session = requests.Session()
    search_url = f"https://{domain}/us/en/search-results"
    logger.info(f"{company}: GET {search_url}")
    try:
        response = session.get(search_url, headers=REQUEST_HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        token = extract_csrf_token(response)
        if not token:
            logger.warning("%s: no session CSRF token found; skipping", company)
            return []
        payload = {
            "sortBy": "",
            "subsearch": "software engineer",
            "from": 0,
            "jobs": True,
            "counts": True,
            "all_fields": [
                "category",
                "country",
                "state",
                "city",
                "type",
                "postalCode",
                "remote",
            ],
            "pageName": "search-results1",
            "size": 10,
            "clearAll": False,
            "jdsource": "facets",
            "isSliderEnable": False,
            "pageId": page_id,
            "siteType": "external",
            "keywords": "",
            "global": True,
            "selected_fields": {},
            "lang": "en_us",
            "deviceType": "desktop",
            "country": "us",
            "refNum": ref_num,
            "ddoKey": "eagerLoadRefineSearchSession",
        }
        postings: list[JobPosting] = []
        offset = 0
        for _ in range(MAX_PAGES):
            payload["from"] = offset
            logger.info(f"{company}: POST {domain}/widgets (from={offset})")
            widget_response = session.post(
                f"https://{domain}/widgets",
                json=payload,
                headers={
                    **REQUEST_HEADERS,
                    "Content-Type": "application/json",
                    "X-CSRF-TOKEN": token,
                },
                timeout=TIMEOUT,
            )
            widget_response.raise_for_status()
            data = widget_response.json()
            jobs = data.get("jobs", data.get("data", {}).get("jobs", []))
            if isinstance(jobs, dict):
                jobs = jobs.get("items", jobs.get("content", []))
            if not jobs:
                break
            for job in jobs:
                title = job.get("title") or job.get("name") or ""
                location = job.get("location") or job.get("city")
                job_id = (
                    job.get("jobId")
                    or job.get("id")
                    or job.get("reqId")
                    or hash_job_id(title, location)
                )
                postings.append(
                    JobPosting(
                        company,
                        str(job_id),
                        title,
                        location,
                        job.get("url") or job.get("applyUrl"),
                        job.get("postedDate"),
                        "1",
                    )
                )
            offset += len(jobs)
            if len(jobs) < payload["size"]:
                break
            time.sleep(REQUEST_DELAY)
        return postings
    except requests.Timeout as exc:
        raise FetchTimeoutError(f"{company} timed out") from exc
    except (requests.RequestException, ValueError) as exc:
        logger.warning("%s Phenom fetch failed: %s", company, exc)
        return []


def fetch_hpe() -> list[JobPosting]:
    return fetch_phenom("careers.hpe.com", "HPE", "HPE1US", "page15")


def fetch_mastercard() -> list[JobPosting]:
    return fetch_phenom("careers.mastercard.com", "Mastercard", "MASRUS", "page15")


# Adobe and Warner Bros Discovery require their live refNum/pageId capture.
# Do not guess those session-bound values; configure them before enabling.
# Boston Consulting Group (careers.bcg.com) is on the same Phenom /widgets
# platform but its refNum/pageId were never captured either - same blocker,
# needs a live requests.Session() inspection (see fetch_phenom docstring)
# before it can be added.


# Not yet implemented - platform identified but endpoint/payload unconfirmed
# during research, so implementing blind would mean guessing values against
# a live API:
#   - Flipkart (TurboHire): backed by thapi.azurewebsites.net, exact REST
#     path unconfirmed.
#   - KKR: Greenhouse-backed but on a private/embedded board - the public
#     Greenhouse API returns no data, so this needs Playwright against KKR's
#     own site with selectors that were never captured.
# Each needs a live browser network-inspection pass (mirroring how the rest
# of this module's endpoints were confirmed) before a fetcher can be written.


# Sources explicitly identified in instruction.md. Cohesity and Concentrix
# remain intentionally excluded: their underlying APIs are unresolved.
TIER1_COMPANIES = {
    "Harness": fetch_harness,
    "Razorpay": fetch_razorpay,
    "Arcesium": fetch_arcesium,
    "Tower Research Capital": fetch_tower_research,
    "PhonePe": fetch_phonepe,
    "Paytm": fetch_paytm,
    "S&P Global": fetch_sp_global,
    "Schneider Electric": fetch_schneider_electric,
    "DocuSign": fetch_docusign,
    "Microsoft": fetch_microsoft,
    "NVIDIA": fetch_nvidia,
    "Qualcomm": fetch_qualcomm,
    "Ericsson": fetch_ericsson,
    "Morgan Stanley": fetch_morgan_stanley,
    "JPMorgan": fetch_jpmorgan,
    "Texas Instruments": fetch_texas_instruments,
    "American Express": fetch_american_express,
    "Oracle": fetch_oracle,
    "Salesforce": fetch_salesforce,
    "Thomson Reuters": fetch_thomson_reuters,
    "Visa": fetch_visa,
    "Autodesk": fetch_autodesk,
    "Apple": fetch_apple,
    "Databricks": fetch_databricks,
    "Uber": fetch_uber,
    "Intuit": fetch_intuit,
    "EA": fetch_ea,
    "Wells Fargo": fetch_wells_fargo,
    "Nike": fetch_nike,
    "Standard Chartered": fetch_standard_chartered,
    "Jio": fetch_jio,
    "Incepto": fetch_incepto,
    "D.E. Shaw": fetch_deshaw,
    "Atlassian": fetch_atlassian,
    "Amazon": fetch_amazon,
    "Rippling": fetch_rippling,
    "HPE": fetch_hpe,
    "Mastercard": fetch_mastercard,
    "Moody's": fetch_moodys,
    "PayPal": fetch_paypal,
    "Coinbase": fetch_coinbase,
    "IBM": fetch_ibm,
    "HashiCorp": fetch_hashicorp,
    "Kyndryl": fetch_kyndryl,
    "Brightly": fetch_brightly,
    "EXL": fetch_exl,
}


def fetch_all_tier1() -> tuple[list[JobPosting], list[str]]:
    """Fetch all tier 1 companies, return postings and list of failed companies.

    Every request is capped at TIMEOUT (15s). A company whose fetch times out
    is queued and retried once, after all other companies have been
    attempted; a second timeout on retry marks it as failed.
    """
    all_postings = []
    failed = []
    retry_queue = []

    for company_name, fetcher in TIER1_COMPANIES.items():
        logger.info(f"=== Starting {company_name} ===")
        try:
            postings = fetcher()
            logger.info(f"{company_name}: {len(postings)} jobs fetched")
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
        logger.info(
            f"Retrying {len(retry_queue)} timed-out compan{'y' if len(retry_queue) == 1 else 'ies'}..."
        )
        for company_name, fetcher in retry_queue:
            logger.info(f"=== Retrying {company_name} ===")
            try:
                postings = fetcher()
                logger.info(
                    f"{company_name}: {len(postings)} jobs fetched (retry succeeded)"
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

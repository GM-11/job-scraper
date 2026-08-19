import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

try:
    from dateutil import parser as date_parser
except ImportError:
    date_parser = None

logger = logging.getLogger(__name__)

# Posting must have been posted within this many days to be surfaced
RECENCY_WINDOW_DAYS = 7

# Exclude postings that explicitly target a graduating class later than this
# (e.g. "Class of 2027", "New Grad 2028", "Summer 2027 Intern")
MAX_GRAD_YEAR = 2026

# Max years of experience a posting may require and still count as
# early-career-friendly. Titles/JDs asking for more than this are excluded.
MAX_YEARS_EXPERIENCE = 2

JD_FETCH_TIMEOUT = 10
JD_FETCH_DELAY = 0.3
JD_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Titles containing these indicate an entry-level / fresher / SDE-1 role.
# A title matching one of these is accepted outright without needing a JD
# lookup, even if it doesn't state a years-of-experience figure.
ENTRY_LEVEL_PATTERNS = [
    r"\bentry[\s-]?level\b",
    r"\bnew\s*grad(uate)?\b",
    r"\bgraduate\b",
    r"\bfresher\b",
    r"\bjunior\b",
    r"\bassociate\b",
    r"\bearly\s*career\b",
    r"\bcampus\b",
    r"\buniversity\s*(hire|grad|program)?\b",
    r"\bintern(ship)?\b",
    r"\bsde\s*[-\s]?1\b",
    r"\bsde\s*[-\s]?i\b(?!i)",
    r"\bsoftware\s*engineer\s*[-\s]?1\b",
    r"\bsoftware\s*engineer\s*[-\s]?i\b(?!i)",
    r"\bswe\s*[-\s]?1\b",
    r"\bswe\s*[-\s]?i\b(?!i)",
    r"\bl3\b",
    r"\bp1\b",
]

# Titles containing these are explicitly NOT early-career; exclude even if
# an entry-level pattern also matches elsewhere in a noisy title. Numeric
# years-of-experience are handled separately (see title_min_years), not
# baked into this list, since "2 years" should pass but "4 years" shouldn't.
SENIORITY_EXCLUDE_PATTERNS = [
    r"\bsenior\b",
    r"\bsr\.?\b",
    r"\bstaff\b",
    r"\bprincipal\b",
    r"\blead\b",
    r"\bmanager\b",
    r"\bdirector\b",
    r"\bvp\b",
    r"\bvice\s*president\b",
    r"\bhead\s*of\b",
    r"\barchitect\b",
    r"\bsde\s*[-\s]?(2|3|4|ii|iii|iv)\b",
    r"\bswe\s*[-\s]?(2|3|4|ii|iii|iv)\b",
    r"\bengineer\s*[-\s]?(2|3|4|ii|iii|iv)\b",
    r"\bscientist\s*[-\s]?(2|3|4|ii|iii|iv)\b",
    r"\bdeveloper\s*[-\s]?(2|3|4|ii|iii|iv)\b",
    r"\bmid[\s-]?level\b",
    r"\bexperienced\b",
]

# Title must reference a technical/engineering role. Broadened beyond plain
# "software engineer" to cover adjacent technical disciplines (data/ML,
# infra/ops, mobile/embedded/systems, application/platform/solutions) that
# early-career candidates commonly apply to.
ROLE_KEYWORD_PATTERNS = [
    # software engineering
    r"\bsoftware\s*(development\s*)?engineer\b",
    r"\bsde\b",
    r"\bswe\b",
    r"\bsoftware\s*developer\b",
    r"\bdeveloper\b",
    r"\bfull[\s-]?stack\b",
    r"\bback[\s-]?end\b",
    r"\bfront[\s-]?end\b",
    r"\bprogrammer\b",
    # data / ML
    r"\bdata\s*engineer\b",
    r"\bdata\s*scientist\b",
    r"\bmachine\s*learning\b",
    r"\bml\s*engineer\b",
    r"\bai\s*engineer\b",
    r"\bartificial\s*intelligence\b",
    # infra / ops
    r"\bdevops\b",
    r"\bsite\s*reliability\b",
    r"\bsre\b",
    r"\bcloud\s*engineer\b",
    r"\bplatform\s*engineer\b",
    r"\binfrastructure\s*engineer\b",
    # mobile / embedded / systems
    r"\bmobile\s*engineer\b",
    r"\bios\s*engineer\b",
    r"\bandroid\s*engineer\b",
    r"\bembedded\s*(software\s*)?engineer\b",
    r"\bsystems?\s*engineer\b",
    r"\bnetwork\s*engineer\b",
    # application / platform / solutions
    r"\bapplication\s*engineer\b",
    r"\bsolutions?\s*engineer\b",
]

# Location strings indicating an India-based posting - country name plus the
# major Indian tech hub cities, for sources that only report a city.
INDIA_LOCATION_PATTERNS = [
    r"\bindia\b",
    r"\bbengaluru\b",
    r"\bbangalore\b",
    r"\bhyderabad\b",
    r"\bpune\b",
    r"\bmumbai\b",
    r"\bchennai\b",
    r"\bnew\s*delhi\b",
    r"\bdelhi\b",
    r"\bgurugram\b",
    r"\bgurgaon\b",
    r"\bnoida\b",
    r"\bkolkata\b",
    r"\bahmedabad\b",
]

_entry_re = re.compile("|".join(ENTRY_LEVEL_PATTERNS), re.IGNORECASE)
_exclude_re = re.compile("|".join(SENIORITY_EXCLUDE_PATTERNS), re.IGNORECASE)
_role_re = re.compile("|".join(ROLE_KEYWORD_PATTERNS), re.IGNORECASE)
_india_re = re.compile("|".join(INDIA_LOCATION_PATTERNS), re.IGNORECASE)

# Loose "N years" / "N-M years" / "N+ years" figure, for scanning titles
# (titles are short, so a bare years figure is almost always about
# experience, e.g. "Software Engineer (3+ years)").
_title_years_re = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:(?:to|-)\s*(\d{1,2})\s*)?\+?\s*years?\b", re.IGNORECASE
)

# Stricter figure for scanning full JD bodies, which is required to be near
# the word "experience" so it doesn't false-positive on copyright years,
# salary figures, "40 hours/week", etc.
_jd_years_re = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:(?:to|-)\s*(\d{1,2})\s*)?\+?\s*years?\s*"
    r"(?:of\s*)?(?:relevant\s*|professional\s*|prior\s*|hands-on\s*|work\s*)?"
    r"experience\b",
    re.IGNORECASE,
)


def _min_years(matches) -> Optional[int]:
    values = [int(g) for m in matches for g in m.groups() if g]
    return min(values) if values else None


def title_min_years(title: str) -> Optional[int]:
    """Smallest 'N years' figure stated directly in a title, or None."""
    if not title:
        return None
    return _min_years(_title_years_re.finditer(title))


def jd_min_years(text: str) -> Optional[int]:
    """Smallest 'N years ... experience' figure stated in JD text, or None."""
    if not text:
        return None
    return _min_years(_jd_years_re.finditer(text))


def title_status(title: str) -> str:
    """Classify a title as 'reject', 'accept', or 'ambiguous'.

    - 'reject': doesn't reference a technical role, has a seniority word
      (senior/staff/lead/...), or states a years-of-experience figure above
      MAX_YEARS_EXPERIENCE.
    - 'accept': references a technical role and either has an explicit
      entry-level signal or states a years figure at or below
      MAX_YEARS_EXPERIENCE.
    - 'ambiguous': references a technical role with no seniority signal in
      either direction (e.g. a plain "Software Engineer, Backend") - the JD
      needs to be checked to know whether it fits.
    """
    if not title:
        return "reject"

    if _exclude_re.search(title):
        return "reject"

    if not _role_re.search(title):
        return "reject"

    years = title_min_years(title)
    if years is not None:
        return "accept" if years <= MAX_YEARS_EXPERIENCE else "reject"

    if _entry_re.search(title):
        return "accept"

    return "ambiguous"


def fetch_jd_text(url: Optional[str]) -> Optional[str]:
    """Fetch a job posting page and return its visible text, or None on any
    failure (missing URL, network error, non-200)."""
    if not url:
        return None
    try:
        response = requests.get(
            url, headers={"User-Agent": JD_USER_AGENT}, timeout=JD_FETCH_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.debug(f"Could not fetch JD at {url}: {exc}")
        return None
    finally:
        time.sleep(JD_FETCH_DELAY)
    return BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)


def jd_is_acceptable(url: Optional[str], max_years: int = MAX_YEARS_EXPERIENCE) -> bool:
    """For an ambiguous title, fetch the JD and look for a stated
    years-of-experience requirement. If the JD can't be fetched or states no
    years figure, keep the posting rather than drop it - consistent with how
    missing recency/location data is handled elsewhere in this module.
    """
    text = fetch_jd_text(url)
    if text is None:
        return True
    years = jd_min_years(text)
    return years is None or years <= max_years


def is_entry_level_swe(title: str) -> bool:
    """Backward-compatible title-only check: True unless the title is
    rejected outright. Ambiguous titles (which need a JD lookup to resolve)
    are treated as passing here; use filter_entry_level() for the full
    title+JD pipeline.
    """
    return title_status(title) != "reject"


_year_re = re.compile(r"\b(20\d{2})\b")


def is_grad_year_acceptable(title: str, max_year: int = MAX_GRAD_YEAR) -> bool:
    """Exclude postings that explicitly target a graduating class later than
    max_year (e.g. 'Class of 2027', 'New Grad 2028', 'Summer 2027 Intern').

    Titles with no 4-digit year mentioned are accepted (can't determine
    target class, so don't drop them).
    """
    if not title:
        return True

    years = [int(y) for y in _year_re.findall(title)]
    if not years:
        return True

    return all(y <= max_year for y in years)


_relative_date_re = re.compile(
    r"posted\s+(today|yesterday|(\d+)\s*\+?\s*days?\s+ago)", re.IGNORECASE
)


def _parse_relative_date(text: str) -> Optional[datetime]:
    match = _relative_date_re.search(text)
    if not match:
        return None

    now = datetime.now(timezone.utc)
    token = match.group(1).lower()
    if token == "today":
        return now
    if token == "yesterday":
        return now - timedelta(days=1)

    days = match.group(2)
    if days:
        return now - timedelta(days=int(days))
    return None


def is_recently_posted(posted_date: Optional[str], window_days: int = RECENCY_WINDOW_DAYS) -> bool:
    """Return True if posted_date falls within the last window_days.

    Many sources don't expose a reliable posted date (Jibe, Databricks-style
    HTML boards, etc); when the date is missing or can't be parsed, the
    posting is kept rather than silently dropped, since diffing against
    seen_jobs.json already prevents re-surfacing stale postings after the
    first run.
    """
    if not posted_date:
        return True

    parsed = _parse_relative_date(posted_date)

    if parsed is None and date_parser is not None:
        try:
            parsed = date_parser.parse(posted_date, fuzzy=True)
        except (ValueError, OverflowError, TypeError):
            parsed = None

    if parsed is None:
        logger.debug(f"Could not parse posted_date {posted_date!r}, keeping posting")
        return True

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    return parsed >= cutoff


def is_india_location(location: Optional[str]) -> bool:
    """Return True if location is an India-based posting.

    Unlike is_recently_posted/is_grad_year_acceptable, this fails closed: a
    missing or unmatched location is rejected rather than kept. Fail-open
    here previously let global companies whose scrapers didn't populate
    location (e.g. a hardcoded None) leak non-India postings straight
    through; better to under-report than to surface out-of-India roles.
    """
    if not location:
        logger.debug("Rejecting posting with no location (fail-closed India filter)")
        return False
    matched = bool(_india_re.search(location))
    if not matched:
        logger.debug(f"Rejecting non-India location: {location!r}")
    return matched


def filter_entry_level(postings: list) -> list:
    """Filter a list of JobPosting objects to early-career-friendly
    technical roles that:
    - reference a broad technical role (SWE, data/ML, infra/ops, mobile/
      embedded/systems, application/platform/solutions)
    - don't require more than MAX_YEARS_EXPERIENCE years of experience,
      per the title or (when the title alone is ambiguous) the JD body
    - don't target a graduating class later than MAX_GRAD_YEAR
    - were posted within RECENCY_WINDOW_DAYS (when a date is available)
    - are India-based (when a location is available)
    """
    result = []
    for p in postings:
        status = title_status(p.title)
        if status == "reject":
            continue
        if status == "ambiguous" and not jd_is_acceptable(p.url):
            continue
        if not is_grad_year_acceptable(p.title):
            continue
        if not is_recently_posted(p.posted_date):
            continue
        if not is_india_location(p.location):
            continue
        result.append(p)
    return result

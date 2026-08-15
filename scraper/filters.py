import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

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

# Titles containing these indicate an entry-level / fresher / SDE-1 role
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

# Titles containing these are explicitly NOT entry-level; exclude even if
# an entry-level pattern also matches elsewhere in a noisy title
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
    r"\bsoftware\s*engineer\s*[-\s]?(2|3|4|ii|iii|iv)\b",
    r"\bmid[\s-]?level\b",
    r"\bexperienced\b",
    r"\b[2-9]\+?\s*years?\b",
]

# Title must reference a software engineering role
ROLE_KEYWORD_PATTERNS = [
    r"\bsoftware\s*(development\s*)?engineer\b",
    r"\bsde\b",
    r"\bswe\b",
    r"\bsoftware\s*developer\b",
    r"\bdeveloper\b",
    r"\bfull[\s-]?stack\b",
    r"\bback[\s-]?end\b",
    r"\bfront[\s-]?end\b",
    r"\bprogrammer\b",
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


def is_entry_level_swe(title: str) -> bool:
    """Return True if the job title looks like a fresher/entry-level/SDE-1
    software engineering role.

    A title matches only if it references a software engineering role AND
    contains an entry-level signal, and does NOT contain a seniority signal
    that would exclude it (senior, staff, lead, SDE-2/3, etc).
    """
    if not title:
        return False

    if _exclude_re.search(title):
        return False

    if not _role_re.search(title):
        return False

    return bool(_entry_re.search(title))


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

    Many sources don't expose location at all; when it's missing, the
    posting is kept rather than dropped, consistent with how missing dates
    are handled - we'd rather surface a possible match than silently lose
    postings from sources with incomplete location data.
    """
    if not location:
        return True
    return bool(_india_re.search(location))


def filter_entry_level(postings: list) -> list:
    """Filter a list of JobPosting objects to entry-level SWE roles that:
    - reference a software engineering role with an entry-level signal
    - don't target a graduating class later than MAX_GRAD_YEAR
    - were posted within RECENCY_WINDOW_DAYS (when a date is available)
    - are India-based (when a location is available)
    """
    return [
        p for p in postings
        if is_entry_level_swe(p.title)
        and is_grad_year_acceptable(p.title)
        and is_recently_posted(p.posted_date)
        and is_india_location(p.location)
    ]

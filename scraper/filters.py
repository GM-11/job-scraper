import re

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

_entry_re = re.compile("|".join(ENTRY_LEVEL_PATTERNS), re.IGNORECASE)
_exclude_re = re.compile("|".join(SENIORITY_EXCLUDE_PATTERNS), re.IGNORECASE)
_role_re = re.compile("|".join(ROLE_KEYWORD_PATTERNS), re.IGNORECASE)


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


def filter_entry_level(postings: list) -> list:
    """Filter a list of JobPosting objects to entry-level SWE roles only."""
    return [p for p in postings if is_entry_level_swe(p.title)]

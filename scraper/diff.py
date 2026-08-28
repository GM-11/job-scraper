import json
import logging
import os
from datetime import datetime
from pathlib import Path

from .models import JobPosting


logger = logging.getLogger(__name__)

SEEN_JOBS_PATH = Path(__file__).parent.parent / "data" / "seen_jobs.json"


def load_seen_jobs() -> dict:
    """Load seen jobs from seen_jobs.json, return empty dict if missing or empty/corrupt."""
    if not SEEN_JOBS_PATH.exists():
        return {}
    with open(SEEN_JOBS_PATH) as f:
        content = f.read()
    if not content.strip():
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"seen_jobs.json is corrupt ({e}), starting fresh")
        return {}


def save_seen_jobs(seen_jobs: dict) -> None:
    """Save seen jobs to seen_jobs.json."""
    SEEN_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_JOBS_PATH, "w") as f:
        # Sorted keys keep the committed file stable across runs, so diffs stay
        # readable and concurrent runs are less likely to touch the same lines.
        json.dump(seen_jobs, f, indent=2, sort_keys=True)
        f.write("\n")


def process_postings(current_postings: list[JobPosting]) -> tuple[list[JobPosting], dict]:
    """
    Compare current postings against seen_jobs.json.

    Returns:
        (new_postings, updated_seen_jobs_dict)
    """
    seen_jobs = load_seen_jobs()
    new_postings = []
    now = datetime.utcnow().isoformat() + "Z"

    for posting in current_postings:
        key = posting.dedup_key()
        if key not in seen_jobs:
            new_postings.append(posting)
            seen_jobs[key] = {"first_seen": now, "title": posting.title}
        else:
            seen_jobs[key]["title"] = posting.title

    return new_postings, seen_jobs

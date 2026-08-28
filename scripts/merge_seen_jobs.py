"""Merge two seen_jobs.json files into their union.

Used by the scrape workflow to resolve concurrent updates to
data/seen_jobs.json without relying on git's line-based merge, which
conflicts whenever two runs both append entries.

Usage: python scripts/merge_seen_jobs.py OURS THEIRS OUT
"""

import json
import sys
from pathlib import Path


def load(path: Path) -> dict:
    """Load a seen_jobs file, treating missing/empty/corrupt input as empty."""
    if not path.exists():
        return {}
    content = path.read_text()
    if not content.strip():
        return {}
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def merge(ours: dict, theirs: dict) -> dict:
    """Union both sides, keeping the earliest first_seen and our title."""
    merged = dict(theirs)
    for key, entry in ours.items():
        other = merged.get(key)
        if other is None:
            merged[key] = entry
            continue
        first_seen = min(
            (v for v in (entry.get("first_seen"), other.get("first_seen")) if v),
            default=None,
        )
        combined = {**other, **entry}
        if first_seen:
            combined["first_seen"] = first_seen
        merged[key] = combined
    return merged


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    ours, theirs, out = (Path(p) for p in sys.argv[1:])
    merged = merge(load(ours), load(theirs))
    out.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    print(f"merged {len(merged)} seen jobs -> {out}")


if __name__ == "__main__":
    main()

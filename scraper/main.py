import argparse
import logging
import sys

from .companies_tier1 import fetch_all_tier1
from .companies_tier2 import fetch_all_tier2
from .diff import process_postings, save_seen_jobs
from .filters import MAX_GRAD_YEAR, RECENCY_WINDOW_DAYS, filter_entry_level
from .notify import send_email

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_scraper(tier: str = "all") -> None:
    """Run the requested API (tier 1) and/or Playwright (tier 2) sources."""
    logger.info("Starting job scraper")

    try:
        postings = []
        failed = []
        if tier in ("1", "all"):
            logger.info("Fetching tier 1 companies (API-based)...")
            tier1_postings, tier1_failed = fetch_all_tier1()
            postings.extend(tier1_postings)
            failed.extend(tier1_failed)

        if tier in ("2", "all"):
            logger.info("Fetching tier 2 companies (Playwright-based)...")
            tier2_postings, tier2_failed = fetch_all_tier2()
            postings.extend(tier2_postings)
            failed.extend(tier2_failed)

        logger.debug(f"Fetched {len(postings)} postings")
        if failed:
            logger.warning(f"Failed to fetch: {', '.join(failed)}")

        # Filter down to early-career technical roles (SWE and adjacent
        # disciplines, up to ~MAX_YEARS_EXPERIENCE yrs), India-based, from
        # the last week
        postings = filter_entry_level(postings)
        logger.info(
            f"{len(postings)} postings match filters "
            f"(early-career technical, India, last {RECENCY_WINDOW_DAYS} days, grad year <= {MAX_GRAD_YEAR}):"
        )
        for p in postings:
            loc = f" — {p.location}" if p.location else ""
            logger.info(
                f"  [{p.company}] {p.title}{loc} (job_id={p.job_id}) {p.url or ''}"
            )

        # Process postings (diff against seen jobs)
        logger.info("Processing postings (diffing against seen jobs)...")
        new_postings, updated_seen = process_postings(postings)
        logger.info(f"Found {len(new_postings)} new postings")

        # Save updated seen jobs
        logger.info("Saving seen_jobs.json...")
        save_seen_jobs(updated_seen)
        logger.info("Saved updated seen_jobs.json")

        # Send email notification
        logger.info("Sending email notification...")
        try:
            send_email(new_postings, failed)
            if new_postings:
                logger.info("Sent email notification")
            else:
                logger.info("No new postings, sent 'no new jobs' email")
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)

        logger.info("Scraper completed successfully")
    except Exception as e:
        logger.error(f"Scraper failed: {e}", exc_info=True)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and notify on new job postings."
    )
    parser.add_argument(
        "--tier",
        choices=("1", "2", "all"),
        default="all",
        help="source tier to run (default: all)",
    )
    args = parser.parse_args()
    run_scraper(args.tier)


if __name__ == "__main__":
    main()

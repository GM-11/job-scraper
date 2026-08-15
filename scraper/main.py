import logging
import sys

from .companies_tier1 import fetch_all_tier1
from .companies_tier2 import fetch_all_tier2
from .diff import process_postings, save_seen_jobs
from .filters import filter_entry_level
from .notify import send_email


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_scraper() -> None:
    """Run the full scraper: tier 1 (API-based) and tier 2 (Playwright-based) together."""
    logger.info("Starting job scraper")

    try:
        logger.info("Fetching tier 1 companies (API-based)...")
        tier1_postings, tier1_failed = fetch_all_tier1()

        logger.info("Fetching tier 2 companies (Playwright-based)...")
        tier2_postings, tier2_failed = fetch_all_tier2()

        postings = tier1_postings + tier2_postings
        failed = tier1_failed + tier2_failed

        logger.info(f"Fetched {len(postings)} postings")
        if failed:
            logger.warning(f"Failed to fetch: {', '.join(failed)}")

        # Filter down to fresher/entry-level/SDE-1 software engineering roles only
        postings = filter_entry_level(postings)
        logger.info(f"{len(postings)} postings match entry-level/fresher/SDE-1 filter")

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
                logger.info("No new postings, skipping email")
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)

        logger.info("Scraper completed successfully")
    except Exception as e:
        logger.error(f"Scraper failed: {e}", exc_info=True)
        sys.exit(1)


def main():
    run_scraper()


if __name__ == "__main__":
    main()

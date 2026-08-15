import argparse
import logging
import sys

from .companies_tier1 import fetch_all_tier1
from .companies_tier2 import fetch_all_tier2
from .diff import process_postings, save_seen_jobs
from .notify import send_email


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def run_scraper(tier: int) -> None:
    """Run scraper for specified tier."""
    logger.info(f"Starting job scraper (tier {tier})")

    try:
        if tier == 1:
            logger.info("Fetching tier 1 companies (API-based)...")
            postings, failed = fetch_all_tier1()
        elif tier == 2:
            logger.info("Fetching tier 2 companies (Playwright-based)...")
            postings, failed = fetch_all_tier2()
        else:
            logger.error(f"Invalid tier: {tier}")
            sys.exit(1)

        logger.info(f"Fetched {len(postings)} postings")
        if failed:
            logger.warning(f"Failed to fetch: {', '.join(failed)}")

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
    parser = argparse.ArgumentParser(description="Job scraper for GitHub Actions")
    parser.add_argument("--tier", type=int, choices=[1, 2], required=True, help="Which tier to scrape (1 or 2)")
    args = parser.parse_args()

    run_scraper(args.tier)


if __name__ == "__main__":
    main()

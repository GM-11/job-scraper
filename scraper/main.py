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

    if tier == 1:
        postings, failed = fetch_all_tier1()
    elif tier == 2:
        postings, failed = fetch_all_tier2()
    else:
        logger.error(f"Invalid tier: {tier}")
        sys.exit(1)

    logger.info(f"Fetched {len(postings)} postings from {len(failed)} failed companies: {failed}")

    # Process postings (diff against seen jobs)
    new_postings, updated_seen = process_postings(postings)
    logger.info(f"Found {len(new_postings)} new postings")

    # Save updated seen jobs
    save_seen_jobs(updated_seen)
    logger.info("Saved updated seen_jobs.json")

    # Send email notification
    try:
        send_email(new_postings, failed)
        logger.info("Sent email notification")
    except Exception as e:
        logger.error(f"Failed to send email: {e}", exc_info=True)

    logger.info("Scraper completed successfully")


def main():
    parser = argparse.ArgumentParser(description="Job scraper for GitHub Actions")
    parser.add_argument("--tier", type=int, choices=[1, 2], required=True, help="Which tier to scrape (1 or 2)")
    args = parser.parse_args()

    run_scraper(args.tier)


if __name__ == "__main__":
    main()

import os
import smtplib
from email.mime.text import MIMEText

from .models import JobPosting


def send_email(new_postings: list[JobPosting], failed_companies: list[str] = None) -> None:
    """Send email notification of new job postings (or a no-new-jobs notice)."""
    if failed_companies is None:
        failed_companies = []

    if new_postings:
        body_lines = [f"{len(new_postings)} new job posting(s) found:\n"]
        by_company = {}
        for p in new_postings:
            by_company.setdefault(p.company, []).append(p)

        for company, jobs in sorted(by_company.items()):
            body_lines.append(f"\n=== {company} ({len(jobs)}) ===")
            for j in jobs:
                loc = f" — {j.location}" if j.location else ""
                date = f" ({j.posted_date})" if j.posted_date else ""
                body_lines.append(f"  • {j.title}{loc}{date}")
                body_lines.append(f"    Job ID: {j.job_id}")
                if j.url:
                    body_lines.append(f"    Link: {j.url}")

        subject = f"Job Alert: {len(new_postings)} new posting(s)"
    else:
        body_lines = ["No new job postings found in this run."]
        subject = "Job Alert: no new postings"

    if failed_companies:
        body_lines.append(f"\n\n⚠ Failed to check: {', '.join(failed_companies)}")

    body = "\n".join(body_lines)

    smtp_from = os.environ.get("SMTP_FROM")
    smtp_to = os.environ.get("SMTP_TO")
    smtp_password = os.environ.get("SMTP_PASSWORD")

    if not (smtp_from and smtp_to and smtp_password):
        import logging
        logging.warning("SMTP credentials not configured, skipping email")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = smtp_to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_from, smtp_password)
        server.send_message(msg)

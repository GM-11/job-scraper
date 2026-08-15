from dataclasses import dataclass
from typing import Optional


@dataclass
class JobPosting:
    company: str
    job_id: str
    title: str
    location: Optional[str]
    url: Optional[str]
    posted_date: Optional[str]
    tier: str

    def dedup_key(self) -> str:
        return f"{self.company}:{self.job_id}"

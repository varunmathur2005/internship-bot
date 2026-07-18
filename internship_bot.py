from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import smtplib
import sqlite3
import ssl
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

USER_AGENT = "InternshipBot/1.0 (+personal job-search bot)"
TIMEOUT = 25


@dataclass(frozen=True)
class Job:
    company: str
    title: str
    location: str
    url: str
    source: str

    @property
    def key(self) -> str:
        normalized = "|".join(
            re.sub(r"\s+", " ", value.strip().lower())
            for value in (self.company, self.title, self.location, self.url.split("?")[0])
        )
        return hashlib.sha256(normalized.encode()).hexdigest()


class Client:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})

    def get(self, url: str, **kwargs) -> requests.Response:
        response = self.session.get(url, timeout=TIMEOUT, **kwargs)
        response.raise_for_status()
        return response


def simplify_jobs(client: Client, source: dict) -> list[Job]:
    text = client.get(source["url"]).text
    jobs: list[Job] = []
    previous_company = ""
    # Simplify lists use Markdown/HTML tables. This accepts common row shapes and
    # intentionally ignores rows without an outbound application link.
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.I | re.S)
        if len(cells) < 3:
            continue
        clean = [BeautifulSoup(f"<div>{cell}</div>", "html.parser").get_text(" ", strip=True) for cell in cells]
        company = previous_company if clean[0] in {"↳", ""} else clean[0]
        if company:
            previous_company = company
        soup = BeautifulSoup(row, "html.parser")
        links = [a.get("href", "") for a in soup.select("a[href]")]
        apply_url = next((u for u in reversed(links) if u.startswith("http")), "")
        if not apply_url:
            continue
        jobs.append(Job(company, clean[1], clean[2], apply_url, "Simplify"))
    return jobs


def speedyapply_jobs(client: Client, source: dict) -> list[Job]:
    jobs: list[Job] = []
    for url in source.get("urls", []):
        text = client.get(url).text
        for line in text.splitlines():
            if not line.startswith("|") or "<a " not in line or "<strong>" not in line:
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 4:
                continue
            company = BeautifulSoup(f"<div>{cells[0]}</div>", "html.parser").get_text(" ", strip=True)
            title = BeautifulSoup(f"<div>{cells[1]}</div>", "html.parser").get_text(" ", strip=True)
            location = BeautifulSoup(f"<div>{cells[2]}</div>", "html.parser").get_text(" ", strip=True)
            links = []
            for cell in cells[3:]:
                fragment = BeautifulSoup(f"<div>{cell}</div>", "html.parser")
                links.extend(a.get("href", "") for a in fragment.select("a[href]"))
            apply_url = next((link for link in links if link.startswith("http") and "i.imgur.com" not in link), "")
            if apply_url:
                jobs.append(Job(company, title, location, apply_url, "SpeedyApply"))
    return jobs


def greenhouse_jobs(client: Client, board: dict) -> list[Job]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board['board_token']}/jobs?content=true"
    data = client.get(url).json()
    return [
        Job(board.get("company", board["board_token"]), item.get("title", ""),
            item.get("location", {}).get("name", ""), item.get("absolute_url", ""), "Greenhouse")
        for item in data.get("jobs", [])
    ]


def lever_jobs(client: Client, board: dict) -> list[Job]:
    data = client.get(f"https://api.lever.co/v0/postings/{board['site']}?mode=json").json()
    return [
        Job(board.get("company", board["site"]), item.get("text", ""),
            item.get("categories", {}).get("location", ""), item.get("hostedUrl", ""), "Lever")
        for item in data
    ]


def ashby_jobs(client: Client, board: dict) -> list[Job]:
    data = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{board['board']}").json()
    return [
        Job(board.get("company", board["board"]), item.get("title", ""), item.get("location", ""),
            item.get("jobUrl") or item.get("applyUrl", ""), "Ashby")
        for item in data.get("jobs", [])
    ]


def page_jobs(client: Client, page: dict) -> list[Job]:
    soup = BeautifulSoup(client.get(page["url"]).text, "html.parser")
    jobs = []
    for anchor in soup.select(page.get("link_selector", "a[href]")):
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href")
        if title and href:
            jobs.append(Job(page["company"], title, page.get("location", ""),
                            urljoin(page["url"], href), "Career page"))
    return jobs


def matches(job: Job, filters: dict) -> bool:
    title = job.title.lower()
    combined = f"{job.title} {job.location} {job.company}".lower()
    if filters.get("require_internship_word", True) and not re.search(r"\bintern(ship)?\b", combined):
        return False
    if filters.get("include_keywords") and not any(k.lower() in title for k in filters["include_keywords"]):
        return False
    if any(k.lower() in combined for k in filters.get("exclude_keywords", [])):
        return False
    years = [str(y) for y in filters.get("years", [])]
    if years:
        mentioned_years = re.findall(r"\b20\d{2}\b", combined)
        if mentioned_years and not any(y in combined for y in years):
            return False
        if filters.get("require_target_year", True) and not any(y in combined for y in years):
            return False
    seasons = [s.lower() for s in filters.get("seasons", [])]
    if seasons and any(s in combined for s in ("spring", "summer", "fall", "winter")):
        if not any(s in combined for s in seasons):
            return False
    countries = [c.lower() for c in filters.get("countries", [])]
    if countries and job.location and not location_allowed(job.location, countries):
        return False
    return bool(job.url and job.title)


def location_allowed(location: str, allowed: list[str]) -> bool:
    value = location.lower()
    us_states = "al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc"
    ca_provinces = "ab bc mb nb nl ns nt nu on pe qc sk yt"
    aliases = {
        "united states": ["united states", "usa", "u.s.", "us", "remote", "new york", "seattle", "california",
                          "san francisco", "boston", "austin", "chicago", "washington", "redmond"] + us_states.split(),
        "canada": ["canada", "toronto", "waterloo", "vancouver", "montreal", "ottawa", "calgary"] + ca_provinces.split(),
        "remote": ["remote"],
    }
    terms = [term for country in allowed for term in aliases.get(country, [country])]
    return any(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", value) for term in terms)


def collect(config: dict) -> tuple[list[Job], list[str]]:
    client, jobs, errors = Client(), [], []
    sources = config.get("sources", {})
    runners = []
    if sources.get("speedyapply", {}).get("enabled"):
        runners.append(("SpeedyApply", lambda: speedyapply_jobs(client, sources["speedyapply"])))
    if sources.get("simplify", {}).get("enabled"):
        runners.append(("Simplify", lambda: simplify_jobs(client, sources["simplify"])))
    for kind, fn in (("greenhouse", greenhouse_jobs), ("lever", lever_jobs), ("ashby", ashby_jobs)):
        for board in sources.get(kind, []) or []:
            runners.append((f"{kind}:{board.get('company', '')}", lambda b=board, f=fn: f(client, b)))
    for page in sources.get("pages", []) or []:
        runners.append((f"page:{page.get('company', '')}", lambda p=page: page_jobs(client, p)))
    for name, runner in runners:
        try:
            jobs.extend(runner())
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    unique = {job.key: job for job in jobs if matches(job, config.get("filters", {}))}
    return sorted(unique.values(), key=lambda j: (j.company.lower(), j.title.lower())), errors


def init_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS seen_jobs (job_key TEXT PRIMARY KEY, first_seen TEXT NOT NULL, payload TEXT NOT NULL)")
    return db


def unseen(db: sqlite3.Connection, jobs: Iterable[Job]) -> list[Job]:
    result = []
    for job in jobs:
        if not db.execute("SELECT 1 FROM seen_jobs WHERE job_key = ?", (job.key,)).fetchone():
            result.append(job)
    return result


def mark_seen(db: sqlite3.Connection, jobs: Iterable[Job]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.executemany("INSERT OR IGNORE INTO seen_jobs VALUES (?, ?, ?)",
                   [(job.key, now, json.dumps(asdict(job))) for job in jobs])
    db.commit()


def build_email(jobs: list[Job], errors: list[str], prefix: str) -> tuple[str, str, str]:
    subject = f"{prefix}: {len(jobs)} new Summer 2027 SWE role{'s' if len(jobs) != 1 else ''}"
    text_rows = [f"{j.company} — {j.title} ({j.location or 'Location not listed'})\n{j.url}" for j in jobs]
    text = subject + "\n\n" + "\n\n".join(text_rows)
    rows = "".join(
        f"<tr><td>{html.escape(j.company)}</td><td><a href=\"{html.escape(j.url, quote=True)}\">{html.escape(j.title)}</a></td>"
        f"<td>{html.escape(j.location or 'Not listed')}</td><td>{html.escape(j.source)}</td></tr>" for j in jobs
    )
    error_html = "" if not errors else "<p><strong>Source warnings:</strong> " + html.escape("; ".join(errors)) + "</p>"
    body = f"<html><body><h2>{html.escape(subject)}</h2>{error_html}<table border='1' cellpadding='7' cellspacing='0'>" \
           f"<tr><th>Company</th><th>Role</th><th>Location</th><th>Source</th></tr>{rows}</table></body></html>"
    return subject, text, body


def send_email(subject: str, text: str, body: str) -> None:
    required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing email environment variables: " + ", ".join(missing))
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, os.environ["EMAIL_FROM"], os.environ["EMAIL_TO"]
    message.set_content(text)
    message.add_alternative(body, subtype="html")
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists() and config_path == "config.yaml":
        fallback_path = Path("config.example.yaml")
        if fallback_path.exists():
            path = fallback_path
    config = yaml.safe_load(path.read_text())
    return config if isinstance(config, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Find new Summer 2027 SWE internships and email them.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--database", default="jobs.db")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without email or database writes")
    parser.add_argument("--send-test", action="store_true", help="Send a test email and exit")
    args = parser.parse_args()
    config = load_config(args.config)
    prefix = config.get("email", {}).get("subject_prefix", "Internship Bot")
    if args.send_test:
        subject, text, body = build_email([Job("Example", "Software Engineering Intern - Summer 2027", "Toronto, Canada", "https://example.com", "Test")], [], prefix)
        send_email(subject, text, body)
        print("Test email sent.")
        return 0
    jobs, errors = collect(config)
    if args.dry_run:
        print(json.dumps({"jobs": [asdict(j) for j in jobs], "errors": errors}, indent=2))
        return 0
    db = init_db(Path(args.database))
    new_jobs = unseen(db, jobs)[: int(config.get("email", {}).get("max_jobs_per_email", 100))]
    if new_jobs or config.get("email", {}).get("send_empty_digest", False):
        required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            print("Skipping email due to missing environment variables: " + ", ".join(missing), file=sys.stderr)
        else:
            send_email(*build_email(new_jobs, errors, prefix))
        mark_seen(db, new_jobs)
        if missing:
            print(f"Skipped email for {len(new_jobs)} new jobs.")
        else:
            print(f"Sent {len(new_jobs)} new jobs.")
    else:
        print(f"No new jobs. Checked {len(jobs)} matching active jobs.")
    if errors:
        print("Warnings:\n" + "\n".join(errors), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

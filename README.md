# Summer 2027 Internship Bot

A lightweight Python bot that checks internship listings, filters for Summer 2027 software roles in Canada/US, remembers jobs it has already seen, and emails only new matches.

## Supported sources

- SpeedyApply's 2027 SWE list for US and international internships
- SimplifyJobs' active public internship list (including early Summer 2027 roles)
- Greenhouse job boards
- Lever job boards
- Ashby job boards
- Configurable server-rendered company career pages

LinkedIn is intentionally not scraped directly. Its markup and anti-bot controls change frequently, and automated scraping can violate its terms. Roles linked from public aggregators and company applicant-tracking systems are still collected.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env
```

Fill in `.env`. For Gmail, enable two-step verification and create an App Password. Do not use your normal Gmail password.

Load the environment variables and test:

```bash
set -a; source .env; set +a
python internship_bot.py --dry-run
python internship_bot.py --send-test
python internship_bot.py
```

The first real run emails all currently matching listings. Later runs email only new ones. By default, a listing must explicitly say `2027`; this avoids accidentally alerting you about undated 2026 roles. Set `require_target_year: false` if you prefer broader results.

## Run automatically with GitHub Actions

1. Create a private GitHub repository and place these files at its root.
2. Copy `config.example.yaml` to `config.yaml` and commit it.
3. In **Settings → Secrets and variables → Actions**, add `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, and `EMAIL_TO`.
4. Enable Actions. The included workflow runs every six hours and can also be triggered manually.

The workflow commits `jobs.db` after successful runs so deduplication survives between executions. A private repository is recommended.

## Add company career boards

Most Greenhouse URLs look like `boards.greenhouse.io/COMPANY`; use `COMPANY` as `board_token`. Most Lever URLs look like `jobs.lever.co/COMPANY`; use `COMPANY` as `site`. Ashby URLs usually look like `jobs.ashbyhq.com/COMPANY`; use the final segment as `board`.

```yaml
sources:
  greenhouse:
    - company: Stripe
      board_token: stripe
  lever:
    - company: Example
      site: example
  ashby:
    - company: Example
      board: Example
```

One broken source does not stop the rest of the run. Source failures appear as warnings.

## Scheduling elsewhere

Run `python internship_bot.py` every few hours using cron, a VPS, Railway, Render, or another scheduler. Mount persistent storage for `jobs.db` if you use Docker.

```cron
17 */6 * * * cd /path/to/internship-bot && . .venv/bin/activate && set -a && . .env && set +a && python internship_bot.py
```

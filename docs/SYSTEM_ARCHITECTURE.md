# System Architecture

## Overview

CoC Report helps clan leaders capture Clash of Clans war data at the right time and turn that data into practical summaries.

The system has two jobs:

- collect reliable war snapshots, especially final results after war end
- generate leader-friendly messages and reports from either live war state or saved history

It is intentionally script-based and file-based. That keeps the project easy to run locally, easy to move to a VPS later, and simple to extend without adding infrastructure too early.

## Core Components

`fetch_war.py` is the shared API layer and manual fetch command. It loads `COC_API_TOKEN`, fetches `currentwar`, prints attack participation, and saves manual snapshots to `data/wars/`.

`schedule_war_snapshot.py` is the long-running scheduler. It reuses `fetch_war.py`, watches the current war state, waits until `endTime` plus a buffer, and saves final snapshots to `data/war_results/`.

`war_warning_message.py` fetches the live current war and prints a copy/paste reminder for members with attacks remaining. It does not store data or send notifications.

`weekly_report.py` reads saved final snapshots from `data/war_results/` and builds weekly and all-time historical reports. These reports include full roster tables with member attacks, stars, and average attack destruction. It does not call the Clash API for saved-snapshot reporting.

`build_site.py` generates static HTML under `site_output/` for Cloudflare Pages. By default it writes the weekly report and total history pages. With `--include-current-war`, it also fetches the live current war and writes a static current-war dashboard.

`deploy.sh` is the one-time local deploy command. It rebuilds the full static site with current-war data, stages only `site_output/`, commits if generated output changed, and pushes normally to GitHub.

`auto_deploy_loop.sh` is the long-running PC refresh loop. It repeatedly runs the same deploy flow, asks `war_poll_interval.py` how long to sleep based on current war state and time left, and keeps running until stopped.

## Data Flow

```text
Clash API
   |
   v
fetch_war.py
   |
   +--> data/wars/                  manual snapshots
   |
   v
schedule_war_snapshot.py
   |
   +--> data/state/saved_wars.json  dedupe state
   |
   v
data/war_results/                  final war snapshots
   |
   v
weekly_report.py
   |
   v
build_site.py / weekly_report.py --site
   |
   v
site_output/index.html
   |
   v
GitHub -> Cloudflare Pages

data/war_results/
   |
   v
build_site.py
   |
   v
site_output/history.html
   |
   v
GitHub -> Cloudflare Pages

Clash API
   |
   v
war_warning_message.py             live copy/paste reminder

Clash API
   |
   v
build_site.py --include-current-war
   |
   v
site_output/current-war.html
   |
   v
GitHub -> Cloudflare Pages

PC auto loop
   |
   v
build_site.py --include-current-war
   |
   v
site_output/
   |
   v
git push
   |
   v
Cloudflare Pages
```

## How Components Interact

`fetch_war.py` owns Clash API authentication and request logic. Other live-data scripts import `fetch_current_war()` instead of duplicating request code.

The scheduler imports both `fetch_current_war()` and `save_war_snapshot()`. It adds scheduling, dedupe, and final-result timing around those reusable helpers.

The weekly report and total history page do not depend on live API access. Their input is the durable JSON output from the scheduler.

The static site generator wraps the same weekly report logic in self-contained HTML. The generated `site_output/index.html` is committed to GitHub so Cloudflare Pages can deploy it without running Python.

The history page uses the same saved final snapshots but includes all deduped wars instead of the weekly report window. It derives all-time war totals, member accountability metrics, and full roster performance, then writes `site_output/history.html`.

When `build_site.py --include-current-war` is used, `build_site.py` calls `fetch_current_war()` once at build time. If the API call succeeds, `site_output/current-war.html` contains the current state, timing, attack usage, remaining attacks, and a copy/paste warning message. If the token is missing or the API fails, the page is still generated with an unavailable-data message.

The auto deploy loop is intended to run on the PC where `COC_API_TOKEN` is available. It never force-pushes and only stages `site_output/`, so runtime data directories remain outside deploy commits.

Displayed site timestamps are formatted in Central Time using `ZoneInfo("America/Chicago")` when available, with a UTC fallback if Python cannot load zoneinfo.

## Design Principles

- Keep collection and analysis separate.
- Prefer local JSON until the project needs database-level querying.
- Preserve one-command scripts so each workflow can be run independently.
- Make long-running behavior safe: log clearly, retry after temporary failures, and dedupe final saves.
- Keep leader-facing output clean and copy/paste-ready.

## Key Decisions

The final snapshot is taken after `endTime` plus a buffer because the API may need a short settlement window before final stars and destruction are stable.

Manual snapshots and final snapshots are stored separately. `data/wars/` is useful for ad hoc inspection; `data/war_results/` is the historical source for reports.

Warnings are generated as text instead of sent automatically because Clash chat tagging and notification behavior cannot be safely automated through this project.

Static publishing is generated locally instead of built on Cloudflare. This keeps Cloudflare Pages setup simple: it only serves the committed `site_output/` folder.

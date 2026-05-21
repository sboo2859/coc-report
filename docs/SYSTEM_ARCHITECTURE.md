# System Architecture

## Overview

CoC Report helps clan leaders capture Clash of Clans war data at the right time and turn that data into practical summaries.

The system has two jobs:

- collect reliable war snapshots, especially final results after war end
- generate leader-friendly messages and reports from either live war state or saved history

It is intentionally script-based and file-based. That keeps the project easy to run locally, easy to move to a VPS later, and simple to extend without adding infrastructure too early.

## Production Droplet

The current DigitalOcean Droplet architecture is:

```text
clashcommand.service
  -> Discord bot

coc-war-snapshot.service
  -> schedule_war_snapshot.py
  -> data/war_results/final_war_*.json

coc-cwl-snapshot.service
  -> schedule_cwl_snapshot.py
  -> data/cwl_war_results/cwl_war_*.json

coc-report-updater.timer
  -> coc-report-updater.service every 15 minutes
  -> update_coc_report.sh
  -> site_output/
  -> git push

Cloudflare Pages
  -> committed site_output/
```

The duplicate `coc-report-deploy.timer` path was disabled on the Droplet. Keep only one report updater timer enabled.

## Core Components

`fetch_war.py` is the shared API layer and manual fetch command. It supports `COC_API_TOKEN`/`COC_CLAN_TAG` and `CLASH_API_TOKEN`/`CLAN_TAG`, fetches `currentwar`, prints attack participation, saves manual snapshots to `data/wars/`, and writes the latest current-war snapshot to `data/current_war/latest_current_war.json`.

`schedule_war_snapshot.py` is the long-running scheduler. It reuses `fetch_war.py`, watches the current war state, waits until `endTime` plus a buffer, and saves final snapshots to `data/war_results/`.

`schedule_cwl_snapshot.py` is the separate long-running CWL scheduler. It fetches the current CWL league group, iterates round war tags, fetches each CWL war by tag, and saves ended CWL wars to `data/cwl_war_results/`.

`clashcommand/bot.py` registers Discord slash commands. Regular war commands use `/war` and `/missed`. CWL read-only commands use `/cwl`, `/cwl-war`, and `/cwl-missed`, fetching CWL data live from the Clash API without changing regular war reminder behavior.

`war_warning_message.py` fetches the live current war and prints a copy/paste reminder for members with attacks remaining. It does not store data or send notifications.

`weekly_report.py` reads saved final snapshots from `data/war_results/` and builds weekly and all-time historical reports. These reports include full roster tables with member attacks, stars, and average attack destruction. It does not call the Clash API for saved-snapshot reporting.

`fetch_current_war_snapshot.py` refreshes `data/current_war/latest_current_war.json` from the Clash API for current-war site generation.

`build_site.py` generates static HTML under `site_output/` for Cloudflare Pages. By default it writes the weekly report and total history pages. With `--include-current-war`, it writes a static current-war dashboard from `data/current_war/latest_current_war.json`. With `--live-current-war-fallback`, it can call the live API if that snapshot is unavailable.

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
   +--> data/current_war/            latest current-war snapshot
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

Clash API CWL league group
   |
   v
schedule_cwl_snapshot.py
   |
   +--> data/state/saved_cwl_wars.json
   |
   v
data/cwl_war_results/              CWL final war snapshots

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

data/current_war/latest_current_war.json
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

The CWL scheduler uses the same API token/clan tag environment behavior but calls CWL endpoints separately. It does not feed `weekly_report.py` or `build_site.py` yet.

The weekly report and total history page do not depend on live API access. Their input is the durable JSON output from the scheduler.

The static site generator wraps the same weekly report logic in self-contained HTML. The generated `site_output/index.html` is committed to GitHub so Cloudflare Pages can deploy it without running Python.

The history page uses the same saved final snapshots but includes all deduped wars instead of the weekly report window. It derives all-time war totals, member accountability metrics, and full roster performance, then writes `site_output/history.html`.

When `build_site.py --include-current-war` is used, `build_site.py` first loads `data/current_war/latest_current_war.json`. If `--live-current-war-fallback` is passed and the snapshot is unavailable, it calls `fetch_current_war()` once at build time. If current-war data is available, `site_output/current-war.html` contains the current state, timing, attack usage, remaining attacks, and a copy/paste warning message. If current-war data is unavailable, the page is still generated with an unavailable-data message.

The production Droplet updater is `coc-report-updater.timer`, which runs `update_coc_report.sh` every 15 minutes. That script must call `build_site.py --include-current-war --live-current-war-fallback` so the current-war page can use the Droplet's allowlisted IP when `data/current_war/latest_current_war.json` is missing. Local deploy scripts are still available for manual operation. Deploy automation should only stage `site_output/`, so runtime data directories remain outside deploy commits.

CWL reminders are intentionally not automated yet. Future CWL reminders should use separate reminder keys from regular wars and avoid duplicate reminder spam.

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

CWL snapshots are stored separately in `data/cwl_war_results/` with `_cwl` metadata. They should not be merged into weekly or overall reports until the desired reporting semantics are decided.

Warnings are generated as text instead of sent automatically because Clash chat tagging and notification behavior cannot be safely automated through this project.

Static publishing is generated locally instead of built on Cloudflare. This keeps Cloudflare Pages setup simple: it only serves the committed `site_output/` folder.

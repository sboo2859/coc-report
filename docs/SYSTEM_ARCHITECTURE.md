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

`schedule_war_snapshot.py` is the long-running scheduler. It reuses `fetch_war.py`, watches the current war state, persists scheduled war identity before sleeping to war end, waits until `endTime` plus a buffer, and saves final snapshots to `data/war_results/`. Once a scheduled final capture exists, the scheduled war key is authoritative; live data is accepted only when it matches that key. To avoid needless API polling, during `preparation` it sleeps until battle-day `startTime` (capped at `WAR_PREP_MAX_SLEEP_MINUTES`) rather than polling every prep interval, and it backs off to the idle interval once a `warEnded` snapshot is already saved.

`schedule_cwl_snapshot.py` is the separate long-running CWL scheduler. It fetches the current CWL league group, iterates round war tags, fetches each CWL war by tag, and saves ended CWL wars to `data/cwl_war_results/`. It polls every `CWL_POLL_MINUTES` while rounds are active and backs off to `CWL_IDLE_POLL_MINUTES` when the league group is `notInWar` or `ended`, since CWL runs only about one week per month.

`clashcommand/bot.py` registers Discord slash commands. Regular war commands use `/war` and `/missed`. CWL commands use `/cwl`, `/cwl-war`, and `/cwl-missed`, fetching CWL data live from the Clash API. CWL recap posting and reminders run in separate schedulers (`cwl_post_war_reports.py`, `cwl_reminders.py`) and do not change regular war reminder behavior.

`clashcommand/post_war_reports.py` watches `data/war_results/final_war_*.json` from inside the Discord bot process and posts regular-war recaps to the configured reminder channel. It ranks top performers and the MVP through `clashcommand/performance.py`. It marks existing snapshots as seen on startup so deploying the bot does not spam old wars. For new snapshots, it only marks a war seen after at least one Discord recap send succeeds or SQLite already has a `post_war_report` event.

`clashcommand/performance.py` holds the shared war-performance scoring used by both the regular and CWL recaps to rank top performers and pick the MVP. It ranks a clan's attackers by, in order, total stars, target difficulty (each attack's stars weighted by how strong the base attacked was, derived from the opponent's `mapPosition`), average destruction, and finally a war-seeded deterministic tiebreak (`sha256` of the war key plus the player tag). The seeded tiebreak replaced an alphabetical one so genuine ties do not always resolve to the same (alphabetically first) player, while any single war's recap stays reproducible.

`clashcommand/clash/cwl.py` holds the pure CWL data helpers (side resolution, war-tag filtering, attack summaries, participation check, and the stable CWL war key). It is shared by `bot.py` and both CWL schedulers so the schedulers do not import the Discord bot module. Because a clan can appear on either the `clan` or `opponent` side of a CWL war, these helpers always resolve the clan by tag on both sides, and they default to one attack per member because the Clash API omits `attacksPerMember` for CWL.

`clashcommand/cwl_post_war_reports.py` watches `data/cwl_war_results/cwl_war_*.json` and posts CWL round recaps. It mirrors the regular post-war reporter (startup seen-marking, retry-safe seen-marking, five-minute scan) with two CWL differences: it dedupes on the CWL war tag with reminder type `cwl_post_war_report`, and it posts a snapshot to a guild only when that guild's clan participates in the war, because CWL snapshots include every ended war in the league group, not just this clan's.

`clashcommand/cwl_reminders.py` runs Discord reminder checks for the active CWL round. It fetches the league group live, selects the clan's single `inWar` round war, and reuses the regular reminder decision logic (3-hour and 1-hour windows). CWL reminder events are namespaced (`cwl_3h`, `cwl_1h`) so they never collide with regular reminders, and linked players are mentioned by player tag.

`war_warning_message.py` fetches the live current war and prints a copy/paste reminder for members with attacks remaining. It does not store data or send notifications.

`clashcommand/reminders.py` runs Discord war reminder checks. It uses the configured reminder channel, records sent reminders in SQLite, and logs explicit skip reasons such as no configured channel, war not `inWar`, no parseable end time, no stable war key, or already-sent dedupe.

`weekly_report.py` reads saved final snapshots from `data/war_results/` and builds weekly and all-time historical reports. These reports include full roster tables with member attacks, stars, and average attack destruction. It does not call the Clash API for saved-snapshot reporting.

`fetch_current_war_snapshot.py` refreshes `data/current_war/latest_current_war.json` from the Clash API for current-war site generation.

`build_site.py` generates static HTML under `site_output/` for Cloudflare Pages. It loads the saved war snapshots from `data/war_results/` once per build and derives both the weekly and total-history pages from that in-memory list. By default it writes the weekly report and total history pages. With `--include-current-war`, it writes a static current-war dashboard. With `--live-current-war-fallback`, it fetches the live Clash API current war first, saves `data/current_war/latest_current_war.json`, then renders `site_output/current-war.html`; if the live fetch fails, it falls back to the cached snapshot. The build also writes `site_output/_headers` so Cloudflare Pages sends no-cache headers for `/current-war.html`.

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

data/war_results/final_war_*.json
   |
   v
post_war_reports.py scan loop
   |
   v
Discord recap post

Clash API CWL league group
   |
   v
schedule_cwl_snapshot.py
   |
   +--> data/state/saved_cwl_wars.json
   |
   v
data/cwl_war_results/              CWL final war snapshots
   |
   v
cwl_post_war_reports.py scan loop  (participation filter, war-tag dedupe)
   |
   v
Discord CWL round recap post

Clash API CWL league group
   |
   v
cwl_reminders.py                   active inWar round, 3h/1h windows
   |
   v
Discord CWL reminder post

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

The scheduler imports both `fetch_current_war()` and `save_war_snapshot()`. It adds scheduling, dedupe, persisted scheduled identity, and final-result timing around those reusable helpers.

The CWL scheduler uses the same API token/clan tag environment behavior but calls CWL endpoints separately. It does not feed `weekly_report.py` or `build_site.py` yet.

The weekly report and total history page do not depend on live API access. Their input is the durable JSON output from the scheduler.

The static site generator wraps the same weekly report logic in self-contained HTML. The generated `site_output/index.html` is committed to GitHub so Cloudflare Pages can deploy it without running Python.

The history page uses the same saved final snapshots but includes all deduped wars instead of the weekly report window. It derives all-time war totals, member accountability metrics, and full roster performance, then writes `site_output/history.html`.

When `build_site.py --include-current-war --live-current-war-fallback` is used, `build_site.py` first calls `fetch_current_war()` from the Droplet's allowlisted IP, saves the result to `data/current_war/latest_current_war.json`, then renders `site_output/current-war.html`. If the live fetch fails, it loads `data/current_war/latest_current_war.json` as a fallback. If no current-war data is available, the page is still generated with an unavailable-data message.

The production Droplet updater is `coc-report-updater.timer`, which runs `update_coc_report.sh` every 15 minutes. The script fetches `origin/main`, fast-forwards only when the branch is clean and behind, refuses dirty/divergent/locally-ahead states, builds with `--include-current-war --live-current-war-fallback`, stages `site_output/`, commits if generated output changed, and pushes normally. Local deploy scripts are still available for manual operation. Deploy automation should only stage `site_output/`, so runtime data directories remain outside deploy commits.

`coc-war-snapshot.service` writes completed regular-war JSON files. The bot's post-war reporter scans those files every five minutes, posts only newly detected wars after bot startup, and records sent recaps in SQLite using `reminder_events` with reminder type `post_war_report`. It uses the configured reminder channel, with a remembered command channel as an in-process fallback. New wars remain retryable when no recap channel exists, channel resolution fails, or Discord send fails.

CWL recaps and reminders are automated in the bot process alongside the regular-war schedulers. `coc-cwl-snapshot.service` writes completed CWL round JSON files, which the CWL post-war reporter scans every five minutes; it filters to wars the guild's clan participated in and dedupes on the CWL war tag with reminder type `cwl_post_war_report`. The CWL reminder scheduler polls the live league group for the active `inWar` round and reuses the regular 3-hour/1-hour decision logic, with reminder keys namespaced (`cwl_3h`, `cwl_1h`) to avoid colliding with regular-war reminders.

Displayed site timestamps are formatted in Central Time using `ZoneInfo("America/Chicago")` when available, with a UTC fallback if Python cannot load zoneinfo.

## Design Principles

- Keep collection and analysis separate.
- Prefer local JSON until the project needs database-level querying.
- Preserve one-command scripts so each workflow can be run independently.
- Make long-running behavior safe: log clearly, retry after temporary failures, and dedupe final saves.
- Keep leader-facing output clean and copy/paste-ready.
- Poll the Clash API only as often as the next relevant event requires; back off during idle, preparation, and off-season windows.

## Scope

ClashCommand is intentionally a **single-clan tool** for one clan's Discord server. It supports one clan per guild and is not built out for multi-tenant/multi-server distribution or paid tiers. This keeps the design simple (SQLite, a single Droplet, per-guild settings) and matches the decision not to pursue commercialization — which Supercell's Fan Content Policy would also restrict. The `guild_settings` table is keyed per guild, so the code is not hostile to multiple servers, but multi-server operation, isolation hardening, and monetization are explicitly out of scope.

## Key Decisions

The final snapshot is taken after `endTime` plus a buffer because the API may need a short settlement window before final stars and destruction are stable.

Final snapshot identity rules:

- During `inWar`, the scheduler writes `data/state/scheduled_war.json` before sleeping to the final capture time.
- When a scheduled final capture is due, `scheduled_war.json` is authoritative.
- A live payload is saved only if its stable war key matches the scheduled key.
- A live `notInWar` payload or identity-less payload uses the persisted scheduled payload.
- A live next-war `preparation` or `inWar` payload is rejected for the prior scheduled war.
- Duplicate protection still uses `data/state/saved_wars.json`.

Manual snapshots and final snapshots are stored separately. `data/wars/` is useful for ad hoc inspection; `data/war_results/` is the historical source for reports.

CWL snapshots are stored separately in `data/cwl_war_results/` with `_cwl` metadata. They should not be merged into weekly or overall reports until the desired reporting semantics are decided.

Warnings are generated as text instead of sent automatically because Clash chat tagging and notification behavior cannot be safely automated through this project.

Static publishing is generated locally instead of built on Cloudflare. This keeps Cloudflare Pages setup simple: it only serves the committed `site_output/` folder.

Cloudflare Pages reads `site_output/_headers`. The generated headers currently disable caching only for `/current-war.html` with `Cache-Control: no-cache, no-store, must-revalidate`; weekly and history pages keep normal static caching.

## Runtime State Classification

Canonical operational state:

```text
data/war_results/final_war_*.json
data/cwl_war_results/cwl_war_*.json
data/clashcommand.sqlite3, or CLASHCOMMAND_DB_PATH
```

Critical temporary state:

```text
data/state/scheduled_war.json
```

Cache and regenerable state:

```text
data/current_war/latest_current_war.json
data/state/saved_wars.json
data/state/saved_cwl_wars.json
data/wars/
```

The SQLite DB stores linked Discord players, guild reminder channels, guild clan tags, reminder sends, and post-war recap dedupe events. The bot's `LinkedPlayerStore` keeps a single WAL-mode connection (opened with `check_same_thread=False` and serialized by a lock) rather than reconnecting per call, since all DB access runs through `asyncio.to_thread`. WAL mode leaves `clashcommand.sqlite3-wal`/`-shm` sidecar files, which a backup must checkpoint or copy alongside the DB. The next operational improvement is a runtime-state backup script plus systemd timer that exports canonical state and the SQLite DB to an off-Droplet target, preferably Cloudflare R2.

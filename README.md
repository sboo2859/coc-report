# CoC Report

Utilities for fetching Clash of Clans current war snapshots.

Production currently runs on the DigitalOcean Droplet `ClashCommand`:

```text
clashcommand.service       -> Discord bot
coc-war-snapshot.service  -> final war snapshots in data/war_results/
coc-cwl-snapshot.service  -> CWL final snapshots in data/cwl_war_results/
coc-report-updater.timer  -> rebuild/push site_output/ every 15 minutes
Cloudflare Pages          -> serves committed static files
```

For recovery and operator commands, start with [CoC Report Runbook](docs/RUNBOOK.md).

Current Discord bot commands include regular war commands (`/war`, `/missed`, `/latest-war-recap`) and CWL visibility commands (`/cwl`, `/cwl-war`, `/cwl-missed`). The bot posts automatic regular-war recaps from new `data/war_results/final_war_*.json` snapshots and automatic CWL round recaps from new `data/cwl_war_results/cwl_war_*.json` snapshots. It also sends automatic pre-end reminders for both regular wars and active CWL rounds (3-hour and 1-hour windows).

## Discord Bot (ClashCommand)

The **ClashCommand** Discord bot is built and running in production alongside the static report flow. It handles war accountability, automatic recaps, and smart reminders for regular wars and CWL.

Scope decision: ClashCommand is intentionally a **single-clan tool** for this clan's own Discord server. Multi-server/multi-tenant distribution and paid features were evaluated and are **not being pursued** (charging fees is also prohibited by Supercell's Fan Content Policy). The planning docs below are retained as historical reference; their later multi-server and monetization phases are out of scope.

Reference docs:

- [Discord Bot Audit](docs/DISCORD_BOT_AUDIT.md)
- [Discord Bot MVP Plan](docs/DISCORD_BOT_MVP_PLAN.md)
- [ClashCommand Droplet Deployment](docs/CLASHCOMMAND_DROPLET_DEPLOY.md)

## System Documentation

- [System Architecture](docs/SYSTEM_ARCHITECTURE.md)
- [Pipeline Flow](docs/PIPELINE_FLOW.md)
- [Data Contracts](docs/DATA_CONTRACTS.md)
- [Operation Guide](docs/OPERATION.md)
- [Runbook](docs/RUNBOOK.md)
- [Architecture Decisions](docs/DECISIONS.md)

## Configuration

Set your Clash of Clans API token before running either script:

```bash
export COC_API_TOKEN="your Clash API token"
```

The default clan tag is configured in `fetch_war.py`. You can override it without editing code:

```bash
export COC_CLAN_TAG="#22YY2LPV2"
```

The Droplet `.env` may instead use the Discord-bot names:

```bash
export CLASH_API_TOKEN="your Clash API token"
export CLAN_TAG="#22YY2LPV2"
```

`fetch_war.py` supports both name pairs. In an interactive Droplet shell, this bridge is safe:

```bash
set -a
source .env
set +a
export COC_API_TOKEN="$CLASH_API_TOKEN"
export COC_CLAN_TAG="$CLAN_TAG"
```

## Manual One-Time Fetch

Run a one-time current war fetch:

```bash
python3 fetch_war.py
```

Snapshots are saved under `data/wars/`.

## Automated War-End Scheduler

Run the scheduler:

```bash
python3 schedule_war_snapshot.py
```

The scheduler watches the `currentwar` response, persists the scheduled war identity before sleeping, waits until `endTime` plus a settlement buffer, then saves the final war snapshot. Once a scheduled final capture exists, that scheduled identity is authoritative: a live `warEnded` payload is used only when its stable war key matches, `notInWar` falls back to the persisted scheduled payload, and next-war `preparation`/`inWar` payloads are rejected for the prior war. It also saves immediately when the current war is already in `warEnded` state.

Final war snapshots are saved under `data/war_results/`.

Saved war keys are tracked in `data/state/saved_wars.json` so the same ended war is not saved repeatedly.

## War Warning Message

Generate a copy/paste warning for members who still have attacks remaining:

```bash
python3 war_warning_message.py
```

Optional count controls:

```bash
python3 war_warning_message.py --counts
python3 war_warning_message.py --no-counts
```

Example output:

```text
⚠️ War reminder — about 3 hours left.

Still need attacks from:
PlayerOne — 2 attacks left
PlayerTwo — 1 attack left

Please use your attacks before war ends.
```

This does not automatically tag members in Clash of Clans chat. Leaders should manually tag/select members in-game if they want true notification behavior.

## Weekly Report

Generate a weekly summary from saved war results:

```bash
python3 weekly_report.py
```

Optional period control:

```bash
python3 weekly_report.py --days 14
```

Example output:

```text
📊 Weekly War Report

Period: Last 7 days
Wars: 5
Record: 3W - 2L

Total Attacks: 180
Unused Attacks: 12
Attack Usage: 93%

Total Stars: 132
Average Stars per War: 26.4

Top Performers:
1. PlayerOne — 12⭐
2. PlayerTwo — 11⭐
3. PlayerThree — 10⭐

Missed Attacks:
PlayerFour — missed 4 attacks
PlayerFive — missed 3 attacks

Notes:
- Strong attack usage overall
- A few repeat missed attacks to address
```

The report reads local JSON files only. It does not call the Clash API.

## Static Report Site

Generate the static report site:

```bash
python3 build_site.py
```

This writes:

```text
site_output/index.html
site_output/history.html
```

Optionally include a static current-war page:

```bash
python3 build_site.py --include-current-war
```

This writes:

```text
site_output/index.html
site_output/current-war.html
site_output/history.html
```

The weekly report uses the selected report window from saved final snapshots in `data/war_results/` and does not require API access. Total History uses all saved final snapshots in `data/war_results/`.

For production freshness, build with live current-war refresh enabled:

```bash
python3 build_site.py --include-current-war --live-current-war-fallback
```

With `--live-current-war-fallback`, `build_site.py` fetches the live Clash API current war first, writes `data/current_war/latest_current_war.json`, then renders `site_output/current-war.html`. If live fetch fails, it falls back to the latest saved current-war snapshot when present.

If current-war data is unavailable, the build still writes `current-war.html` with an unavailable-data message.

The build also writes `site_output/_headers` so Cloudflare Pages sends `Cache-Control: no-cache, no-store, must-revalidate` for `/current-war.html`.

You can also generate only the weekly page directly from the report script:

```bash
python3 weekly_report.py --site
```

Commit and push the generated site:

```bash
git add site_output/index.html site_output/history.html
git commit -m "Update weekly report site"
git push
```

If you generated the current-war page, include it in the same commit:

```bash
git add site_output/current-war.html
```

The site is static. Cloudflare Pages serves the committed files and only updates after you rebuild locally, commit the generated HTML, and push.

### Deploy commands

Run a one-time build, commit, and push of `site_output/`:

```bash
./deploy.sh
```

Run continuous PC-based refresh:

```bash
./auto_deploy_loop.sh
```

Run it detached:

```bash
nohup ./auto_deploy_loop.sh > auto_deploy.log 2>&1 &
```

Watch logs:

```bash
tail -f auto_deploy.log
```

Stop it:

```bash
ps aux | grep auto_deploy_loop
kill <PID>
```

The auto deploy loop should run on the PC where `COC_API_TOKEN` is permanently available. It rebuilds the static site, commits only `site_output/` when generated output changes, and lets Cloudflare Pages redeploy from the GitHub push.

On the production Droplet, the active updater is `coc-report-updater.timer`, which runs `update_coc_report.sh` every 15 minutes. The updater fetches `origin/main`, fast-forwards only when the local branch is clean and behind, refuses dirty/divergent/locally-ahead states, builds with `--include-current-war --live-current-war-fallback`, commits `site_output/` when changed, and pushes normally. The older local loop is still useful for manual/local operation but is not the current production service.

### Cloudflare Pages setup

Use:

- Framework preset: None
- Build command: leave blank
- Build output directory: `site_output`
- Production branch: `main`

Cloudflare Pages will redeploy when GitHub receives a new push.

`site_output/_headers` is committed with the generated site. It only disables caching for `/current-war.html`; `index.html` and `history.html` keep normal static-page caching.

## Runtime State

Runtime data is intentionally ignored by Git so the 15-minute updater does not commit noisy state.

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

Cache/runtime convenience state:

```text
data/current_war/latest_current_war.json
data/state/saved_wars.json
data/state/saved_cwl_wars.json
data/wars/
```

The SQLite DB stores linked players, guild reminder channel/clan settings, and reminder/post-war recap dedupe events. It runs in WAL mode, so `data/clashcommand.sqlite3-wal` and `-shm` sidecar files may exist alongside it; a backup should checkpoint the DB (or use `sqlite3 .backup`) or copy all three files together. The next operational TODO is to add a runtime-state backup script plus systemd timer that exports canonical state and the SQLite DB to an off-Droplet target, preferably Cloudflare R2.

## Scheduler Settings

These environment variables are optional:

```bash
export WAR_END_BUFFER_MINUTES=2
export WAR_PREP_POLL_MINUTES=30
export WAR_PREP_MAX_SLEEP_MINUTES=360
export WAR_IDLE_POLL_MINUTES=60
export WAR_ENDED_POLL_MINUTES=30
export WAR_RESULTS_DIR=data/war_results
export CWL_POLL_MINUTES=30
export CWL_IDLE_POLL_MINUTES=360
export WAR_WARNING_TARGET_HOURS=3
export WAR_WARNING_INCLUDE_COUNTS=true
export REPORT_DAYS=7
```

Defaults are shown above.

The war scheduler avoids needless Clash API polling: during `preparation` it sleeps until battle-day `startTime` (capped at `WAR_PREP_MAX_SLEEP_MINUTES`, falling back to `WAR_PREP_POLL_MINUTES` when `startTime` is unavailable), and after a final `warEnded` snapshot is saved it backs off to `WAR_IDLE_POLL_MINUTES`. The CWL scheduler polls every `CWL_POLL_MINUTES` while rounds are active and every `CWL_IDLE_POLL_MINUTES` when the league group is `notInWar` or `ended`.

## Run Detached

Start the scheduler in the background:

```bash
nohup python3 schedule_war_snapshot.py > war_scheduler.log 2>&1 &
```

Watch logs:

```bash
tail -f war_scheduler.log
```

Stop the scheduler:

```bash
ps aux | grep schedule_war_snapshot
kill <PID>
```

This same scheduler can later move unchanged to a VPS with a static IP address added to the Clash API allowlist.

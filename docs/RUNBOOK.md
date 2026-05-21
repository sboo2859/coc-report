# CoC Report Runbook

## What This Project Does

- The Discord bot runs on the DigitalOcean Droplet as `clashcommand.service`.
- The Droplet has Clash API access because its IP is allowlisted.
- The final war watcher saves completed war snapshots into `data/war_results/`.
- The website updater rebuilds `site_output/` and pushes generated HTML to GitHub.
- Cloudflare Pages serves the committed static output from GitHub.

## Current Production Architecture

```text
clashcommand.service
  -> Discord bot

coc-war-snapshot.service
  -> runs schedule_war_snapshot.py continuously
  -> watches active wars through the Clash API
  -> saves completed final_war_*.json to data/war_results/

coc-cwl-snapshot.service
  -> runs schedule_cwl_snapshot.py continuously
  -> watches CWL league group war tags
  -> saves completed cwl_war_*.json to data/cwl_war_results/

coc-report-updater.timer
  -> runs coc-report-updater.service every 15 minutes
  -> executes update_coc_report.sh
  -> rebuilds site_output/
  -> commits/pushes site_output if changed

Cloudflare Pages
  -> serves committed static files from GitHub
```

The duplicate `coc-report-deploy.timer` automation was disabled on the Droplet. Do not run both updater timers at the same time.

## DigitalOcean Access

1. Log in to DigitalOcean.
2. Open the Droplet named `ClashCommand`.
3. Use the Droplet console if SSH details are forgotten.
4. Go to the repo:

```bash
cd /opt/clashcommand/app
```

## Quick Health Check

```bash
cd /opt/clashcommand/app

systemctl status clashcommand
systemctl status coc-war-snapshot
systemctl status coc-cwl-snapshot
systemctl status coc-report-updater
systemctl list-timers | grep coc

journalctl -u clashcommand -n 100 --no-pager
journalctl -u coc-war-snapshot -n 100 --no-pager
journalctl -u coc-cwl-snapshot -n 100 --no-pager
journalctl -u coc-report-updater -n 100 --no-pager

ls -l data/war_results
ls -l data/cwl_war_results
find data -maxdepth 3 -type f | sort | tail -50

python3 build_site.py --include-current-war
git status --short
```

Expected:

- `clashcommand.service` is active for the Discord bot.
- `coc-war-snapshot.service` is active or sleeping until the next war check/final snapshot.
- `coc-cwl-snapshot.service` is active or sleeping until the next CWL poll.
- `coc-report-updater.timer` is enabled and scheduled.
- `coc-report-deploy.timer` should not be enabled if `coc-report-updater.timer` is active.
- `data/war_results/` should gain `final_war_*.json` files after wars end.
- `data/cwl_war_results/` should gain `cwl_war_*.json` files after CWL wars end.

## Environment Variables

The Droplet `.env` contains the real tokens. Do not commit it, paste it into chat, or copy token values into docs.

Interactive shells do not automatically load `.env`. Before manual API work, run:

```bash
set -a
source .env
set +a
export COC_API_TOKEN="$CLASH_API_TOKEN"
export COC_CLAN_TAG="$CLAN_TAG"
```

Why the bridge matters:

- The Discord bot uses `CLASH_API_TOKEN` and `CLAN_TAG`.
- Older scripts may look for `COC_API_TOKEN` and `COC_CLAN_TAG`.
- `fetch_war.py` in this repo supports both name pairs, but the bridge is still safe for manual recovery.

## Data Flow

Current war snapshots:

```text
Clash API
  -> fetch_war.py / fetch_current_war_snapshot.py
  -> data/current_war/latest_current_war.json
  -> build_site.py --include-current-war
  -> site_output/current-war.html
```

Final war snapshots:

```text
Clash API
  -> schedule_war_snapshot.py
  -> data/war_results/final_war_*.json
  -> weekly_report.py / build_site.py
  -> site_output/index.html and site_output/history.html
```

CWL war snapshots:

```text
Clash API
  -> /clans/{clanTag}/currentwar/leaguegroup
  -> /clanwarleagues/wars/{warTag}
  -> schedule_cwl_snapshot.py
  -> data/cwl_war_results/cwl_war_*.json
```

Deployment:

```text
update_coc_report.sh
  -> python3 build_site.py --include-current-war --live-current-war-fallback
  -> git add site_output
  -> git commit + push if changed
  -> Cloudflare Pages deploy
```

## How Reports Populate

Weekly and overall reports read completed final snapshots from:

```text
data/war_results/final_war_*.json
```

They do not read `data/wars/`. A file in `data/wars/` is usually a current/manual snapshot. Copying a `data/wars/*.json` file into `data/war_results/final_war_*.json` proved the report pages populate when final snapshots exist, but that is only a diagnostic trick. The normal source should be `coc-war-snapshot.service`.

Verify final snapshots:

```bash
ls -l data/war_results
find data/war_results -maxdepth 1 -type f -name 'final_war_*.json' | sort | tail
```

If `data/war_results/` is empty, weekly and overall pages can still build, but they will show no historical report data.

## How CWL Capture Works

CWL capture is intentionally separate from regular war capture and reporting.

```text
schedule_cwl_snapshot.py
  -> fetch current CWL league group
  -> iterate rounds[].warTags
  -> fetch each /clanwarleagues/wars/{warTag}
  -> save only warEnded wars once
```

Saved CWL snapshots go here:

```text
data/cwl_war_results/cwl_war_*.json
```

Deduplication state lives here:

```text
data/state/saved_cwl_wars.json
```

Weekly and overall report pages do not read CWL files yet. Keep CWL separate until the capture is proven reliable and there is a clear decision about CWL-specific reports or merged totals.

## How Current War Updates

The Droplet is the trusted Clash API fetch environment. Mac or Cloudflare fetches may fail with:

```text
403 accessDenied.invalidIp
```

In this repo, `build_site.py --include-current-war` prefers:

```text
data/current_war/latest_current_war.json
```

Use this command to refresh that snapshot from the Clash API:

```bash
python3 fetch_current_war_snapshot.py
```

Then rebuild:

```bash
python3 build_site.py --include-current-war --live-current-war-fallback
```

`update_coc_report.sh` must use `--live-current-war-fallback` on the Droplet. That lets the updater use `data/current_war/latest_current_war.json` when present, or fetch the live current war from the Clash API when the snapshot is missing. The Droplet IP is allowlisted, so this fallback is expected to work there.

```bash
sed -n '1,120p' update_coc_report.sh
```

## Manual Rebuild And Push

Use this when the updater timer is broken or you need to publish immediately:

```bash
cd /opt/clashcommand/app
set -a
source .env
set +a
export COC_API_TOKEN="$CLASH_API_TOKEN"
export COC_CLAN_TAG="$CLAN_TAG"

python3 fetch_current_war_snapshot.py
python3 build_site.py --include-current-war --live-current-war-fallback
git status --short
git add site_output
git commit -m "Update clan report site"
git push
```

Only stage `site_output/` for normal site deploys. Do not stage `.env`, `data/`, or unrelated files.

If there are no generated changes, `git commit` will report nothing to commit. That is safe.

## Services And Timers

### Discord Bot

```bash
systemctl status clashcommand
systemctl restart clashcommand
journalctl -u clashcommand -n 100 --no-pager
```

Useful command checks in Discord:

```text
/war
/missed
/cwl
/cwl-war
/cwl-missed
```

The CWL commands are read-only. They do not schedule CWL reminders and do not change regular war reminders.

Service file:

```text
/etc/systemd/system/clashcommand.service
```

Working directory:

```text
/opt/clashcommand/app
```

### Final War Snapshot Watcher

```bash
systemctl status coc-war-snapshot
systemctl restart coc-war-snapshot
journalctl -u coc-war-snapshot -n 100 --no-pager
```

Role:

- Runs `schedule_war_snapshot.py` continuously.
- Watches the active war.
- Sleeps until war end plus buffer.
- Saves completed `final_war_*.json` files to `data/war_results/`.

The repo includes a service template:

```text
systemd/coc-war-snapshot.service
```

Install example:

```bash
sudo cp systemd/coc-war-snapshot.service /etc/systemd/system/coc-war-snapshot.service
sudo systemctl daemon-reload
sudo systemctl enable coc-war-snapshot
sudo systemctl start coc-war-snapshot
sudo systemctl status coc-war-snapshot
```

### CWL Snapshot Watcher

```bash
systemctl status coc-cwl-snapshot
systemctl restart coc-cwl-snapshot
journalctl -u coc-cwl-snapshot -n 100 --no-pager
```

Role:

- Runs `schedule_cwl_snapshot.py` continuously.
- Fetches the current CWL league group.
- Fetches each CWL war by war tag.
- Saves completed `cwl_war_*.json` files to `data/cwl_war_results/`.
- Does not update weekly or overall reports.

The repo includes a service template:

```text
systemd/coc-cwl-snapshot.service
```

Install example:

```bash
sudo cp systemd/coc-cwl-snapshot.service /etc/systemd/system/coc-cwl-snapshot.service
sudo systemctl daemon-reload
sudo systemctl enable coc-cwl-snapshot
sudo systemctl start coc-cwl-snapshot
sudo systemctl status coc-cwl-snapshot
```

### Report Updater Timer

```bash
systemctl status coc-report-updater
systemctl list-timers | grep coc
journalctl -u coc-report-updater -n 100 --no-pager
sudo systemctl start coc-report-updater.service
```

Role:

- `coc-report-updater.timer` runs every 15 minutes.
- `coc-report-updater.service` executes `update_coc_report.sh`.
- The script rebuilds `site_output/` with `--live-current-war-fallback`.
- It stages `site_output/`, commits, and pushes only when generated output changed.

Expected script behavior:

```text
update_coc_report.sh
  -> python3 build_site.py --include-current-war --live-current-war-fallback
  -> git add site_output
  -> commit/push if changed
```

Install examples should match the live Droplet unit names:

```bash
sudo cp systemd/coc-report-updater.service /etc/systemd/system/coc-report-updater.service
sudo cp systemd/coc-report-updater.timer /etc/systemd/system/coc-report-updater.timer
sudo systemctl daemon-reload
sudo systemctl enable coc-report-updater.timer
sudo systemctl start coc-report-updater.timer
sudo systemctl start coc-report-updater.service
journalctl -u coc-report-updater -n 100 --no-pager
systemctl list-timers | grep coc
```

Avoid duplicate timers:

```bash
systemctl list-timers | grep coc
systemctl is-enabled coc-report-deploy.timer
sudo systemctl disable --now coc-report-deploy.timer
```

Use `coc-report-updater.timer` as the active production updater unless you intentionally replace it.

## Common Commands

Check repo:

```bash
cd /opt/clashcommand/app
git status
git pull
```

Fetch current war manually:

```bash
set -a
source .env
set +a
export COC_API_TOKEN="$CLASH_API_TOKEN"
export COC_CLAN_TAG="$CLAN_TAG"
python3 fetch_war.py
```

Verify current war page:

```bash
grep -n "Current war data unavailable" site_output/current-war.html
grep -n "vs" site_output/current-war.html | head
```

- If the unavailable text appears, current war did not render.
- If matchup text like `Clan vs Opponent` appears, current war rendered.

## Troubleshooting

### Missing API Token

Error:

```text
Missing COC_API_TOKEN environment variable
```

Fix:

```bash
set -a
source .env
set +a
export COC_API_TOKEN="$CLASH_API_TOKEN"
export COC_CLAN_TAG="$CLAN_TAG"
```

### Invalid IP

Error:

```text
403 accessDenied.invalidIp
```

Meaning:

- The request is coming from a machine not allowlisted in Clash API.
- Run fetches on the DigitalOcean Droplet.

### Weekly Or Overall Reports Are Empty

Check final snapshots and watcher logs:

```bash
ls -l data/war_results
find data/war_results -maxdepth 1 -type f -name 'final_war_*.json' | sort | tail
systemctl status coc-war-snapshot
journalctl -u coc-war-snapshot -n 100 --no-pager
```

If snapshots exist but the site is stale, check the updater:

```bash
systemctl status coc-report-updater
journalctl -u coc-report-updater -n 100 --no-pager
sudo systemctl start coc-report-updater.service
```

### Current War Page Shows Fallback

Run:

```bash
set -a
source .env
set +a
export COC_API_TOKEN="$CLASH_API_TOKEN"
export COC_CLAN_TAG="$CLAN_TAG"

python3 fetch_current_war_snapshot.py
python3 build_site.py --include-current-war --live-current-war-fallback
grep -n "Current war data unavailable" site_output/current-war.html
```

If fetch fails with invalid IP, confirm you are on the Droplet.

### Bot Not Responding

Run:

```bash
systemctl status clashcommand
journalctl -u clashcommand -n 100 --no-pager
systemctl restart clashcommand
```

### Site Is Not Updating

Run:

```bash
systemctl list-timers | grep coc
journalctl -u coc-report-updater -n 100 --no-pager
sudo systemctl start coc-report-updater.service
git status --short
```

If the updater reports no changes, the rebuilt `site_output/` matched the committed output.

## Known Gotchas

- `data/war_results/` was empty before final snapshot automation; weekly and overall pages need this directory populated.
- `data/wars/` and `data/war_results/` are different. Reports use `data/war_results/`.
- Current war data and final war report data are separate paths.
- CWL snapshots are separate from regular war snapshots and are not used by public reports yet.
- The Droplet interactive shell does not automatically load `.env`.
- `CLASH_API_TOKEN`/`CLAN_TAG` and `COC_API_TOKEN`/`COC_CLAN_TAG` naming still needs cleanup.
- `coc-report-deploy.timer` is duplicate automation and was disabled on the Droplet.
- The repo still contains deprecated `systemd/coc-report-deploy.*` templates and `scripts/deploy_report_once.sh`; treat them as inactive unless intentionally migrating away from `coc-report-updater.timer`.
- Do not paste `.env` or API tokens into chat.
- Do not commit `.env`, `data/`, or unrelated runtime files.

## Next Improvements

TODO:

- Standardize token names to `CLASH_API_TOKEN` and `CLAN_TAG` everywhere.
- Remove or archive duplicate/obsolete deploy automation after confirming no one uses it.
- Consider making the updater refresh `data/current_war/latest_current_war.json` before each build if it does not already.
- Add CWL reminders after command behavior is stable:
  - remind 3h before CWL war ends
  - remind 1h before CWL war ends
  - avoid duplicate reminder spam
  - use separate reminder keys from regular wars
- Add clan activity/member snapshots later.

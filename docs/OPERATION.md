# Operation Guide

For Discord bot deployment on DigitalOcean, see [ClashCommand Droplet Deployment](CLASHCOMMAND_DROPLET_DEPLOY.md).

## Manual Commands

Set the API token for live API scripts:

```bash
export COC_API_TOKEN="your Clash API token"
```

Optionally override the clan tag:

```bash
export COC_CLAN_TAG="#22YY2LPV2"
```

Run a manual fetch:

```bash
python3 fetch_war.py
```

Generate an unused-attacks warning message:

```bash
python3 war_warning_message.py
```

Generate a weekly report from saved final snapshots:

```bash
python3 weekly_report.py
```

Generate the static report site:

```bash
python3 build_site.py
```

Generate the static report site and current-war page:

```bash
python3 build_site.py --include-current-war
```

## Scheduler (Background)

Run the scheduler in the foreground:

```bash
python3 schedule_war_snapshot.py
```

Run detached with `nohup`:

```bash
nohup python3 schedule_war_snapshot.py > war_scheduler.log 2>&1 &
```

The scheduler should run continuously until stopped. It saves final snapshots to `data/war_results/` by default.

## Logs

Watch scheduler logs:

```bash
tail -f war_scheduler.log
```

Find a running scheduler:

```bash
ps aux | grep schedule_war_snapshot
```

Stop it:

```bash
kill <PID>
```

## Environment Variables

Live API:

```text
COC_API_TOKEN
COC_CLAN_TAG
```

Scheduler:

```text
WAR_END_BUFFER_MINUTES=2
WAR_PREP_POLL_MINUTES=30
WAR_IDLE_POLL_MINUTES=60
WAR_ENDED_POLL_MINUTES=30
WAR_RESULTS_DIR=data/war_results
```

Warning message:

```text
WAR_WARNING_TARGET_HOURS=3
WAR_WARNING_INCLUDE_COUNTS=true
```

Weekly report:

```text
REPORT_DAYS=7
```

## Static Site Publishing

Build the static site locally:

```bash
python3 build_site.py
```

This writes:

```text
site_output/index.html
site_output/history.html
```

To include current war status, build with API access:

```bash
python3 build_site.py --include-current-war
```

This writes:

```text
site_output/index.html
site_output/current-war.html
site_output/history.html
```

The weekly report page uses the selected report window from saved snapshots and includes a full roster table for that window. The history page uses all saved final snapshots in `data/war_results/` and includes all-time roster accountability. The current-war page requires `COC_API_TOKEN` for the live API call; if the token is missing or the API call fails, the page is still generated with an unavailable-data message.

Commit and push the generated page:

```bash
git add site_output/index.html site_output/history.html
git commit -m "Update weekly report site"
git push
```

If you built with `--include-current-war`, also add `site_output/current-war.html` before committing.

The site is static. Cloudflare Pages updates only after the generated HTML is committed and pushed.

Cloudflare Pages settings:

```text
Framework preset: None
Build command: leave blank
Build output directory: site_output
Production branch: main
```

Cloudflare Pages redeploys when GitHub receives a new push.

## Local Deploy Commands

Run a one-time build, commit, and push of generated site output:

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

The auto deploy loop should run on the PC where `COC_API_TOKEN` is permanently available. It rebuilds with current-war data, stages only `site_output/`, uses normal `git push`, and keeps retrying after failed builds or pushes.

## Troubleshooting

Missing token:

```text
Missing COC_API_TOKEN environment variable.
```

Set `COC_API_TOKEN` before running `fetch_war.py`, `schedule_war_snapshot.py`, or `war_warning_message.py`.

No weekly report data:

```text
No war data available for the selected period.
```

Check that final snapshots exist in `data/war_results/` and that their `startTime` is within the selected report period.

API access errors may happen if the Clash API token is invalid, expired, restricted by IP allowlist, or the clan war log is private. The scheduler logs these errors and retries later.

Malformed JSON in `data/war_results/` is skipped by `weekly_report.py`. Remove or repair the file if it should be counted.

Missing or empty static report:

Run `python3 build_site.py` again and confirm `site_output/index.html` exists. If the page says no war data is available, check that final snapshots exist in `data/war_results/` and are inside the selected report period.

Missing or empty history:

Run `python3 build_site.py` again and confirm `site_output/history.html` exists. If the page says no historical war data is available yet, check that final snapshots exist in `data/war_results/`.

Current war page unavailable:

Run `python3 build_site.py --include-current-war` with `COC_API_TOKEN` set. If `site_output/current-war.html` still says current war data is unavailable, verify the token, IP allowlist, clan tag, and Clash API availability.

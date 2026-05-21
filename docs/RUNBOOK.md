# CoC Report Runbook

## What This Project Does

- The Discord bot runs on the DigitalOcean Droplet.
- The Droplet has Clash API access because its IP is allowlisted.
- The static website is generated into `site_output/`.
- Cloudflare serves the committed static output.

## System Overview

Current working chain:

```text
DigitalOcean Droplet
  -> fetch_war.py / Clash API
  -> build_site.py --include-current-war
  -> site_output/
  -> git commit + push
  -> Cloudflare Pages deploy
```

## DigitalOcean Access

1. Log in to DigitalOcean.
2. Open the Droplet named `ClashCommand`.
3. Use the Droplet console if SSH details are forgotten.
4. Go to the repo:

```bash
cd /opt/clashcommand/app
```

## Common Commands

### Check Repo

```bash
cd /opt/clashcommand/app
git status
git pull
```

### Load Environment Variables

`.env` contains the real token variables. Do not commit it, paste it into chat, or copy token values into docs.

```bash
set -a
source .env
set +a
```

### Fetch Current War

The current Droplet `.env` uses `CLASH_API_TOKEN` and `CLAN_TAG`, while older scripts may expect `COC_API_TOKEN` and `COC_CLAN_TAG`.

Bridge the names before running older scripts:

```bash
export COC_API_TOKEN="$CLASH_API_TOKEN"
export COC_CLAN_TAG="$CLAN_TAG"
python3 fetch_war.py
```

### Rebuild Website

```bash
python3 build_site.py --include-current-war
```

### Verify Current War Page

```bash
grep -n "Current war data unavailable" site_output/current-war.html
grep -n "vs" site_output/current-war.html | head
```

- If the unavailable text appears, current war did not render.
- If matchup text like `Clan vs Opponent` appears, current war rendered.

### Push Site Update

```bash
git status --short
git add site_output
git commit -m "Update current war page"
git push
```

## Bot Service

```bash
systemctl status clashcommand
systemctl restart clashcommand
journalctl -u clashcommand -n 100 --no-pager
```

The service file currently lives at:

```text
/etc/systemd/system/clashcommand.service
```

The service working directory is:

```text
/opt/clashcommand/app
```

## Final War Snapshot Service

Weekly and overall reports read completed war snapshots from:

```text
data/war_results/final_war_*.json
```

The watcher is `schedule_war_snapshot.py`. It runs continuously, polls the Clash current-war API, and saves completed wars into `data/war_results/`.

The repo includes a systemd unit template at:

```text
systemd/coc-war-snapshot.service
```

On the Droplet, install or edit the service file:

```bash
sudo nano /etc/systemd/system/coc-war-snapshot.service
```

Use this service content:

```ini
[Unit]
Description=CoC Final War Snapshot Watcher
After=network.target

[Service]
User=root
WorkingDirectory=/opt/clashcommand/app
EnvironmentFile=/opt/clashcommand/app/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/clashcommand/.venv/bin/python schedule_war_snapshot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable coc-war-snapshot
sudo systemctl start coc-war-snapshot
sudo systemctl status coc-war-snapshot
journalctl -u coc-war-snapshot -n 100 --no-pager
```

Check that completed wars are being saved:

```bash
ls -lh data/war_results/
```

## Report Deploy Timer

Weekly and overall report pages update only after `site_output/` is rebuilt, committed, and pushed. The deploy timer runs a safe one-shot script every 30 minutes.

The script is:

```text
scripts/deploy_report_once.sh
```

It rebuilds the site, stages only `site_output/`, exits cleanly when nothing changed, commits generated site output with `Update clan report site`, and pushes to origin.

The repo includes systemd templates at:

```text
systemd/coc-report-deploy.service
systemd/coc-report-deploy.timer
```

Install and start on the Droplet:

```bash
sudo cp systemd/coc-report-deploy.service /etc/systemd/system/coc-report-deploy.service
sudo cp systemd/coc-report-deploy.timer /etc/systemd/system/coc-report-deploy.timer
sudo systemctl daemon-reload
sudo systemctl enable coc-report-deploy.timer
sudo systemctl start coc-report-deploy.timer
sudo systemctl start coc-report-deploy.service
journalctl -u coc-report-deploy -n 100 --no-pager
systemctl list-timers | grep coc
```

Check the latest pushed site update:

```bash
git log --oneline -5 -- site_output
```

## Known Gotchas

- `fetch_current_war_snapshot.py` may exist on local Mac but not on Droplet if changes were not pushed.
- Droplet interactive shell does not automatically load `.env`.
- Clash API may fail from Mac or Cloudflare with `403 accessDenied.invalidIp`; Droplet is the trusted fetch environment.
- `data/wars/` may be ignored; the important deployed artifact is usually `site_output/current-war.html`.
- Weekly and overall report pages need completed snapshots in `data/war_results/`; current war snapshots in `data/wars/` do not populate those reports.
- The deploy timer should only commit `site_output/`; do not use it to publish `.env`, `data/`, or unrelated local changes.
- Do not paste `.env` or API tokens into chat.
- `update_coc_report.sh` is currently untracked; review before committing.

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

### Current War Page Shows Fallback

Run:

```bash
python3 fetch_war.py
python3 build_site.py --include-current-war
grep -n "Current war data unavailable" site_output/current-war.html
```

### Bot Not Responding

Run:

```bash
systemctl status clashcommand
journalctl -u clashcommand -n 100 --no-pager
systemctl restart clashcommand
```

### Weekly Or Overall Reports Are Empty

Check whether final war snapshots exist:

```bash
ls -lh data/war_results/
systemctl status coc-war-snapshot
journalctl -u coc-war-snapshot -n 100 --no-pager
```

If the service is missing, install and start `coc-war-snapshot.service` from the Final War Snapshot Service section.

### Site Is Not Updating

Check the deploy timer and last one-shot run:

```bash
systemctl list-timers | grep coc
journalctl -u coc-report-deploy -n 100 --no-pager
sudo systemctl start coc-report-deploy.service
```

If the logs say `No generated site changes to deploy.`, the rebuild matched the committed `site_output/`.

## Next Improvements

TODO:

- Standardize token names to `CLASH_API_TOKEN` and `CLAN_TAG`.
- Add a stable current-war snapshot file path if desired.
- Add clan activity/member snapshots later.
- Decide whether `update_coc_report.sh` should be tracked.
- Watch the deploy timer logs after the first completed war snapshot.

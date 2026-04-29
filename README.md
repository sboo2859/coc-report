# CoC Report

Utilities for fetching Clash of Clans current war snapshots.

## Discord Bot Conversion

This project is being planned for conversion into **ClashCommand**, a Discord bot for war accountability and smart reminders. The existing script and static report flow should remain usable while reusable Clash API, war timing, missed-attack, and reporting logic is extracted into a bot-ready package.

Planning docs:

- [Discord Bot Audit](docs/DISCORD_BOT_AUDIT.md)
- [Discord Bot MVP Plan](docs/DISCORD_BOT_MVP_PLAN.md)
- [ClashCommand Droplet Deployment](docs/CLASHCOMMAND_DROPLET_DEPLOY.md)

## System Documentation

- [System Architecture](docs/SYSTEM_ARCHITECTURE.md)
- [Pipeline Flow](docs/PIPELINE_FLOW.md)
- [Data Contracts](docs/DATA_CONTRACTS.md)
- [Operation Guide](docs/OPERATION.md)
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

The scheduler watches the `currentwar` response, waits until `endTime` plus a settlement buffer, then saves the final war snapshot. It also saves immediately when the current war is already in `warEnded` state.

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

Optionally include a static current-war page from the live Clash API:

```bash
python3 build_site.py --include-current-war
```

This writes:

```text
site_output/index.html
site_output/current-war.html
site_output/history.html
```

The weekly report uses the selected report window from saved final snapshots in `data/war_results/` and does not require API access. Total History uses all saved final snapshots in `data/war_results/`. The current-war page requires `COC_API_TOKEN` at build time. If the token is missing or the API call fails, the build still writes `current-war.html` with an unavailable-data message.

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

### Local deploy

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

### Cloudflare Pages setup

Use:

- Framework preset: None
- Build command: leave blank
- Build output directory: `site_output`
- Production branch: `main`

Cloudflare Pages will redeploy when GitHub receives a new push.

## Scheduler Settings

These environment variables are optional:

```bash
export WAR_END_BUFFER_MINUTES=2
export WAR_PREP_POLL_MINUTES=30
export WAR_IDLE_POLL_MINUTES=60
export WAR_ENDED_POLL_MINUTES=30
export WAR_RESULTS_DIR=data/war_results
export WAR_WARNING_TARGET_HOURS=3
export WAR_WARNING_INCLUDE_COUNTS=true
export REPORT_DAYS=7
```

Defaults are shown above.

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

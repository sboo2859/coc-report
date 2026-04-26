# Pipeline Flow

## Manual Flow

Run:

```bash
python3 fetch_war.py
```

The script:

1. Reads `COC_API_TOKEN` and `COC_CLAN_TAG`.
2. Calls the Clash `currentwar` endpoint.
3. Prints each clan member's attack participation.
4. Saves the raw response to `data/wars/war_YYYY-MM-DD_HH-MM.json`.

Manual snapshots are useful for inspection, but they are only final if the command is run after war results have settled.

## Automated Flow (Scheduler)

Run:

```bash
python3 schedule_war_snapshot.py
```

The scheduler loops until stopped. Each loop fetches the current war and branches on `state`.

For `inWar`:

1. Parse `endTime`.
2. Calculate `endTime + WAR_END_BUFFER_MINUTES`.
3. Sleep until that exact target time.
4. Fetch the war again.
5. Save the final snapshot if it has not already been saved.

The default buffer is 2 minutes. The buffer exists to avoid fetching at the exact war end moment, when final stars, destruction, and state may still be settling.

For `warEnded`:

1. Save immediately if this war has not already been saved.
2. Sleep for `WAR_ENDED_POLL_MINUTES`.

For `preparation`:

1. Do not save a result snapshot.
2. Sleep for `WAR_PREP_POLL_MINUTES`.

For `notInWar` or unknown states:

1. Do not save a result snapshot.
2. Sleep for `WAR_IDLE_POLL_MINUTES`.

If an API call fails, the scheduler logs the error, sleeps, and retries later.

## Warning Message Flow

Run:

```bash
python3 war_warning_message.py
```

The script fetches the live current war and only generates a reminder if `state == "inWar"`.

For each clan member:

1. Read `attacksPerMember`, defaulting to 2 if missing.
2. Count `member.attacks`, treating missing attacks as zero.
3. Calculate remaining attacks.
4. Include only members with remaining attacks.
5. Sort by remaining attacks descending, then name ascending.

The output is plain text for a leader to copy into chat. It does not send messages or attempt automatic tagging.

## Weekly Report Flow

Run:

```bash
python3 weekly_report.py
```

The script:

1. Reads local JSON files from `data/war_results/`.
2. Skips malformed or unreadable files.
3. Filters wars to the last `REPORT_DAYS` days.
4. Deduplicates wars by `(startTime, clan.tag, opponent.tag)`.
5. Aggregates record, attack usage, stars, missed attacks, top performers, and average destruction when available.
6. Builds a full roster table for the report window with wars participated, attacks, stars, and average attack destruction.
7. Prints a copy/paste-ready report.

Weekly reporting never calls the Clash API. This makes it safe to run anytime, even without an API token.

## Static Site Flow

Run:

```bash
python3 build_site.py
```

To generate only the weekly page directly from the report script:

```bash
python3 weekly_report.py --site
```

The `build_site.py` static site flow:

1. Reads local JSON files from `data/war_results/`.
2. Builds the same weekly report text used by terminal output.
3. Builds a self-contained dashboard with stat cards, current-window roster, and the copy/paste text report.
4. Writes `site_output/index.html`.
5. Builds all-time history from all deduped saved final snapshots.
6. Writes `site_output/history.html`.
7. The generated files can be committed and pushed to GitHub.
8. Cloudflare Pages serves `site_output/` as a no-framework static site.

This flow is:

```text
local snapshots -> weekly_report.py/build_site.py -> site_output/index.html -> GitHub -> Cloudflare Pages
data/war_results/ -> build_site.py -> history.html -> GitHub -> Cloudflare Pages
```

Displayed site timestamps are converted to Central Time with `ZoneInfo("America/Chicago")` when available.

## Total History Flow

The history page is generated during the default static site build:

```bash
python3 build_site.py
```

The history flow:

1. Reads local JSON files from `data/war_results/`.
2. Skips malformed or unreadable files.
3. Deduplicates wars by `(startTime, clan.tag, opponent.tag)`.
4. Aggregates all-time record, attacks, stars, and average destruction.
5. Calculates member accountability and full roster metrics across all tracked wars.
6. Writes `site_output/history.html`.

History generation never calls the Clash API.

## Static Current War Flow

Run:

```bash
python3 build_site.py --include-current-war
```

The command always builds the weekly report. It then attempts one live API call for the current war.

If the API call succeeds:

1. Fetch the live `currentwar` response through `fetch_current_war()`.
2. Calculate used attacks, possible attacks, unused attacks, and members with remaining attacks.
3. Parse start and end times and display them in Central Time.
4. Calculate time remaining when `state == "inWar"`.
5. Write `site_output/current-war.html`.

If the API token is missing or the API call fails, the command still writes `site_output/current-war.html` with a "Current war data unavailable" message.

This flow is:

```text
Clash API -> build_site.py --include-current-war -> current-war.html -> GitHub -> Cloudflare Pages
```

## Local Deploy Flow

Run a one-time deploy:

```bash
./deploy.sh
```

The deploy command:

1. Runs `python3 build_site.py --include-current-war`.
2. Checks `site_output/` for generated changes.
3. Stages only `site_output/`.
4. Commits with `Update CoC report site` when generated output changed.
5. Pushes normally to GitHub.
6. Cloudflare Pages redeploys from the push.

Run continuous PC-based refresh:

```bash
./auto_deploy_loop.sh
```

The auto loop follows this flow:

```text
PC auto loop -> build_site.py --include-current-war -> site_output/ -> git push -> Cloudflare Pages
```

After each loop, `war_poll_interval.py` fetches current war state and returns the next sleep interval: 2 hours when not in war, 60 minutes during preparation or after war end, 30 minutes during active war, 15 minutes under 3 hours left, 10 minutes under 1 hour left, and 60 minutes when API access is unavailable.

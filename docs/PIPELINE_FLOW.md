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
6. Prints a copy/paste-ready report.

Weekly reporting never calls the Clash API. This makes it safe to run anytime, even without an API token.

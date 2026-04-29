# ClashCommand Discord Bot Audit

## Executive Summary

The current CoC Report project is a script and static-site reporting system. It already has useful domain logic for Clash API access, war timing, attack counting, missed-attack detection, final snapshot capture, weekly aggregation, and static HTML rendering.

The cleanest conversion path is not a giant rewrite. Preserve the current behavior as a compatibility layer, then extract reusable logic into a package that a Discord bot can call. ClashCommand should introduce a database-backed config model, Discord command handlers, a scheduler service, and explicit multi-guild boundaries while keeping the static report generator available as an optional output.

The first production target can still be one clan on one DigitalOcean droplet, but the code should treat that clan as configuration belonging to a Discord guild instead of a hardcoded global default.

## Current Repository Structure

Top-level scripts:

- `fetch_war.py`: shared Clash API fetch helper plus a manual fetch command. Reads `COC_API_TOKEN` and `COC_CLAN_TAG`, calls `/clans/{tag}/currentwar`, prints participation, and saves raw snapshots under `data/wars/`.
- `schedule_war_snapshot.py`: long-running scheduler. Polls current war state, waits until `endTime + WAR_END_BUFFER_MINUTES`, saves final snapshots to `data/war_results/`, and dedupes with `data/state/saved_wars.json`.
- `war_warning_message.py`: live copy/paste reminder generator. Fetches current war and lists members with remaining attacks.
- `war_poll_interval.py`: computes a polling interval based on war state and time remaining.
- `weekly_report.py`: report and rendering module. Loads saved final snapshots, dedupes wars, aggregates weekly and all-time metrics, builds roster accountability data, generates text reports, and renders static HTML pages.
- `build_site.py`: static site entrypoint. Builds weekly and history pages, and optionally a current-war page from the live API.
- `deploy.sh`: local build, commit, and push flow for `site_output/`.
- `auto_deploy_loop.sh`: local long-running loop that rebuilds and pushes static output using `war_poll_interval.py`.
- `Procfile`: currently points a worker at `python fetch_war.py`, which is not enough for the future bot or scheduler.

Documentation already present:

- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/PIPELINE_FLOW.md`
- `docs/DATA_CONTRACTS.md`
- `docs/OPERATION.md`
- `docs/DECISIONS.md`

Generated and runtime paths:

- `data/wars/`: manual raw snapshots.
- `data/war_results/`: final snapshots used by reports.
- `data/state/saved_wars.json`: file-based dedupe state.
- `site_output/`: committed static Cloudflare Pages output.

## Existing Data Flow

Current flow:

```text
Clash API
  -> fetch_war.py
  -> schedule_war_snapshot.py
  -> data/war_results/*.json
  -> weekly_report.py
  -> build_site.py
  -> site_output/*.html
  -> git push
  -> Cloudflare Pages
```

Live warning flow:

```text
Clash API
  -> war_warning_message.py
  -> copy/paste reminder text
```

Current-war static page flow:

```text
Clash API
  -> build_site.py --include-current-war
  -> site_output/current-war.html
```

## Reusable Components

Keep and extract these into package modules:

- Clash API fetch logic from `fetch_war.py`.
- `parse_coc_time()` from `schedule_war_snapshot.py`, ideally with one canonical implementation.
- War identity and dedupe logic from `schedule_war_snapshot.py` and `weekly_report.py`.
- Remaining-attack and missed-attack logic from `war_warning_message.py` and `weekly_report.py`.
- Current-war summary logic from `weekly_report.current_war_attack_summary()`.
- Weekly aggregation from `weekly_report.aggregate_wars()`, `filter_recent_wars()`, `build_member_roster()`, and `generate_weekly_report_data()`.
- Static rendering from `weekly_report.py`, but keep it behind an optional report/export layer.
- Environment loading pattern, but move bot configuration to a dedicated config module that supports `.env`.

The existing report code is valuable, but `weekly_report.py` is doing too many jobs now: aggregation, HTML rendering, current-war display, and CLI handling. For the bot, split domain calculations from presentation.

## Assumptions That Need To Change

Single clan:

- Current code defaults to `#22YY2LPV2` in `fetch_war.py`.
- Bot design should store clan tags per Discord guild in a `guilds` table.
- Future multi-clan support can be added with a `guild_clans` table, but MVP can use one clan per guild.

Local PC and static deployment:

- `deploy.sh` and `auto_deploy_loop.sh` assume a local machine with Git credentials and a token in the environment.
- The Discord bot should run as a long-lived service on the droplet. It should not commit generated files as part of normal operation.

Manual run:

- Current commands are CLI scripts.
- The bot needs slash commands, background scheduled jobs, durable event tracking, and restart-safe dedupe.

Hardcoded paths:

- Current paths are relative: `data/wars`, `data/war_results`, `data/state`, `site_output`.
- Bot paths should come from config, with a default app data directory such as `/opt/clashcommand/data` on the droplet.

Time zone:

- Static display currently uses Central Time with UTC fallback.
- Bot should store all times in UTC and allow a per-guild timezone setting for display.

Token handling:

- Current API token comes from `COC_API_TOKEN`, which is good.
- Discord token must also come from environment only.
- Never commit `.env`, tokens, generated secrets, or droplet-local config.

Static site:

- The static site is a useful optional product surface, but it should not be required for Discord bot operation.
- Later it can become `/report export`, a hosted dashboard, or an admin-only artifact.

## Recommended Bot Architecture

Proposed package structure:

```text
clashcommand/
  __init__.py
  bot.py
  config.py
  logging.py
  db/
    __init__.py
    connection.py
    migrations.py
    models.py
    repositories.py
  clash/
    __init__.py
    client.py
    time.py
    normalizers.py
  services/
    __init__.py
    wars.py
    reminders.py
    reports.py
    players.py
    entitlements.py
  discord/
    __init__.py
    app.py
    commands/
      setup.py
      war.py
      missed.py
      players.py
      settings.py
    views.py
    formatting.py
  scheduler/
    __init__.py
    runner.py
    jobs.py
  static_reports/
    __init__.py
    render.py
scripts/
  bot_dev.py
  migrate_db.py
  import_snapshots.py
```

Layer responsibilities:

- `discord/`: slash command definitions, permission checks, Discord message formatting, embeds, ephemeral responses.
- `clash/`: Clash API client, API errors, request timeouts, time parsing, raw response normalization.
- `services/`: business logic. Commands and schedulers should call services instead of parsing raw JSON directly.
- `scheduler/`: background polling, reminder timing, final snapshot capture, restart-safe job recovery.
- `db/`: SQLite connection, migrations, repositories, and transaction boundaries.
- `static_reports/`: optional renderer using the existing static HTML ideas.

For the Discord library, use either `discord.py` 2.x or `py-cord`. Because slash commands already worked on the droplet, keep whichever library the proof-of-concept used unless there is a strong reason to change. `discord.py` 2.x is a conservative default.

## Recommended Database Design

Start with SQLite using normal SQL migrations. Keep column types and access patterns compatible with a later Postgres migration. Store raw Clash war JSON as text JSON in SQLite; use JSONB later in Postgres if needed.

### `guilds`

Purpose: one row per Discord server.

Suggested columns:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `discord_guild_id TEXT NOT NULL UNIQUE`
- `guild_name TEXT`
- `primary_clan_tag TEXT`
- `war_channel_id TEXT`
- `reminder_channel_id TEXT`
- `timezone TEXT NOT NULL DEFAULT 'UTC'`
- `reminders_enabled INTEGER NOT NULL DEFAULT 1`
- `missed_attack_threshold INTEGER NOT NULL DEFAULT 1`
- `plan TEXT NOT NULL DEFAULT 'free'`
- `subscription_status TEXT NOT NULL DEFAULT 'inactive'`
- `subscription_current_period_end TEXT`
- `stripe_customer_id TEXT`
- `stripe_subscription_id TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

### `linked_players`

Purpose: map Discord users to Clash player tags.

Suggested columns:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `guild_id INTEGER NOT NULL REFERENCES guilds(id)`
- `discord_user_id TEXT`
- `clash_player_tag TEXT NOT NULL`
- `player_name TEXT`
- `linked_by_discord_user_id TEXT`
- `verified_at TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- unique index on `(guild_id, clash_player_tag)`
- optional unique index on `(guild_id, discord_user_id)` if MVP allows only one player per Discord user

For Clash alt accounts later, allow multiple player tags per Discord user and add `is_primary`.

### `war_snapshots`

Purpose: durable war state and history.

Suggested columns:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `guild_id INTEGER NOT NULL REFERENCES guilds(id)`
- `clan_tag TEXT NOT NULL`
- `opponent_tag TEXT`
- `state TEXT`
- `preparation_start_time TEXT`
- `start_time TEXT`
- `end_time TEXT`
- `snapshot_type TEXT NOT NULL`
- `war_key TEXT NOT NULL`
- `fetched_at TEXT NOT NULL`
- `raw_json TEXT NOT NULL`
- unique index on `(guild_id, war_key, snapshot_type)`

Use `snapshot_type` values such as `poll`, `final`, and `manual`.

### `reminder_events`

Purpose: prevent duplicate reminder spam and audit reminders.

Suggested columns:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `guild_id INTEGER NOT NULL REFERENCES guilds(id)`
- `war_snapshot_id INTEGER REFERENCES war_snapshots(id)`
- `war_key TEXT NOT NULL`
- `event_type TEXT NOT NULL`
- `scheduled_for TEXT`
- `sent_at TEXT`
- `discord_channel_id TEXT`
- `discord_message_id TEXT`
- `status TEXT NOT NULL DEFAULT 'pending'`
- `error TEXT`
- `created_at TEXT NOT NULL`
- unique index on `(guild_id, war_key, event_type)`

Example event types: `three_hour_warning`, `one_hour_warning`, `final_report`.

### `usage_events`

Purpose: analytics and future plan limits.

Suggested columns:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `guild_id INTEGER REFERENCES guilds(id)`
- `discord_user_id TEXT`
- `event_type TEXT NOT NULL`
- `command_name TEXT`
- `metadata_json TEXT`
- `created_at TEXT NOT NULL`

### Later Tables

Add later when needed:

- `guild_clans`: support multiple clans per Discord server.
- `clan_members`: cached roster data.
- `subscriptions`: if Stripe logic grows beyond fields on `guilds`.
- `audit_log`: admin config changes.

## Recommended MVP Command Set

### `/setup`

Purpose: connect a Discord server to a Clash clan.

Inputs:

- `clan_tag`
- optional `war_channel`
- optional `reminder_channel`
- optional `timezone`

Behavior:

- Validate Discord permissions.
- Validate clan tag format.
- Fetch current war or clan metadata to confirm API access.
- Upsert `guilds`.
- Respond ephemerally with setup summary.

### `/war`

Purpose: show current war status.

Behavior:

- Load guild config.
- Fetch current war.
- Store a poll snapshot.
- Display state, opponent, score, destruction, attack usage, time remaining, and remaining attackers.

### `/missed`

Purpose: show members with remaining attacks in the current war.

Behavior:

- Fetch current war.
- Calculate remaining attacks.
- If linked players exist, mention Discord users when available.
- Avoid posting huge messages; use embeds or split output when needed.

### `/link-player`

Purpose: let a Discord user link their own Clash player tag.

Inputs:

- `player_tag`

Behavior:

- Store mapping in `linked_players`.
- MVP can be trust-based.
- Later verification can use Clash API player profile labels, town hall, or a verification code workflow if feasible.

### `/link-member`

Purpose: admin links a Clash player to a Discord user.

Inputs:

- `discord_user`
- `player_tag`

Behavior:

- Requires admin or configured bot manager permission.
- Upsert mapping.

### `/roster-unlinked`

Purpose: show current war or clan roster members not mapped to Discord users.

Behavior:

- Fetch current war roster for MVP.
- Compare `member.tag` to `linked_players`.
- Return unlinked names and tags.

### `/settings`

Purpose: inspect current bot config.

Behavior:

- Show clan tag, configured channels, timezone, reminders enabled, and plan status.
- Add subcommands later for updates.

## Deployment Recommendation

Target: DigitalOcean Ubuntu droplet.

Repository location:

```text
/opt/clashcommand/app
```

Runtime data:

```text
/opt/clashcommand/data
```

Virtual environment:

```bash
cd /opt/clashcommand/app
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Recommended `.env` variables:

```text
DISCORD_TOKEN=
DISCORD_APPLICATION_ID=
COC_API_TOKEN=
DATABASE_URL=sqlite:////opt/clashcommand/data/clashcommand.sqlite3
CLASHCOMMAND_DATA_DIR=/opt/clashcommand/data
LOG_LEVEL=INFO
DEFAULT_TIMEZONE=America/Chicago
ENVIRONMENT=production
```

Optional MVP variables:

```text
DISCORD_TEST_GUILD_ID=
COMMAND_SYNC_MODE=guild
REMINDERS_ENABLED=true
WAR_END_BUFFER_MINUTES=2
```

Use `.env.example` in the repo later, but keep the real `.env` only on the droplet.

Systemd service:

```ini
[Unit]
Description=ClashCommand Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/clashcommand/app
EnvironmentFile=/opt/clashcommand/.env
ExecStart=/opt/clashcommand/app/.venv/bin/python -m clashcommand.bot
Restart=always
RestartSec=10
User=clashcommand
Group=clashcommand

[Install]
WantedBy=multi-user.target
```

Common operations:

```bash
sudo systemctl daemon-reload
sudo systemctl enable clashcommand
sudo systemctl start clashcommand
sudo systemctl status clashcommand
journalctl -u clashcommand -f
sudo systemctl restart clashcommand
```

Safe restart workflow:

1. Develop locally in VS Code.
2. Commit or otherwise package changes intentionally.
3. Pull or deploy the repo to `/opt/clashcommand/app`.
4. Install dependencies in the venv.
5. Run migrations.
6. Restart the systemd service.
7. Watch logs with `journalctl -u clashcommand -f`.

## Monetization-Ready Hooks

Do not implement Stripe in MVP, but design for it now:

- Add `plan`, `subscription_status`, `stripe_customer_id`, `stripe_subscription_id`, and `subscription_current_period_end` on `guilds`.
- Create `services/entitlements.py` with a function such as `can_use_feature(guild, feature_name)`.
- Gate future premium features through the entitlement function, not direct plan checks in command handlers.
- Track commands in `usage_events` from day one.
- Add `/upgrade` later as a placeholder command that returns a billing link or support message.
- Add a Stripe webhook endpoint later only if a web server becomes part of the deployment. Until then, a separate lightweight FastAPI service can update the same database.

Possible feature gates later:

- More reminder schedules.
- Historical reports beyond a free lookback window.
- Multiple clans per Discord server.
- Automatic end-of-war reports.
- Advanced weekly accountability summaries.
- Exportable dashboards.

## Risks And Gotchas

Clash API IP allowlisting:

- The droplet IP must stay allowlisted for the Clash API token.
- If the droplet IP changes, all live commands and scheduler jobs will fail.

Discord token handling:

- The Discord token must only live in the droplet `.env` or a secret manager.
- Never paste it into source, docs, shell history examples, or committed config.

Slash command sync:

- Global command sync can take time to appear.
- MVP should support guild-scoped sync for the private test server, then global sync later.

Bot downtime and restarts:

- The scheduler must recover from downtime by checking current war state on startup.
- Reminder dedupe must be stored in the database, not in memory.

API rate limits:

- Cache poll snapshots briefly.
- Avoid fetching current war separately for every command if several commands are used together.

Private war logs and unavailable states:

- The Clash API can return inaccessible war data depending on clan settings or state.
- Commands must handle `notInWar`, `preparation`, `warEnded`, and API errors cleanly.

Player mapping:

- Clash player names are mutable; player tags should be the stable key.
- Discord users may have multiple Clash accounts.
- Some war members may not be in Discord.

Duplicate reminder spam:

- Use `reminder_events` unique keys per guild, war, and reminder type.
- Record attempted sends, success, failure, and message IDs.

Multi-server isolation:

- Every command and query must be scoped by `discord_guild_id`.
- Do not use global clan tag or global scheduler state in bot logic.

Static report drift:

- If static generation remains, it should read from the same database or an explicit export, not a parallel truth source.

## Recommended Build Phases

1. Package extraction:
   - Create `clashcommand/`.
   - Move or wrap Clash API, time parsing, war summaries, and report calculations.
   - Keep old scripts working by importing from the package.

2. Database foundation:
   - Add SQLite connection and migrations.
   - Add `guilds`, `linked_players`, `war_snapshots`, `reminder_events`, and `usage_events`.
   - Add an import script for existing `data/war_results/*.json`.

3. Discord MVP:
   - Implement bot startup, config loading, logging, and guild-scoped command sync.
   - Add `/setup`, `/settings`, `/war`, and `/missed`.

4. Player linking:
   - Add `/link-player`, `/link-member`, and `/roster-unlinked`.
   - Use linked users in missed-attack output.

5. Scheduler and reminders:
   - Move war polling into a restart-safe scheduler service.
   - Save snapshots and reminder events to SQLite.
   - Add smart reminder messages.

6. Reports:
   - Add end-of-war report posting.
   - Add weekly accountability summaries.
   - Keep static HTML rendering optional.

7. Product hardening:
   - Add entitlement checks.
   - Add usage tracking.
   - Add tests around service logic.
   - Prepare migration path to Postgres.

## Small Refactor Targets

These are good first code changes after the audit:

- Add `.env.example` and `.gitignore` entries for `.env`, SQLite files, logs, and venvs.
- Add `clashcommand/config.py`.
- Add one canonical `parse_coc_time()`.
- Extract remaining-attack calculations into a pure function module.
- Add tests for war summaries using fixture JSON.


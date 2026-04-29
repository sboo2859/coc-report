# ClashCommand MVP Implementation Plan

## Goal

Build ClashCommand as a Discord bot without breaking the current static report scripts. The MVP should support one clan per Discord server, use SQLite, run cleanly on a DigitalOcean Ubuntu droplet, and leave room for multi-server distribution and paid features.

## Phase 0 - Repo Safety And Baseline

Work:

- Add `.gitignore` coverage for `.env`, `.venv/`, SQLite databases, logs, and droplet-local files.
- Add `.env.example` with placeholder values only.
- Add bot dependencies to `requirements.txt` after choosing the Discord library.
- Keep existing CLI scripts runnable.

Test criteria:

- `python3 weekly_report.py` still runs.
- `python3 build_site.py` still writes `site_output/index.html` and `site_output/history.html`.
- No secrets appear in `git status` or diffs.

## Phase 1 - Extract Reusable Domain Logic

Work:

- Create `clashcommand/` package.
- Move Clash API code into `clashcommand/clash/client.py`.
- Move Clash time parsing into `clashcommand/clash/time.py`.
- Move current-war attack summary and missed-attack calculations into `clashcommand/services/wars.py`.
- Keep old scripts as thin wrappers where practical.

Suggested files:

```text
clashcommand/
  config.py
  clash/client.py
  clash/time.py
  services/wars.py
```

Test criteria:

- Old scripts still work with the same environment variables.
- Unit-style checks can call service functions with saved JSON and get the same missed-attack counts as before.
- No Discord code is required yet.

## Phase 2 - SQLite Foundation

Work:

- Add database connection and migration runner.
- Create tables: `guilds`, `linked_players`, `war_snapshots`, `reminder_events`, `usage_events`.
- Add repository functions for guild setup, linked players, snapshot save, and reminder dedupe.
- Add `scripts/migrate_db.py`.

Suggested files:

```text
clashcommand/db/connection.py
clashcommand/db/migrations.py
clashcommand/db/repositories.py
scripts/migrate_db.py
```

Test criteria:

- Running migrations creates a SQLite database from scratch.
- Running migrations twice is safe.
- A small smoke script can create a guild, link a player, save a war snapshot, and read it back.

## Phase 3 - Bot Shell And Command Sync

Work:

- Add `clashcommand/bot.py` entrypoint.
- Load config from environment and optional `.env`.
- Configure logging.
- Start the Discord client.
- Sync slash commands to `DISCORD_TEST_GUILD_ID` for fast MVP iteration.
- Add a simple `/settings` command showing missing setup when no guild row exists.

Suggested files:

```text
clashcommand/bot.py
clashcommand/discord/app.py
clashcommand/discord/commands/settings.py
clashcommand/discord/formatting.py
```

Test criteria:

- Bot starts locally or on the droplet without indentation/manual editing issues.
- `/settings` appears in the test Discord server.
- `/settings` responds ephemerally.
- Logs show startup and command sync results.

## Phase 4 - Setup And Current War Commands

Work:

- Implement `/setup`.
- Implement `/war`.
- Store guild config in SQLite.
- Fetch current war using the configured clan tag.
- Save a `poll` snapshot to `war_snapshots`.
- Display clean status output with state, opponent, stars, destruction, time remaining, used attacks, and unused attacks.

Test criteria:

- `/setup clan_tag:#...` validates and saves config.
- `/settings` shows the saved clan tag and channels.
- `/war` works on the droplet using the allowlisted Clash API token.
- `/war` handles `notInWar`, `preparation`, `inWar`, `warEnded`, and API errors gracefully.

## Phase 5 - Missed Attacks And Player Linking

Work:

- Implement `/missed`.
- Implement `/link-player`.
- Implement `/link-member`.
- Implement `/roster-unlinked`.
- Use Clash player tags as stable identifiers.
- Mention linked Discord users when showing missed attacks.

Test criteria:

- `/link-player player_tag:#...` links the caller.
- `/link-member @user #...` links another user when run by an admin.
- `/missed` lists remaining attacks and mentions linked users.
- `/roster-unlinked` shows war members without Discord links.
- Duplicate links are updated intentionally rather than creating conflicting rows.

## Phase 6 - Restart-Safe Scheduler

Work:

- Add background scheduler loop inside the bot process, or a separate worker process if preferred.
- Poll configured guilds.
- Compute next relevant reminder windows.
- Save final snapshots after `endTime + WAR_END_BUFFER_MINUTES`.
- Record reminder sends in `reminder_events` before or during send attempts.
- Prevent duplicate reminders with unique keys.

Suggested files:

```text
clashcommand/scheduler/runner.py
clashcommand/scheduler/jobs.py
clashcommand/services/reminders.py
```

Test criteria:

- Restarting the bot does not resend an already sent reminder.
- Restarting during war recovers by fetching current state.
- Final snapshot save dedupes by guild and war key.
- Scheduler logs are useful in `journalctl`.

## Phase 7 - End-Of-War And Weekly Reports

Work:

- Move weekly aggregation to use either database snapshots or an explicit DB export.
- Add end-of-war report generation from final snapshots.
- Add weekly accountability summary command or scheduled post.
- Keep static HTML generation optional.

Possible commands later in this phase:

- `/report weekly`
- `/report history`
- `/report current`

Test criteria:

- Existing JSON-based weekly report still works during transition.
- Database-backed weekly report matches known fixture results.
- End-of-war report posts once per completed war.

## Phase 8 - Deployment Hardening

Work:

- Add `systemd/clashcommand.service.example`.
- Document droplet setup and restart workflow.
- Add structured logging.
- Add a health check command or log heartbeat.
- Update `Procfile` or remove it if no longer useful.

Test criteria:

- Fresh droplet setup can run from repo checkout plus `.env`.
- `sudo systemctl restart clashcommand` restarts cleanly.
- `journalctl -u clashcommand -f` shows useful logs.
- No manual nano edits are required on the server.

## Phase 9 - Monetization Hooks

Work:

- Add `services/entitlements.py`.
- Use `can_use_feature(guild, feature)` in commands and scheduler decisions.
- Record command usage in `usage_events`.
- Add a non-functional `/upgrade` placeholder only when ready.

Test criteria:

- Free guilds can use MVP commands.
- Feature gates can deny a fake premium feature in tests.
- Usage events are written for commands without blocking command success.

## Recommended First Coding Step

Start with Phase 0 and Phase 1 together:

1. Add `.gitignore` and `.env.example`.
2. Create the `clashcommand/` package.
3. Extract only pure, low-risk logic first: time parsing and remaining-attack calculations.
4. Keep existing script behavior unchanged.

That gives the bot a clean foundation without touching Discord, databases, or deployment yet.

## Decisions Needed Before Implementation

- Discord library: keep the library used in the droplet proof-of-concept if known, otherwise use `discord.py` 2.x.
- Command sync mode for MVP: guild-only sync for the private test server is recommended.
- Player linking policy: allow one Clash account per Discord user for MVP, or support alts immediately.
- Scheduler placement: run scheduler inside the bot process for simplicity, or as a separate worker for cleaner operations.
- Static report future: keep JSON static reports as legacy, or migrate static generation to database snapshots after MVP.


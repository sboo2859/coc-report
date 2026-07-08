# Architecture Decisions

## Decision: Use a scheduler instead of manual timing

Reason:

- Final war results are only useful if captured after war end.
- Manual timing is easy to miss.
- The Clash API provides `endTime`, so the system can schedule the final fetch directly.

Tradeoffs:

- Requires a long-running process.
- Needs basic retry and logging behavior.
- Acceptable because the scheduler is simple and can run locally or on a VPS.

## Decision: Use local JSON instead of a database

Reason:

- No infrastructure required.
- Raw API responses are preserved for future analysis.
- Easy to inspect, copy, back up, and move.

Tradeoffs:

- Harder to query at scale.
- No built-in indexing or concurrency controls.
- Acceptable for the current data volume and v1 workflow.

## Decision: Separate manual snapshots from final war results

Reason:

- Manual snapshots may be taken during preparation or active war.
- Final snapshots are intended to be the trusted input for historical reports.
- Separate folders make accidental analysis of non-final data less likely.

Tradeoffs:

- There are two snapshot directories to understand.
- Users need to know that reports read `data/war_results/` by default.

## Decision: Generate copy/paste warning messages instead of automating chat

Reason:

- The project does not integrate with Clash chat.
- Pasted names do not guarantee true in-game notifications.
- A leader-reviewed message is safer and avoids false assumptions about tagging.

Tradeoffs:

- Leaders still need to paste the message manually.
- True notification behavior remains outside the system.

## Decision: Use JSON state for scheduler dedupe

Reason:

- The scheduler needs to avoid saving the same ended war repeatedly.
- A small local state file is enough for current usage.
- The dedupe key uses stable war fields from the API.

Tradeoffs:

- If the state file is deleted, an old ended war may be saved again.
- This is simpler than a database and acceptable for local operation.

## Decision: Keep weekly reporting offline

Reason:

- Reports should be reproducible from saved data.
- Historical analysis should not depend on current API availability.
- The report can run even when the API token is missing.

Tradeoffs:

- Reports are only as complete as the saved snapshots.
- If the scheduler was not running, that war may be absent from the report.

## Decision: Automate CWL recaps and reminders in the bot process

Reason:

- Regular wars already had automatic recaps and reminders; CWL was read-only.
- Running CWL recap and reminder schedulers in the bot process reuses the existing channel, dedupe, and decision logic.
- Namespaced reminder keys (`cwl_post_war_report`, `cwl_3h`, `cwl_1h`) keep CWL events from colliding with regular-war events in the shared `reminder_events` table.

Tradeoffs:

- More background jobs in one process (four schedulers).
- CWL snapshots include the whole league group, so recap posting must filter to wars the configured clan actually participated in.

## Decision: Poll the Clash API only as often as the next event requires

Reason:

- Polling fixed short intervals during preparation, after war end, and outside CWL wastes API calls with no benefit.
- The API exposes `startTime`/`endTime`, so the scheduler can sleep until the next relevant moment.

Tradeoffs:

- Slightly more scheduler logic (sleep-until-`startTime` with a safety cap, idle backoff, CWL off-season backoff).
- A mid-preparation war change is noticed a little later; acceptable because such changes are rare and the cap re-verifies.

## Decision: Reuse one WAL SQLite connection in the bot

Reason:

- Opening a new connection (and `os.makedirs`) on every store call was pure overhead across four schedulers.
- All DB access runs through `asyncio.to_thread`, so a single `check_same_thread=False` WAL connection serialized by a lock is safe and cheaper.

Tradeoffs:

- WAL adds `-wal`/`-shm` sidecar files that backups must account for.

## Decision: Rank recap MVP by impact, not by name

Reason:

- Top attackers routinely tie at maximum stars and full destruction, so the previous tiebreak on `name.lower()` handed the MVP to the alphabetically first player every war (confirmed across 12 real wars, each with a 6-17 player tie at 6 stars).
- Weighting stars by target difficulty (the opponent's `mapPosition`) rewards three-starring the toughest bases, which discriminates among otherwise-tied perfect attackers.
- A war-seeded deterministic tiebreak (`sha256` of the war key plus player tag) resolves genuine ties without always favoring the same name, while keeping each war's recap reproducible.

Tradeoffs:

- The ranking is less obvious to read than "most stars"; the recap still shows each player's star total.
- Difficulty depends on the opponent `mapPosition` being present in the snapshot; when it is missing, difficulty contributes zero and the seeded tiebreak still applies.

## Decision: Keep ClashCommand a single-clan tool

Reason:

- The tool exists to serve one clan's Discord server; multi-server distribution adds isolation, support, and reliability burden without a matching payoff.
- Supercell's Fan Content Policy prohibits charging fees for a CoC-API tool (only ads, donations, and coaching are allowed), so a paid multi-tenant product is not a viable goal.

Tradeoffs:

- Some code (e.g. `guild_settings`) is per-guild and could support more servers, but multi-tenant hardening and monetization are intentionally not built.

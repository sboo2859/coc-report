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

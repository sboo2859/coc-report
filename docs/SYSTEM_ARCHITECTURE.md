# System Architecture

## Overview

CoC Report helps clan leaders capture Clash of Clans war data at the right time and turn that data into practical summaries.

The system has two jobs:

- collect reliable war snapshots, especially final results after war end
- generate leader-friendly messages and reports from either live war state or saved history

It is intentionally script-based and file-based. That keeps the project easy to run locally, easy to move to a VPS later, and simple to extend without adding infrastructure too early.

## Core Components

`fetch_war.py` is the shared API layer and manual fetch command. It loads `COC_API_TOKEN`, fetches `currentwar`, prints attack participation, and saves manual snapshots to `data/wars/`.

`schedule_war_snapshot.py` is the long-running scheduler. It reuses `fetch_war.py`, watches the current war state, waits until `endTime` plus a buffer, and saves final snapshots to `data/war_results/`.

`war_warning_message.py` fetches the live current war and prints a copy/paste reminder for members with attacks remaining. It does not store data or send notifications.

`weekly_report.py` reads saved final snapshots from `data/war_results/` and builds a weekly performance report. It does not call the Clash API.

## Data Flow

```text
Clash API
   |
   v
fetch_war.py
   |
   +--> data/wars/                  manual snapshots
   |
   v
schedule_war_snapshot.py
   |
   +--> data/state/saved_wars.json  dedupe state
   |
   v
data/war_results/                  final war snapshots
   |
   v
weekly_report.py

Clash API
   |
   v
war_warning_message.py             live copy/paste reminder
```

## How Components Interact

`fetch_war.py` owns Clash API authentication and request logic. Other live-data scripts import `fetch_current_war()` instead of duplicating request code.

The scheduler imports both `fetch_current_war()` and `save_war_snapshot()`. It adds scheduling, dedupe, and final-result timing around those reusable helpers.

The weekly report does not depend on live API access. Its input is the durable JSON output from the scheduler.

## Design Principles

- Keep collection and analysis separate.
- Prefer local JSON until the project needs database-level querying.
- Preserve one-command scripts so each workflow can be run independently.
- Make long-running behavior safe: log clearly, retry after temporary failures, and dedupe final saves.
- Keep leader-facing output clean and copy/paste-ready.

## Key Decisions

The final snapshot is taken after `endTime` plus a buffer because the API may need a short settlement window before final stars and destruction are stable.

Manual snapshots and final snapshots are stored separately. `data/wars/` is useful for ad hoc inspection; `data/war_results/` is the historical source for reports.

Warnings are generated as text instead of sent automatically because Clash chat tagging and notification behavior cannot be safely automated through this project.

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clashcommand.db import LinkedPlayerStore
from clashcommand.post_war_reports import (
    DEFAULT_WAR_RESULTS_DIR,
    POST_WAR_REPORT_REMINDER_TYPE,
    load_war_snapshots,
)
from clashcommand.clash.war import stable_war_key


def parse_args():
    parser = argparse.ArgumentParser(
        description="List or mark post-war recap status for saved final war snapshots."
    )
    parser.add_argument(
        "--db-path",
        default="data/clashcommand.sqlite3",
        help="SQLite database path used by the Discord bot.",
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_WAR_RESULTS_DIR,
        help="Directory containing final_war_*.json snapshots.",
    )
    parser.add_argument(
        "--guild-id",
        required=True,
        help="Discord guild ID whose recap dedupe records should be checked.",
    )
    parser.add_argument(
        "--mark-war-key",
        help="Record a post_war_report event for this exact stable war key.",
    )
    parser.add_argument(
        "--mark-path",
        help="Record a post_war_report event for the snapshot at this path.",
    )
    return parser.parse_args()


def snapshot_key_for_path(path):
    requested = Path(path).resolve()
    for snapshot_path, war in load_war_snapshots(str(requested.parent)):
        if Path(snapshot_path).resolve() == requested:
            return stable_war_key(war)
    return None


def list_snapshots(store, guild_id, data_dir):
    for path, war in load_war_snapshots(data_dir):
        key = stable_war_key(war)
        if not key:
            print(f"UNKEYED {path}")
            continue

        posted = store.has_reminder_event(guild_id, key, POST_WAR_REPORT_REMINDER_TYPE)
        status = "POSTED" if posted else "UNPOSTED"
        clan = war.get("clan", {}).get("name") or war.get("clan", {}).get("tag") or "unknown"
        opponent = war.get("opponent", {}).get("name") or war.get("opponent", {}).get("tag") or "unknown"
        print(f"{status} {path} clan={clan!r} opponent={opponent!r} war_key={key}")


def main():
    args = parse_args()
    store = LinkedPlayerStore(args.db_path)
    store.initialize()

    mark_key = args.mark_war_key
    if args.mark_path:
        mark_key = snapshot_key_for_path(args.mark_path)
        if not mark_key:
            print(f"Could not find a stable war key for snapshot path: {args.mark_path}", file=sys.stderr)
            return 1

    if mark_key:
        store.record_reminder_event(args.guild_id, mark_key, POST_WAR_REPORT_REMINDER_TYPE)
        print(f"Marked post-war recap as posted: guild_id={args.guild_id} war_key={mark_key}")
        return 0

    list_snapshots(store, args.guild_id, args.data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

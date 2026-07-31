"""Find saved final war snapshots that recorded no attacks at all.

Before the battle-day tracking fix, a war that rolled over into the next war
before its final snapshot could be taken was saved from the payload captured at
battle-day start: 0-0 stars with every attack unused. Those files produce a
"tie, everyone missed" recap and skew weekly/history reports, so they should be
deleted.

Usage:
    python scripts/find_empty_war_snapshots.py [data_dir] [--delete]
"""
import glob
import json
import os
import sys


DEFAULT_WAR_RESULTS_DIR = "data/war_results"


def attacks_in(war):
    members = war.get("clan", {}).get("members", []) or []
    return sum(len(member.get("attacks") or []) for member in members)


def describe(path, war):
    clan = war.get("clan", {})
    opponent = war.get("opponent", {})
    return (
        f"{path}\n"
        f"    {clan.get('name', 'Unknown')} vs {opponent.get('name', 'Unknown')} "
        f"| stars {clan.get('stars')}-{opponent.get('stars')} "
        f"| endTime {war.get('endTime')} "
        f"| state {war.get('state')} "
        f"| attacks_used 0"
    )


def main():
    args = [arg for arg in sys.argv[1:] if arg != "--delete"]
    delete = "--delete" in sys.argv[1:]
    data_dir = args[0] if args else DEFAULT_WAR_RESULTS_DIR

    empty = []
    for path in sorted(glob.glob(os.path.join(data_dir, "final_war_*.json"))):
        try:
            with open(path) as f:
                war = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Could not read {path}: {exc}")
            continue

        if attacks_in(war) == 0:
            empty.append((path, war))

    if not empty:
        print(f"No empty war snapshots found in {data_dir}.")
        return

    print(f"Found {len(empty)} war snapshot(s) with zero recorded attacks in {data_dir}:\n")
    for path, war in empty:
        print(describe(path, war))

    if not delete:
        print("\nRe-run with --delete to remove them.")
        print(
            "Also remove the matching war_key entries from data/state/saved_wars.json "
            "only if you want the scheduler to be able to re-save those wars."
        )
        return

    for path, _war in empty:
        os.remove(path)
        print(f"Deleted {path}")


if __name__ == "__main__":
    main()

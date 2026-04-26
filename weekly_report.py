import argparse
import glob
import json
import os
from datetime import datetime, timedelta, timezone

from schedule_war_snapshot import parse_coc_time


DEFAULT_REPORT_DAYS = 7
DEFAULT_WAR_RESULTS_DIR = "data/war_results"


def env_int(name, default):
    value = os.environ.get(name, "").strip()
    if not value:
        return default

    try:
        parsed = int(value)
    except ValueError:
        print(f"Invalid {name}={value!r}; using {default}.")
        return default

    if parsed <= 0:
        print(f"Invalid {name}={value!r}; using {default}.")
        return default

    return parsed


def load_war_files(data_dir):
    paths = sorted(glob.glob(os.path.join(data_dir, "*.json")))
    wars = []

    for path in paths:
        try:
            with open(path) as f:
                war = json.load(f)
        except json.JSONDecodeError as exc:
            print(f"Skipping malformed JSON: {path} ({exc})")
            continue
        except OSError as exc:
            print(f"Skipping unreadable file: {path} ({exc})")
            continue

        wars.append((path, war))

    return wars


def war_start_time(war):
    start_time = war.get("startTime")
    if not start_time:
        return None

    try:
        return parse_coc_time(start_time)
    except ValueError:
        return None


def war_key(war):
    start_time = war.get("startTime")
    clan_tag = war.get("clan", {}).get("tag")
    opponent_tag = war.get("opponent", {}).get("tag")

    if not start_time or not clan_tag or not opponent_tag:
        return None

    return (start_time, clan_tag, opponent_tag)


def safe_number(value, default=0):
    if isinstance(value, (int, float)):
        return value
    return default


def player_record(stats, member):
    tag = member.get("tag") or member.get("name") or "Unknown"
    name = member.get("name") or "Unknown"

    if tag not in stats:
        stats[tag] = {
            "name": name,
            "stars": 0,
            "attacks_used": 0,
            "attacks_missed": 0,
        }
    elif name != "Unknown":
        stats[tag]["name"] = name

    return stats[tag]


def aggregate_wars(wars):
    totals = {
        "wars": 0,
        "wins": 0,
        "losses": 0,
        "ties": 0,
        "possible_attacks": 0,
        "used_attacks": 0,
        "unused_attacks": 0,
        "stars": 0,
        "destruction": 0.0,
        "destruction_count": 0,
        "players": {},
    }

    for _path, war in wars:
        totals["wars"] += 1

        clan = war.get("clan", {})
        opponent = war.get("opponent", {})
        clan_stars = safe_number(clan.get("stars"))
        opponent_stars = safe_number(opponent.get("stars"))

        if clan_stars > opponent_stars:
            totals["wins"] += 1
        elif clan_stars < opponent_stars:
            totals["losses"] += 1
        else:
            totals["ties"] += 1

        destruction = clan.get("destructionPercentage")
        if isinstance(destruction, (int, float)):
            totals["destruction"] += float(destruction)
            totals["destruction_count"] += 1

        attacks_allowed = war.get("attacksPerMember", 2)
        if not isinstance(attacks_allowed, int):
            attacks_allowed = 2

        members = clan.get("members", [])
        if not isinstance(members, list):
            members = []

        for member in members:
            record = player_record(totals["players"], member)
            attacks = member.get("attacks", [])
            if not isinstance(attacks, list):
                attacks = []

            used_attacks = len(attacks)
            missed_attacks = max(0, attacks_allowed - used_attacks)

            totals["possible_attacks"] += attacks_allowed
            totals["used_attacks"] += used_attacks
            totals["unused_attacks"] += missed_attacks

            record["attacks_used"] += used_attacks
            record["attacks_missed"] += missed_attacks

            for attack in attacks:
                stars = safe_number(attack.get("stars"))
                totals["stars"] += stars
                record["stars"] += stars

    return totals


def filter_recent_wars(loaded_wars, days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen = set()
    filtered = []

    for path, war in loaded_wars:
        key = war_key(war)
        if not key:
            continue
        if key in seen:
            continue

        started_at = war_start_time(war)
        if started_at is None or started_at < cutoff:
            continue

        seen.add(key)
        filtered.append((path, war))

    return filtered


def format_percent(numerator, denominator):
    if denominator <= 0:
        return "0%"
    return f"{round((numerator / denominator) * 100)}%"


def top_players(players, field, limit=3):
    ranked = sorted(
        players.values(),
        key=lambda player: (-player[field], player["name"].lower()),
    )
    return [player for player in ranked if player[field] > 0][:limit]


def build_notes(totals, usage_percent):
    notes = []

    if usage_percent >= 95:
        notes.append("Strong attack usage overall")
    elif usage_percent >= 85:
        notes.append("Good attack usage, with some missed attacks to clean up")
    else:
        notes.append("Attack usage needs attention")

    repeat_misses = [player for player in totals["players"].values() if player["attacks_missed"] >= 2]
    if repeat_misses:
        notes.append("A few repeat missed attacks to address")
    else:
        notes.append("No major missed-attack pattern this period")

    return notes


def build_report(totals, days):
    if totals["wars"] == 0:
        return "No war data available for the selected period."

    usage_percent_number = (
        (totals["used_attacks"] / totals["possible_attacks"]) * 100
        if totals["possible_attacks"]
        else 0
    )
    usage_percent = format_percent(totals["used_attacks"], totals["possible_attacks"])
    average_stars = totals["stars"] / totals["wars"]
    average_destruction = (
        totals["destruction"] / totals["destruction_count"]
        if totals["destruction_count"]
        else None
    )

    record = f"{totals['wins']}W - {totals['losses']}L"
    if totals["ties"]:
        record = f"{record} - {totals['ties']}T"

    lines = [
        "📊 Weekly War Report",
        "",
        f"Period: Last {days} days",
        f"Wars: {totals['wars']}",
        f"Record: {record}",
        "",
        f"Total Attacks: {totals['used_attacks']}",
        f"Unused Attacks: {totals['unused_attacks']}",
        f"Attack Usage: {usage_percent}",
        "",
        f"Total Stars: {totals['stars']}",
        f"Average Stars per War: {average_stars:.1f}",
    ]

    if average_destruction is not None:
        lines.append(f"Average Destruction: {average_destruction:.1f}%")

    lines.extend(["", "Top Performers:"])
    performers = top_players(totals["players"], "stars", limit=3)
    if performers:
        for index, player in enumerate(performers, start=1):
            lines.append(f"{index}. {player['name']} — {player['stars']}⭐")
    else:
        lines.append("None")

    lines.extend(["", "Missed Attacks:"])
    missed_players = top_players(totals["players"], "attacks_missed", limit=10)
    if missed_players:
        for player in missed_players:
            count = player["attacks_missed"]
            attack_label = "attack" if count == 1 else "attacks"
            lines.append(f"{player['name']} — missed {count} {attack_label}")
    else:
        lines.append("None")

    lines.extend(["", "Notes:"])
    for note in build_notes(totals, usage_percent_number):
        lines.append(f"- {note}")

    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate a weekly war report from saved snapshots.")
    parser.add_argument("--days", type=int, help="Number of recent days to include.")
    parser.add_argument("--data-dir", default=DEFAULT_WAR_RESULTS_DIR, help="Directory containing war result JSON files.")
    return parser.parse_args()


def main():
    args = parse_args()
    days = args.days if args.days and args.days > 0 else env_int("REPORT_DAYS", DEFAULT_REPORT_DAYS)

    loaded_wars = load_war_files(args.data_dir)
    recent_wars = filter_recent_wars(loaded_wars, days)
    totals = aggregate_wars(recent_wars)
    print(build_report(totals, days))


if __name__ == "__main__":
    main()

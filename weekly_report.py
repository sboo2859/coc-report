import argparse
import glob
import html
import json
import os
from datetime import datetime, timedelta, timezone

from schedule_war_snapshot import parse_coc_time


DEFAULT_REPORT_DAYS = 7
DEFAULT_WAR_RESULTS_DIR = "data/war_results"
DEFAULT_SITE_OUTPUT_DIR = "site_output"


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


def generate_weekly_report_text(days=None, data_dir=DEFAULT_WAR_RESULTS_DIR):
    report_days = days if days and days > 0 else env_int("REPORT_DAYS", DEFAULT_REPORT_DAYS)
    loaded_wars = load_war_files(data_dir)
    recent_wars = filter_recent_wars(loaded_wars, report_days)
    totals = aggregate_wars(recent_wars)
    return build_report(totals, report_days), report_days


def build_report_html(report_text, days, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc)
    escaped_report = html.escape(report_text)
    generated_text = html.escape(generated_at.strftime("%Y-%m-%d %H:%M:%S UTC"))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CoC Weekly Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fa;
      --text: #111827;
      --muted: #5b6472;
      --card: #ffffff;
      --border: #d9e0ea;
      --accent: #0f766e;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    main {{
      max-width: 860px;
      margin: 0 auto;
      padding: 32px 16px;
    }}
    header {{
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 2rem;
      line-height: 1.15;
    }}
    .meta {{
      margin: 0;
      color: var(--muted);
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 24px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: inherit;
      line-height: 1.55;
    }}
    footer {{
      margin-top: 16px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .period {{
      color: var(--accent);
      font-weight: 700;
    }}
    @media (max-width: 560px) {{
      main {{
        padding: 24px 12px;
      }}
      h1 {{
        font-size: 1.6rem;
      }}
      .card {{
        padding: 18px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Clash of Clans Weekly Report</h1>
      <p class="meta">Generated: {generated_text}</p>
      <p class="meta">Report period: <span class="period">Last {days} days</span></p>
    </header>
    <section class="card" aria-label="Weekly report text">
      <pre>{escaped_report}</pre>
    </section>
    <footer>
      Report generated from local saved war snapshots.
    </footer>
  </main>
</body>
</html>
"""


def write_site(report_text, days, output_dir=DEFAULT_SITE_OUTPUT_DIR):
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "index.html")
    with open(output_path, "w") as f:
        f.write(build_report_html(report_text, days))
    return output_path


def parse_args():
    parser = argparse.ArgumentParser(description="Generate a weekly war report from saved snapshots.")
    parser.add_argument("--days", type=int, help="Number of recent days to include.")
    parser.add_argument("--data-dir", default=DEFAULT_WAR_RESULTS_DIR, help="Directory containing war result JSON files.")
    parser.add_argument("--site", action="store_true", help="Write site_output/index.html.")
    parser.add_argument("--output-dir", default=DEFAULT_SITE_OUTPUT_DIR, help="Directory for --site output.")
    return parser.parse_args()


def main():
    args = parse_args()
    report_text, days = generate_weekly_report_text(days=args.days, data_dir=args.data_dir)

    if args.site:
        output_path = write_site(report_text, days, output_dir=args.output_dir)
        print(f"Wrote static report site: {output_path}")
    else:
        print(report_text)


if __name__ == "__main__":
    main()

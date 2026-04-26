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

    if totals["wars"] == 0:
        return ["No completed war snapshots found for this report period"]

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


def report_summary(totals):
    usage_percent_number = (
        (totals["used_attacks"] / totals["possible_attacks"]) * 100
        if totals["possible_attacks"]
        else 0
    )
    usage_percent = format_percent(totals["used_attacks"], totals["possible_attacks"])
    average_stars = totals["stars"] / totals["wars"] if totals["wars"] else 0
    average_destruction = (
        totals["destruction"] / totals["destruction_count"]
        if totals["destruction_count"]
        else None
    )

    record = f"{totals['wins']}W - {totals['losses']}L"
    if totals["ties"]:
        record = f"{record} - {totals['ties']}T"

    return {
        "usage_percent_number": usage_percent_number,
        "usage_percent": usage_percent,
        "average_stars": average_stars,
        "average_destruction": average_destruction,
        "record": record,
    }


def build_report(totals, days):
    if totals["wars"] == 0:
        return "No war data available for the selected period."

    summary = report_summary(totals)

    lines = [
        "📊 Weekly War Report",
        "",
        f"Period: Last {days} days",
        f"Wars: {totals['wars']}",
        f"Record: {summary['record']}",
        "",
        f"Total Attacks: {totals['used_attacks']}",
        f"Unused Attacks: {totals['unused_attacks']}",
        f"Attack Usage: {summary['usage_percent']}",
        "",
        f"Total Stars: {totals['stars']}",
        f"Average Stars per War: {summary['average_stars']:.1f}",
    ]

    if summary["average_destruction"] is not None:
        lines.append(f"Average Destruction: {summary['average_destruction']:.1f}%")

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
    for note in build_notes(totals, summary["usage_percent_number"]):
        lines.append(f"- {note}")

    return "\n".join(lines)


def generate_weekly_report_data(days=None, data_dir=DEFAULT_WAR_RESULTS_DIR):
    report_days = days if days and days > 0 else env_int("REPORT_DAYS", DEFAULT_REPORT_DAYS)
    loaded_wars = load_war_files(data_dir)
    recent_wars = filter_recent_wars(loaded_wars, report_days)
    totals = aggregate_wars(recent_wars)
    summary = report_summary(totals)

    return {
        "days": report_days,
        "recent_wars": recent_wars,
        "totals": totals,
        "summary": summary,
        "notes": build_notes(totals, summary["usage_percent_number"]),
        "report_text": build_report(totals, report_days),
    }


def generate_weekly_report_text(days=None, data_dir=DEFAULT_WAR_RESULTS_DIR):
    report_data = generate_weekly_report_data(days=days, data_dir=data_dir)
    return report_data["report_text"], report_data["days"]


def text_or_default(value, default="Unknown"):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def format_war_date(war):
    started_at = war_start_time(war)
    if started_at is None:
        return "Unknown date"
    return f"{started_at.strftime('%b')} {started_at.day}, {started_at.year}"


def war_result_label(war):
    clan = war.get("clan", {})
    opponent = war.get("opponent", {})
    clan_stars = safe_number(clan.get("stars"))
    opponent_stars = safe_number(opponent.get("stars"))

    if clan_stars > opponent_stars:
        return "Win"
    if clan_stars < opponent_stars:
        return "Loss"
    return "Tie"


def render_stat_cards(report_data):
    totals = report_data["totals"]
    summary = report_data["summary"]
    possible_attacks = totals["possible_attacks"]
    used_attacks = totals["used_attacks"]
    attack_detail = (
        f"{used_attacks} of {possible_attacks} used"
        if possible_attacks
        else "No attacks tracked"
    )
    cards = [
        ("Wars", totals["wars"], f"Last {report_data['days']} days"),
        ("Record", summary["record"], "Wins - losses - ties"),
        ("Attack Usage", summary["usage_percent"], attack_detail),
        ("Unused Attacks", totals["unused_attacks"], "Missed available attacks"),
        ("Total Stars", totals["stars"], f"{summary['average_stars']:.1f} per war"),
    ]

    if summary["average_destruction"] is not None:
        cards.append(
            (
                "Average Destruction",
                f"{summary['average_destruction']:.1f}%",
                "Across completed wars",
            )
        )

    rendered_cards = []
    for label, value, detail in cards:
        rendered_cards.append(
            f"""
      <article class="stat-card">
        <p class="stat-label">{html.escape(str(label))}</p>
        <p class="stat-value">{html.escape(str(value))}</p>
        <p class="stat-detail">{html.escape(str(detail))}</p>
      </article>"""
        )
    return "\n".join(rendered_cards)


def render_player_list(players, field, empty_text, formatter, limit=5):
    ranked_players = top_players(players, field, limit=limit)
    if not ranked_players:
        return f'<p class="empty">{html.escape(empty_text)}</p>'

    items = []
    for player in ranked_players:
        name = html.escape(text_or_default(player.get("name")))
        detail = html.escape(formatter(player))
        items.append(
            f"""
        <li>
          <span>{name}</span>
          <strong>{detail}</strong>
        </li>"""
        )
    return f'<ol class="rank-list">\n{"".join(items)}\n      </ol>'


def render_recent_wars(wars):
    if not wars:
        return '<p class="empty">No completed wars are available for this report period.</p>'

    rows = []
    fallback_date = datetime.min.replace(tzinfo=timezone.utc)
    sorted_wars = sorted(
        wars,
        key=lambda item: war_start_time(item[1]) or fallback_date,
        reverse=True,
    )

    for _path, war in sorted_wars[:5]:
        clan = war.get("clan", {})
        opponent = war.get("opponent", {})
        opponent_name = text_or_default(opponent.get("name"), "Opponent")
        clan_stars = safe_number(clan.get("stars"))
        opponent_stars = safe_number(opponent.get("stars"))
        destruction = clan.get("destructionPercentage")
        destruction_text = f"{destruction:.1f}%" if isinstance(destruction, (int, float)) else "N/A"

        rows.append(
            f"""
        <tr>
          <td>{html.escape(format_war_date(war))}</td>
          <td>{html.escape(opponent_name)}</td>
          <td><span class="result">{html.escape(war_result_label(war))}</span></td>
          <td>{html.escape(str(clan_stars))}-{html.escape(str(opponent_stars))}</td>
          <td>{html.escape(destruction_text)}</td>
        </tr>"""
        )

    return f"""
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Opponent</th>
              <th>Result</th>
              <th>Stars</th>
              <th>Destruction</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}
          </tbody>
        </table>
      </div>"""


def render_notes(notes):
    if not notes:
        return '<p class="empty">No notes for this report period.</p>'

    items = [f"<li>{html.escape(note)}</li>" for note in notes]
    return f'<ul class="note-list">{"".join(items)}</ul>'


def build_report_html(report_text, days, generated_at=None, report_data=None):
    generated_at = generated_at or datetime.now(timezone.utc)
    if report_data is None:
        empty_totals = aggregate_wars([])
        report_data = {
            "days": days,
            "recent_wars": [],
            "totals": empty_totals,
            "summary": report_summary(empty_totals),
            "notes": ["No completed war snapshots found for this report period"],
            "report_text": report_text,
        }

    escaped_report = html.escape(report_text)
    generated_text = html.escape(generated_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    period_text = html.escape(f"Last {days} days")
    stat_cards = render_stat_cards(report_data)
    top_performers = render_player_list(
        report_data["totals"]["players"],
        "stars",
        "No top performers yet.",
        lambda player: f"{player['stars']} stars",
    )
    missed_attacks = render_player_list(
        report_data["totals"]["players"],
        "attacks_missed",
        "No missed attacks tracked.",
        lambda player: (
            f"{player['attacks_missed']} missed attack"
            if player["attacks_missed"] == 1
            else f"{player['attacks_missed']} missed attacks"
        ),
        limit=10,
    )
    recent_wars = render_recent_wars(report_data["recent_wars"])
    notes = render_notes(report_data["notes"])

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CoC Weekly Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3f6f8;
      --text: #17202a;
      --muted: #657182;
      --card: #ffffff;
      --border: #d8e0e7;
      --accent: #0b6b61;
      --accent-soft: #e4f3f0;
      --shadow: 0 10px 24px rgba(23, 32, 42, 0.08);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      background: linear-gradient(180deg, #eaf1f4 0, var(--bg) 280px);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 32px 16px 40px;
    }}
    header {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(1.8rem, 4vw, 3rem);
      line-height: 1.15;
    }}
    h2 {{
      margin: 0;
      font-size: 1.05rem;
      line-height: 1.25;
    }}
    .meta {{
      margin: 0;
      color: var(--muted);
    }}
    .header-meta {{
      display: grid;
      gap: 6px;
      min-width: 220px;
      text-align: right;
    }}
    .period {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #badbd5;
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }}
    .stat-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    .stat-card,
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .stat-card {{
      min-height: 130px;
      padding: 18px;
    }}
    .stat-label,
    .stat-detail {{
      margin: 0;
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .stat-value {{
      margin: 8px 0;
      font-size: 2rem;
      font-weight: 800;
      letter-spacing: 0;
      line-height: 1.1;
    }}
    .dashboard-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .card {{
      padding: 20px;
    }}
    .wide {{
      grid-column: 1 / -1;
    }}
    .section-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .section-kicker {{
      margin: 0;
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .rank-list,
    .note-list {{
      margin: 0;
      padding-left: 20px;
    }}
    .rank-list li,
    .note-list li {{
      padding: 10px 0;
      border-top: 1px solid var(--border);
    }}
    .rank-list li:first-child,
    .note-list li:first-child {{
      border-top: 0;
    }}
    .rank-list li {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    .rank-list strong {{
      white-space: nowrap;
    }}
    .empty {{
      margin: 0;
      color: var(--muted);
    }}
    .table-wrap {{
      width: 100%;
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 620px;
    }}
    th,
    td {{
      border-top: 1px solid var(--border);
      padding: 11px 10px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
    }}
    .result {{
      display: inline-flex;
      border-radius: 999px;
      padding: 3px 8px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
      font-size: 0.9rem;
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
    .report-card {{
      margin-top: 16px;
    }}
    @media (max-width: 900px) {{
      header {{
        display: block;
      }}
      .header-meta {{
        margin-top: 14px;
        text-align: left;
      }}
      .stat-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .dashboard-grid {{
        grid-template-columns: 1fr;
      }}
    }}
    @media (max-width: 560px) {{
      main {{
        padding: 24px 12px;
      }}
      h1 {{
        font-size: 1.6rem;
      }}
      .card,
      .stat-card {{
        padding: 18px;
      }}
      .stat-grid {{
        grid-template-columns: 1fr;
      }}
      .stat-card {{
        min-height: auto;
      }}
      .rank-list li {{
        display: block;
      }}
      .rank-list strong {{
        display: block;
        margin-top: 4px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Clash of Clans Weekly Report</h1>
        <p class="meta">Static dashboard generated from saved war snapshots.</p>
      </div>
      <div class="header-meta" aria-label="Report metadata">
        <p class="meta">Generated: {generated_text}</p>
        <p class="meta">Report period: <span class="period">{period_text}</span></p>
      </div>
    </header>

    <section class="stat-grid" aria-label="Report statistics">
{stat_cards}
    </section>

    <section class="dashboard-grid" aria-label="Report details">
      <article class="card">
        <div class="section-head">
          <h2>Top Performers</h2>
          <p class="section-kicker">By stars</p>
        </div>
        {top_performers}
      </article>

      <article class="card">
        <div class="section-head">
          <h2>Missed Attacks</h2>
          <p class="section-kicker">Needs follow-up</p>
        </div>
        {missed_attacks}
      </article>

      <article class="card wide">
        <div class="section-head">
          <h2>Recent Wars</h2>
          <p class="section-kicker">Latest completed snapshots</p>
        </div>
        {recent_wars}
      </article>

      <article class="card wide">
        <div class="section-head">
          <h2>Notes</h2>
        </div>
        {notes}
      </article>
    </section>

    <section class="card report-card" aria-label="Copy paste report text">
      <div class="section-head">
        <h2>Copy/Paste Report</h2>
      </div>
      <pre>{escaped_report}</pre>
    </section>
    <footer>
      Report generated from local saved war snapshots.
    </footer>
  </main>
</body>
</html>
"""


def write_site(report_text, days, output_dir=DEFAULT_SITE_OUTPUT_DIR, report_data=None):
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "index.html")
    with open(output_path, "w") as f:
        f.write(build_report_html(report_text, days, report_data=report_data))
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
    report_data = generate_weekly_report_data(days=args.days, data_dir=args.data_dir)
    report_text = report_data["report_text"]
    days = report_data["days"]

    if args.site:
        output_path = write_site(report_text, days, output_dir=args.output_dir, report_data=report_data)
        print(f"Wrote static report site: {output_path}")
    else:
        print(report_text)


if __name__ == "__main__":
    main()

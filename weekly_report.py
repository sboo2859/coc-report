import argparse
import glob
import html
import json
import math
import os
from datetime import datetime, timedelta, timezone

from schedule_war_snapshot import parse_coc_time
from war_warning_message import pluralize_attack

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


DEFAULT_REPORT_DAYS = 7
DEFAULT_WAR_RESULTS_DIR = "data/war_results"
DEFAULT_SITE_OUTPUT_DIR = "site_output"
CENTRAL_TIMEZONE_NAME = "America/Chicago"


def central_timezone():
    if ZoneInfo is None:
        return timezone.utc

    try:
        return ZoneInfo(CENTRAL_TIMEZONE_NAME)
    except Exception:
        return timezone.utc


def format_display_datetime(value):
    if value is None:
        return "Unavailable"

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    local_value = value.astimezone(central_timezone())
    hour_text = local_value.strftime("%I").lstrip("0") or "0"
    return f"{local_value.strftime('%b')} {local_value.day}, {local_value.year} {hour_text}:{local_value.strftime('%M %p %Z')}"


def parse_optional_coc_time(value):
    if not value:
        return None

    try:
        return parse_coc_time(value)
    except ValueError:
        return None


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
    return parse_optional_coc_time(start_time)


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


def optional_number(value):
    return value if isinstance(value, (int, float)) else None


def get_attacks_allowed(war):
    attacks_allowed = war.get("attacksPerMember", 2)
    if not isinstance(attacks_allowed, int) or attacks_allowed <= 0:
        return 2
    return attacks_allowed


def member_attacks(member):
    attacks = member.get("attacks", [])
    if not isinstance(attacks, list):
        return []
    return attacks


def player_record(stats, member):
    tag = member.get("tag") or member.get("name") or "Unknown"
    name = member.get("name") or "Unknown"

    if tag not in stats:
        stats[tag] = {
            "name": name,
            "tag": tag,
            "role": text_or_default(member.get("role"), "N/A"),
            "town_hall": member.get("townHallLevel"),
            "trophies": member.get("trophies"),
            "donations": member.get("donations"),
            "donations_received": member.get("donationsReceived"),
            "first_donations": optional_number(member.get("donations")),
            "last_donations": optional_number(member.get("donations")),
            "first_donations_received": optional_number(member.get("donationsReceived")),
            "last_donations_received": optional_number(member.get("donationsReceived")),
            "wars_participated": 0,
            "possible_attacks": 0,
            "stars": 0,
            "attacks_used": 0,
            "attacks_missed": 0,
            "destruction": 0.0,
            "destruction_count": 0,
        }
    elif name != "Unknown":
        stats[tag]["name"] = name

    for field in ("role", "townHallLevel", "trophies", "donations", "donationsReceived"):
        value = member.get(field)
        if value is None:
            continue
        if field == "townHallLevel":
            stats[tag]["town_hall"] = value
        elif field == "donationsReceived":
            stats[tag]["donations_received"] = value
            if isinstance(value, (int, float)):
                if stats[tag]["first_donations_received"] is None:
                    stats[tag]["first_donations_received"] = value
                stats[tag]["last_donations_received"] = value
        elif field == "donations":
            stats[tag]["donations"] = value
            if isinstance(value, (int, float)):
                if stats[tag]["first_donations"] is None:
                    stats[tag]["first_donations"] = value
                stats[tag]["last_donations"] = value
        else:
            stats[tag][field] = value

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
        "war_summaries": [],
    }

    fallback_date = datetime.min.replace(tzinfo=timezone.utc)
    sorted_wars = sorted(wars, key=lambda item: war_start_time(item[1]) or fallback_date)

    for _path, war in sorted_wars:
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

        attacks_allowed = get_attacks_allowed(war)

        members = clan.get("members", [])
        if not isinstance(members, list):
            members = []

        war_used_attacks = 0
        war_possible_attacks = len(members) * attacks_allowed

        for member in members:
            record = player_record(totals["players"], member)
            attacks = member_attacks(member)

            used_attacks = len(attacks)
            missed_attacks = max(0, attacks_allowed - used_attacks)
            war_used_attacks += used_attacks

            totals["possible_attacks"] += attacks_allowed
            totals["used_attacks"] += used_attacks
            totals["unused_attacks"] += missed_attacks

            record["wars_participated"] += 1
            record["possible_attacks"] += attacks_allowed
            record["attacks_used"] += used_attacks
            record["attacks_missed"] += missed_attacks

            for attack in attacks:
                stars = safe_number(attack.get("stars"))
                record["stars"] += stars
                attack_destruction = attack.get("destructionPercentage")
                if isinstance(attack_destruction, (int, float)):
                    record["destruction"] += float(attack_destruction)
                    record["destruction_count"] += 1

        totals["stars"] += clan_stars
        totals["war_summaries"].append(
            {
                "date": format_war_date(war),
                "opponent": text_or_default(opponent.get("name"), "Opponent"),
                "result": war_result_label(war),
                "team_size": len(members),
                "clan_stars": clan_stars,
                "opponent_stars": opponent_stars,
                "clan_destruction": clan.get("destructionPercentage"),
                "opponent_destruction": opponent.get("destructionPercentage"),
                "attacks_used": war_used_attacks,
                "possible_attacks": war_possible_attacks,
                "missed_attacks": max(0, war_possible_attacks - war_used_attacks),
            }
        )

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


def dedupe_wars(loaded_wars):
    seen = set()
    deduped = []

    for path, war in loaded_wars:
        key = war_key(war)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append((path, war))

    return deduped


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
        f"Period: {'All tracked wars' if days == 'all tracked' else f'Last {days} days'}",
        f"Wars: {totals['wars']}",
        f"Record: {summary['record']}",
        "",
        f"Total Attacks: {totals['used_attacks']}",
        f"Possible Attacks: {totals['possible_attacks']}",
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


def generate_history_report_data(data_dir=DEFAULT_WAR_RESULTS_DIR):
    loaded_wars = load_war_files(data_dir)
    history_wars = dedupe_wars(loaded_wars)
    totals = aggregate_wars(history_wars)
    summary = report_summary(totals)

    return {
        "days": None,
        "recent_wars": history_wars,
        "totals": totals,
        "summary": summary,
        "notes": build_notes(totals, summary["usage_percent_number"]),
        "report_text": build_report(totals, "all tracked"),
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
    return format_display_datetime(started_at)


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
    period_detail = "All tracked wars" if report_data.get("days") is None else f"Last {report_data['days']} days"
    cards = [
        ("Members", len(totals["players"]), "Roster members in snapshots"),
        ("Wars", totals["wars"], period_detail),
        ("Record", summary["record"], "Wins - losses - ties"),
        ("Attacks Used", used_attacks, attack_detail),
        ("Possible Attacks", possible_attacks, "Across tracked wars"),
        ("Unused Attacks", totals["unused_attacks"], "Missed available attacks"),
        ("Attack Usage", summary["usage_percent"], "Overall participation rate"),
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


def display_value(value):
    if value is None:
        return "N/A"
    return str(value)


def donation_delta(player, first_field, last_field):
    first_value = player.get(first_field)
    last_value = player.get(last_field)
    if not isinstance(first_value, (int, float)) or not isinstance(last_value, (int, float)):
        return "N/A"
    return str(last_value - first_value)


def player_average_stars(player):
    attacks_used = player.get("attacks_used", 0)
    if not attacks_used:
        return "0.0"
    return f"{player.get('stars', 0) / attacks_used:.1f}"


def player_average_destruction(player):
    count = player.get("destruction_count", 0)
    if not count:
        return "0.0%"
    return f"{player.get('destruction', 0.0) / count:.1f}%"


def render_roster_table(players):
    if not players:
        return '<p class="empty">No roster members found in tracked snapshots.</p>'

    rows = []
    for player in sorted(players.values(), key=lambda item: item["name"].lower()):
        rows.append(
            f"""
        <tr>
          <td>{html.escape(text_or_default(player.get("name")))}</td>
          <td>{html.escape(text_or_default(player.get("tag")))}</td>
          <td>{html.escape(display_value(player.get("role")))}</td>
          <td>{html.escape(display_value(player.get("town_hall")))}</td>
          <td>{html.escape(display_value(player.get("trophies")))}</td>
          <td>{html.escape(display_value(player.get("donations")))}</td>
          <td>{html.escape(display_value(player.get("donations_received")))}</td>
          <td>{html.escape(donation_delta(player, "first_donations", "last_donations"))}</td>
          <td>{html.escape(donation_delta(player, "first_donations_received", "last_donations_received"))}</td>
        </tr>"""
        )

    return f"""
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Tag</th>
              <th>Role</th>
              <th>Town Hall</th>
              <th>Trophies</th>
              <th>Donations</th>
              <th>Received</th>
              <th>7-Day Donation Delta</th>
              <th>7-Day Received Delta</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}
          </tbody>
        </table>
      </div>"""


def render_member_war_performance(players):
    if not players:
        return '<p class="empty">No member war performance available yet.</p>'

    rows = []
    ranked_players = sorted(
        players.values(),
        key=lambda player: (-player.get("stars", 0), -player.get("attacks_used", 0), player["name"].lower()),
    )
    for player in ranked_players:
        rows.append(
            f"""
        <tr>
          <td>{html.escape(text_or_default(player.get("name")))}</td>
          <td>{html.escape(text_or_default(player.get("tag")))}</td>
          <td>{html.escape(str(player.get("attacks_used", 0)))}</td>
          <td>{html.escape(str(player.get("possible_attacks", 0)))}</td>
          <td>{html.escape(str(player.get("attacks_missed", 0)))}</td>
          <td>{html.escape(str(player.get("wars_participated", 0)))}</td>
          <td>{html.escape(str(player.get("stars", 0)))}</td>
          <td>{html.escape(player_average_stars(player))}</td>
          <td>{html.escape(f"{player.get('destruction', 0.0):.1f}%")}</td>
          <td>{html.escape(player_average_destruction(player))}</td>
        </tr>"""
        )

    return f"""
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Tag</th>
              <th>Attacks Used</th>
              <th>Possible</th>
              <th>Missed</th>
              <th>Wars</th>
              <th>Stars</th>
              <th>Avg Stars / Attack</th>
              <th>Total Destruction</th>
              <th>Avg Destruction</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}
          </tbody>
        </table>
      </div>"""


def render_war_summary_table(war_summaries):
    if not war_summaries:
        return '<p class="empty">No completed wars are available for this report period.</p>'

    rows = []
    for war in reversed(war_summaries):
        rows.append(
            f"""
        <tr>
          <td>{html.escape(war["date"])}</td>
          <td>{html.escape(war["opponent"])}</td>
          <td><span class="result">{html.escape(war["result"])}</span></td>
          <td>{html.escape(str(war["team_size"]))}</td>
          <td>{html.escape(str(war["clan_stars"]))}</td>
          <td>{html.escape(str(war["opponent_stars"]))}</td>
          <td>{html.escape(format_decimal_percent(war["clan_destruction"]))}</td>
          <td>{html.escape(format_decimal_percent(war["opponent_destruction"]))}</td>
          <td>{html.escape(str(war["attacks_used"]))}</td>
          <td>{html.escape(str(war["possible_attacks"]))}</td>
          <td>{html.escape(str(war["missed_attacks"]))}</td>
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
              <th>Team Size</th>
              <th>Clan Stars</th>
              <th>Opponent Stars</th>
              <th>Clan Destruction</th>
              <th>Opponent Destruction</th>
              <th>Attacks Used</th>
              <th>Possible</th>
              <th>Missed</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}
          </tbody>
        </table>
      </div>"""


def render_recent_wars(wars):
    return render_war_summary_table(aggregate_wars(wars)["war_summaries"])


def render_notes(notes):
    if not notes:
        return '<p class="empty">No notes for this report period.</p>'

    items = [f"<li>{html.escape(note)}</li>" for note in notes]
    return f'<ul class="note-list">{"".join(items)}</ul>'


def render_nav(active_page):
    links = [
        ("index.html", "Weekly Report", "weekly"),
        ("current-war.html", "Current War", "current"),
        ("history.html", "Total History", "history"),
    ]
    rendered_links = []
    for href, label, page in links:
        class_name = ' class="active"' if active_page == page else ""
        rendered_links.append(f'<a href="{href}"{class_name}>{label}</a>')
    return f'<nav aria-label="Site navigation">{"".join(rendered_links)}</nav>'


def render_site_styles():
    return """
    :root {
      color-scheme: light;
      --bg: #f3f6f8;
      --text: #17202a;
      --muted: #657182;
      --card: #ffffff;
      --border: #d8e0e7;
      --accent: #0b6b61;
      --accent-soft: #e4f3f0;
      --shadow: 0 10px 24px rgba(23, 32, 42, 0.08);
    }
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      background: linear-gradient(180deg, #eaf1f4 0, var(--bg) 280px);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }
    main {
      max-width: 1120px;
      margin: 0 auto;
      padding: 32px 16px 40px;
    }
    nav {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 22px;
    }
    nav a {
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 7px 12px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--text);
      font-weight: 700;
      text-decoration: none;
    }
    nav a.active {
      border-color: #badbd5;
      background: var(--accent-soft);
      color: var(--accent);
    }
    header {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 24px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: clamp(1.8rem, 4vw, 3rem);
      line-height: 1.15;
    }
    h2 {
      margin: 0;
      font-size: 1.05rem;
      line-height: 1.25;
    }
    .meta {
      margin: 0;
      color: var(--muted);
    }
    .header-meta {
      display: grid;
      gap: 6px;
      min-width: 220px;
      text-align: right;
    }
    .period,
    .status-pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #badbd5;
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }
    .stat-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .stat-card,
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .stat-card {
      min-height: 130px;
      padding: 18px;
    }
    .stat-label,
    .stat-detail {
      margin: 0;
      color: var(--muted);
      font-size: 0.88rem;
    }
    .stat-value {
      margin: 8px 0;
      font-size: 2rem;
      font-weight: 800;
      letter-spacing: 0;
      line-height: 1.1;
    }
    .dashboard-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }
    .card {
      padding: 20px;
    }
    .wide {
      grid-column: 1 / -1;
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }
    .section-kicker {
      margin: 0;
      color: var(--muted);
      font-size: 0.88rem;
    }
    .rank-list,
    .note-list {
      margin: 0;
      padding-left: 20px;
    }
    .rank-list li,
    .note-list li {
      padding: 10px 0;
      border-top: 1px solid var(--border);
    }
    .rank-list li:first-child,
    .note-list li:first-child {
      border-top: 0;
    }
    .rank-list li {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .rank-list strong {
      white-space: nowrap;
    }
    .empty {
      margin: 0;
      color: var(--muted);
    }
    .table-wrap {
      width: 100%;
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 620px;
    }
    th,
    td {
      border-top: 1px solid var(--border);
      padding: 11px 10px;
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
    }
    .result {
      display: inline-flex;
      border-radius: 999px;
      padding: 3px 8px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
      font-size: 0.9rem;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: inherit;
      line-height: 1.55;
    }
    footer {
      margin-top: 16px;
      color: var(--muted);
      font-size: 0.95rem;
    }
    .report-card {
      margin-top: 16px;
    }
    @media (max-width: 900px) {
      header {
        display: block;
      }
      .header-meta {
        margin-top: 14px;
        text-align: left;
      }
      .dashboard-grid {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 560px) {
      main {
        padding: 24px 12px;
      }
      h1 {
        font-size: 1.6rem;
      }
      .card,
      .stat-card {
        padding: 18px;
      }
      .stat-card {
        min-height: auto;
      }
      .rank-list li {
        display: block;
      }
      .rank-list strong {
        display: block;
        margin-top: 4px;
      }
    }
"""


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
    generated_text = html.escape(format_display_datetime(generated_at))
    period_text = html.escape("All tracked wars" if days is None else f"Last {days} days")
    page_title = "CoC Total History" if days is None else "CoC Weekly Report"
    heading = "Total War History" if days is None else "Clash of Clans Weekly Report"
    active_page = "history" if days is None else "weekly"
    subtitle = (
        "Static dashboard generated from all saved final war snapshots."
        if days is None
        else "Static dashboard generated from saved war snapshots."
    )
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
    roster_table = render_roster_table(report_data["totals"]["players"])
    war_summary = render_war_summary_table(report_data["totals"]["war_summaries"])
    member_performance = render_member_war_performance(report_data["totals"]["players"])
    notes = render_notes(report_data["notes"])

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)}</title>
  <style>
{render_site_styles()}
  </style>
</head>
<body>
  <main>
    {render_nav(active_page)}
    <header>
      <div>
        <h1>{html.escape(heading)}</h1>
        <p class="meta">{html.escape(subtitle)}</p>
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
          <h2>Full Roster</h2>
          <p class="section-kicker">Includes zero-attack members found in snapshots</p>
        </div>
        {roster_table}
      </article>

      <article class="card wide">
        <div class="section-head">
          <h2>War Summary</h2>
          <p class="section-kicker">All tracked wars in this report</p>
        </div>
        {war_summary}
      </article>

      <article class="card wide">
        <div class="section-head">
          <h2>War Participation by Member</h2>
          <p class="section-kicker">Average destruction uses only attacks actually used</p>
        </div>
        {member_performance}
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


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def format_decimal_percent(value):
    if isinstance(value, (int, float)):
        return f"{float(value):.1f}%"
    return "N/A"


def current_war_attack_summary(war):
    attacks_allowed = safe_int(war.get("attacksPerMember"), 2)
    if attacks_allowed <= 0:
        attacks_allowed = 2

    members = war.get("clan", {}).get("members", [])
    if not isinstance(members, list):
        members = []

    used_attacks = 0
    possible_attacks = 0
    remaining_members = []

    for member in members:
        attacks = member.get("attacks", [])
        if not isinstance(attacks, list):
            attacks = []

        member_used = len(attacks)
        remaining = max(0, attacks_allowed - member_used)
        used_attacks += member_used
        possible_attacks += attacks_allowed

        if remaining:
            remaining_members.append(
                {
                    "name": text_or_default(member.get("name")),
                    "remaining": remaining,
                    "used": member_used,
                }
            )

    remaining_members.sort(key=lambda player: (-player["remaining"], player["name"].lower()))
    return {
        "attacks_allowed": attacks_allowed,
        "used_attacks": used_attacks,
        "possible_attacks": possible_attacks,
        "unused_attacks": sum(player["remaining"] for player in remaining_members),
        "remaining_members": remaining_members,
    }


def format_time_remaining(war, now=None):
    state = war.get("state")
    if state == "warEnded":
        return "War ended"
    if state == "preparation":
        return "Preparation day"
    if state != "inWar":
        return "Unavailable"

    end_time = parse_optional_coc_time(war.get("endTime"))
    if end_time is None:
        return "Unavailable"

    now = now or datetime.now(timezone.utc)
    minutes_left = max(0, math.ceil((end_time - now).total_seconds() / 60))
    if minutes_left < 60:
        return f"{minutes_left}m"

    hours, minutes = divmod(minutes_left, 60)
    return f"{hours}h {minutes}m"


def build_current_war_warning_message(war, attack_summary=None, now=None):
    attack_summary = attack_summary or current_war_attack_summary(war)
    remaining_members = attack_summary["remaining_members"]

    if not remaining_members:
        return "✅ Everyone has used all available attacks."

    time_left = format_time_remaining(war, now=now)
    header = "⚠️ War reminder."
    if war.get("state") == "inWar" and time_left != "Unavailable":
        header = f"⚠️ War reminder — about {time_left} left."

    lines = [header, "", "Still need attacks from:"]
    for player in remaining_members:
        lines.append(f"{player['name']} — {pluralize_attack(player['remaining'])}")

    lines.extend(["", "Please use your attacks before war ends."])
    return "\n".join(lines)


def render_remaining_attacks(remaining_members):
    if not remaining_members:
        return '<p class="empty">Everyone has used all available attacks.</p>'

    items = []
    for player in remaining_members:
        name = html.escape(player["name"])
        detail = html.escape(pluralize_attack(player["remaining"]))
        items.append(
            f"""
        <li>
          <span>{name}</span>
          <strong>{detail}</strong>
        </li>"""
        )

    return f'<ol class="rank-list">\n{"".join(items)}\n      </ol>'


def render_current_war_stat_cards(war, attack_summary, time_left):
    clan = war.get("clan", {})
    opponent = war.get("opponent", {})
    cards = [
        ("War State", text_or_default(war.get("state")), "Current API state"),
        (
            "Stars",
            f"{safe_number(clan.get('stars'))}-{safe_number(opponent.get('stars'))}",
            "Clan - opponent",
        ),
        (
            "Destruction",
            f"{format_decimal_percent(clan.get('destructionPercentage'))} / {format_decimal_percent(opponent.get('destructionPercentage'))}",
            "Clan / opponent",
        ),
        (
            "Attack Usage",
            f"{attack_summary['used_attacks']} / {attack_summary['possible_attacks']}",
            "Used vs possible",
        ),
        ("Unused Attacks", attack_summary["unused_attacks"], "Remaining attacks"),
        ("Time Left", time_left, "Current war timer"),
    ]

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


def build_current_war_html(war=None, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc)
    generated_text = html.escape(format_display_datetime(generated_at))

    if not war:
        title = "Current War"
        subtitle = "Current war data unavailable."
        state_text = "Unavailable"
        stat_cards = render_current_war_stat_cards(
            {"state": "unavailable", "clan": {}, "opponent": {}},
            {
                "used_attacks": 0,
                "possible_attacks": 0,
                "unused_attacks": 0,
                "remaining_members": [],
            },
            "Unavailable",
        )
        war_timing = '<p class="empty">Current war data unavailable.</p>'
        remaining_attacks = '<p class="empty">Current war data unavailable.</p>'
        warning_message = "Current war data unavailable."
    else:
        clan = war.get("clan", {})
        opponent = war.get("opponent", {})
        clan_name = text_or_default(clan.get("name"), "Clan")
        opponent_name = text_or_default(opponent.get("name"), "Opponent")
        title = f"{clan_name} vs {opponent_name}"
        subtitle = "Live current war snapshot captured at build time."
        state_text = text_or_default(war.get("state"))
        attack_summary = current_war_attack_summary(war)
        time_left = format_time_remaining(war, now=generated_at)
        stat_cards = render_current_war_stat_cards(war, attack_summary, time_left)
        start_text = html.escape(format_display_datetime(parse_optional_coc_time(war.get("startTime"))))
        end_text = html.escape(format_display_datetime(parse_optional_coc_time(war.get("endTime"))))
        war_timing = f"""
        <dl>
          <div>
            <dt>Start Time</dt>
            <dd>{start_text}</dd>
          </div>
          <div>
            <dt>End Time</dt>
            <dd>{end_text}</dd>
          </div>
          <div>
            <dt>Time Remaining</dt>
            <dd>{html.escape(time_left)}</dd>
          </div>
        </dl>"""
        remaining_attacks = render_remaining_attacks(attack_summary["remaining_members"])
        warning_message = build_current_war_warning_message(war, attack_summary=attack_summary, now=generated_at)

    escaped_title = html.escape(title)
    escaped_subtitle = html.escape(subtitle)
    escaped_state = html.escape(state_text)
    escaped_warning = html.escape(warning_message)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CoC Current War</title>
  <style>
{render_site_styles()}
    dl {{
      display: grid;
      gap: 12px;
      margin: 0;
    }}
    dl div {{
      display: grid;
      gap: 3px;
      border-top: 1px solid var(--border);
      padding-top: 12px;
    }}
    dl div:first-child {{
      border-top: 0;
      padding-top: 0;
    }}
    dt {{
      color: var(--muted);
      font-size: 0.88rem;
      font-weight: 700;
      text-transform: uppercase;
    }}
    dd {{
      margin: 0;
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <main>
    {render_nav("current")}
    <header>
      <div>
        <h1>Current War</h1>
        <p class="meta">{escaped_subtitle}</p>
      </div>
      <div class="header-meta" aria-label="Current war metadata">
        <p class="meta">Generated: {generated_text}</p>
        <p class="meta">War state: <span class="status-pill">{escaped_state}</span></p>
      </div>
    </header>

    <section class="card report-card" aria-label="Current war matchup">
      <div class="section-head">
        <h2>{escaped_title}</h2>
      </div>
    </section>

    <section class="stat-grid" aria-label="Current war statistics">
{stat_cards}
    </section>

    <section class="dashboard-grid" aria-label="Current war details">
      <article class="card">
        <div class="section-head">
          <h2>War Timing</h2>
        </div>
        {war_timing}
      </article>

      <article class="card">
        <div class="section-head">
          <h2>Members With Attacks</h2>
          <p class="section-kicker">Remaining first</p>
        </div>
        {remaining_attacks}
      </article>
    </section>

    <section class="card report-card" aria-label="Copy paste warning message">
      <div class="section-head">
        <h2>Copy/Paste Warning</h2>
      </div>
      <pre>{escaped_warning}</pre>
    </section>
    <footer>
      Current war page is static and reflects the latest rebuild.
    </footer>
  </main>
</body>
</html>
"""


def write_site(report_text, days, output_dir=DEFAULT_SITE_OUTPUT_DIR, report_data=None):
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "index.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(build_report_html(report_text, days, report_data=report_data))
    return output_path


def write_current_war_site(war=None, output_dir=DEFAULT_SITE_OUTPUT_DIR):
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "current-war.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(build_current_war_html(war=war))
    return output_path


def write_history_site(output_dir=DEFAULT_SITE_OUTPUT_DIR, report_data=None):
    os.makedirs(output_dir, exist_ok=True)
    report_data = report_data or generate_history_report_data()
    output_path = os.path.join(output_dir, "history.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(build_report_html(report_data["report_text"], None, report_data=report_data))
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

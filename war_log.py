"""Reconcile final war snapshots against the clan war log.

A final snapshot is a reading of `currentwar` taken shortly after `endTime`.
The war log is Supercell's record of how the war actually finished. Comparing
the two across 25 saved wars showed the snapshot is not always the final word:

- 19 agreed exactly
- 4 were 1-2 attacks short, because attacks landed in the seconds before the
  war closed and after the snapshot was taken
- 2 recorded zero attacks and zero stars, because the API had already dropped
  or replaced the war by snapshot time (see DECISIONS.md)

So war-level numbers come from the war log wherever an entry matches, and the
snapshot remains the only source of per-member attribution.

The log is cached in `data/state/war_log.json` because the API returns only a
recent window; the cache keeps older wars available as history grows.
"""

import json
import os
from datetime import datetime, timedelta, timezone


DEFAULT_WAR_LOG_FILE = os.environ.get("WAR_LOG_FILE", "data/state/war_log.json")
# Preparation plus battle day is about 47h, so one clan cannot finish two wars
# against the same opponent inside this window. Wide enough to absorb the
# small endTime drift between a snapshot and the log (observed: 1 second).
MATCH_TOLERANCE_HOURS = 24


def parse_coc_timestamp(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%dT%H%M%S.000Z").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def normalize_tag(tag):
    normalized = str(tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def is_regular_war_entry(entry):
    # CWL rounds appear in the war log with no opponent tag and season-level
    # totals. They are not comparable to a regular war snapshot.
    if not isinstance(entry, dict):
        return False
    opponent_tag = normalize_tag((entry.get("opponent") or {}).get("tag"))
    return bool(opponent_tag) and bool(parse_coc_timestamp(entry.get("endTime")))


def entry_identity(entry):
    return (
        normalize_tag((entry.get("opponent") or {}).get("tag")),
        str(entry.get("endTime") or ""),
    )


def load_cached_war_log(path=None):
    path = path or DEFAULT_WAR_LOG_FILE
    if not os.path.exists(path):
        return []

    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    entries = data.get("entries")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def write_cached_war_log(entries, path=None):
    path = path or DEFAULT_WAR_LOG_FILE
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    ordered = sorted(entries, key=lambda item: str(item.get("endTime") or ""))
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump({"entries": ordered}, f, indent=2)
    os.replace(tmp_path, path)
    return path


def merge_war_log_entries(cached, fetched):
    """Union of cached and freshly fetched entries, newest data winning."""
    merged = {}
    for entry in list(cached or []) + list(fetched or []):
        if not is_regular_war_entry(entry):
            continue
        merged[entry_identity(entry)] = entry
    return sorted(merged.values(), key=lambda item: str(item.get("endTime") or ""))


def match_war_log_entry(war, entries, tolerance_hours=MATCH_TOLERANCE_HOURS):
    """Find the log entry for a snapshot by opponent and approximate end time."""
    opponent_tag = normalize_tag((war.get("opponent") or {}).get("tag"))
    war_end = parse_coc_timestamp(war.get("endTime"))
    if not opponent_tag or war_end is None:
        return None

    tolerance = timedelta(hours=tolerance_hours)
    best = None
    best_gap = None
    for entry in entries or []:
        if normalize_tag((entry.get("opponent") or {}).get("tag")) != opponent_tag:
            continue
        entry_end = parse_coc_timestamp(entry.get("endTime"))
        if entry_end is None:
            continue
        gap = abs(entry_end - war_end)
        if gap > tolerance:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = entry, gap

    return best


def snapshot_attacks_used(war):
    total = 0
    for member in (war.get("clan") or {}).get("members") or []:
        attacks = member.get("attacks")
        if isinstance(attacks, list):
            total += len(attacks)
    return total


def safe_int(value, default=0):
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    return default


def reconcile_war(war, entry):
    """War-level truth for one war, plus whether members can be attributed.

    Returns None when there is no log entry, so callers fall back to the
    snapshot unchanged.
    """
    if not entry:
        return None

    clan = entry.get("clan") or {}
    opponent = entry.get("opponent") or {}
    team_size = safe_int(entry.get("teamSize"))
    attacks_per_member = safe_int(entry.get("attacksPerMember"), 2) or 2
    log_attacks = safe_int(clan.get("attacks"))
    snapshot_attacks = snapshot_attacks_used(war)

    # A snapshot that recorded no attacks at all for a war the log says was
    # fought holds no member data worth attributing: crediting every player
    # with a full set of missed attacks would invent them.
    members_usable = snapshot_attacks > 0 or log_attacks == 0

    return {
        "result": entry.get("result"),
        "clan_stars": safe_int(clan.get("stars")),
        "opponent_stars": safe_int(opponent.get("stars")),
        "clan_destruction": clan.get("destructionPercentage"),
        "opponent_destruction": opponent.get("destructionPercentage"),
        "team_size": team_size,
        "attacks_per_member": attacks_per_member,
        "used_attacks": log_attacks,
        "possible_attacks": team_size * attacks_per_member,
        "snapshot_attacks": snapshot_attacks,
        "members_usable": members_usable,
        "attacks_match": snapshot_attacks == log_attacks,
        "opponent_name": opponent.get("name"),
    }


RESULT_LABELS = {"win": "Win", "lose": "Loss", "tie": "Tie"}


def result_label(result):
    return RESULT_LABELS.get(str(result or "").strip().lower())

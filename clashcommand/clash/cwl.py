"""Pure Clan War League (CWL) data helpers.

These are extracted from ``clashcommand.bot`` so that background schedulers
(recaps and reminders) can reuse them without importing the Discord bot module,
which would create a circular import (``bot`` imports the schedulers).

All functions are pure and free of Discord dependencies.
"""

from clashcommand.clash.war import stable_war_key


def normalize_clan_tag(clan_tag):
    normalized = str(clan_tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def cwl_group_war_tags(group):
    tags = []
    rounds = group.get("rounds", [])
    if not isinstance(rounds, list):
        return tags

    for round_index, round_data in enumerate(rounds, start=1):
        war_tags = round_data.get("warTags", [])
        if not isinstance(war_tags, list):
            continue

        for war_tag in war_tags:
            if war_tag and war_tag != "#0":
                tags.append((round_index, war_tag))

    return tags


def cwl_clan_entry(group, clan_tag):
    normalized_tag = normalize_clan_tag(clan_tag)
    clans = group.get("clans", [])
    if not isinstance(clans, list):
        return None

    for clan in clans:
        if normalize_clan_tag(clan.get("tag")) == normalized_tag:
            return clan
    return None


def cwl_war_side(war, clan_tag):
    normalized_tag = normalize_clan_tag(clan_tag)
    for side_name in ("clan", "opponent"):
        side = war.get(side_name, {})
        if normalize_clan_tag(side.get("tag")) == normalized_tag:
            return side_name, side
    return None, None


def cwl_opponent_side(war, clan_tag):
    side_name, our_side = cwl_war_side(war, clan_tag)
    if side_name == "clan":
        return our_side, war.get("opponent", {})
    if side_name == "opponent":
        return our_side, war.get("clan", {})
    return None, None


def cwl_attacks_summary(war, clan_tag):
    our_side, _opponent = cwl_opponent_side(war, clan_tag)
    if not our_side:
        return None

    attacks_allowed = war.get("attacksPerMember", 1)
    if not isinstance(attacks_allowed, int) or attacks_allowed <= 0:
        attacks_allowed = 1

    remaining_members = []
    members = our_side.get("members", [])
    if not isinstance(members, list):
        members = []

    for member in members:
        attacks = member.get("attacks", [])
        if not isinstance(attacks, list):
            attacks = []
        remaining = max(0, attacks_allowed - len(attacks))
        if remaining:
            remaining_members.append(
                {
                    "tag": member.get("tag"),
                    "name": member.get("name") or "Unknown",
                    "used": len(attacks),
                    "remaining": remaining,
                }
            )

    remaining_members.sort(key=lambda player: (-player["remaining"], player["name"].lower()))
    possible_attacks = len(members) * attacks_allowed
    used_attacks = possible_attacks - sum(player["remaining"] for player in remaining_members)

    return {
        "attacks_allowed": attacks_allowed,
        "used_attacks": used_attacks,
        "possible_attacks": possible_attacks,
        "remaining_members": remaining_members,
    }


def cwl_participates(war, clan_tag):
    """True when ``clan_tag`` is on either side of this CWL war.

    CWL snapshots saved by ``schedule_cwl_snapshot.py`` include every ended war
    in the league group, including wars our clan is not part of, so recap logic
    must filter on participation before posting.
    """
    side_name, _side = cwl_war_side(war, clan_tag)
    return side_name is not None


def cwl_war_key(war):
    """Stable dedupe key for a CWL round war.

    Prefers the CWL ``warTag`` (season-unique, saved under ``_cwl`` by the
    snapshot scheduler, or present as ``tag`` on a live war fetched by tag).
    Falls back to the regular stable war key so recaps still dedupe if the tag
    is ever missing.
    """
    cwl_meta = war.get("_cwl")
    if isinstance(cwl_meta, dict):
        war_tag = cwl_meta.get("warTag")
        if war_tag:
            return str(war_tag)

    live_tag = war.get("tag")
    if live_tag:
        return str(live_tag)

    return stable_war_key(war)

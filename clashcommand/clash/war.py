import json

from clashcommand.clash.time import parse_optional_coc_time


DEFAULT_ATTACKS_PER_MEMBER = 2


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_number(value, default=0):
    if isinstance(value, (int, float)):
        return value
    return default


def text_or_default(value, default="Unknown"):
    if value is None:
        return default

    value = str(value).strip()
    return value if value else default


def attacks_per_member(war, default=DEFAULT_ATTACKS_PER_MEMBER):
    allowed = safe_int(war.get("attacksPerMember"), default)
    if allowed <= 0:
        return default
    return allowed


def clan_members(war):
    members = war.get("clan", {}).get("members", [])
    if not isinstance(members, list):
        return []
    return members


def member_attacks(member):
    attacks = member.get("attacks", [])
    if not isinstance(attacks, list):
        return []
    return attacks


def remaining_attack_members(war):
    allowed = attacks_per_member(war)
    remaining_members = []

    for member in clan_members(war):
        used = len(member_attacks(member))
        remaining = max(0, allowed - used)
        if remaining <= 0:
            continue

        remaining_members.append(
            {
                "tag": member.get("tag"),
                "name": text_or_default(member.get("name")),
                "used": used,
                "remaining": remaining,
            }
        )

    return sorted(
        remaining_members,
        key=lambda player: (-player["remaining"], player["name"].lower()),
    )


def current_war_attack_summary(war):
    allowed = attacks_per_member(war)
    members = clan_members(war)
    used_attacks = 0
    possible_attacks = 0

    for member in members:
        used_attacks += len(member_attacks(member))
        possible_attacks += allowed

    remaining_members = remaining_attack_members(war)
    return {
        "attacks_allowed": allowed,
        "used_attacks": used_attacks,
        "possible_attacks": possible_attacks,
        "unused_attacks": sum(player["remaining"] for player in remaining_members),
        "remaining_members": remaining_members,
    }


def current_war_overview(war):
    clan = war.get("clan", {})
    opponent = war.get("opponent", {})
    attack_summary = current_war_attack_summary(war)

    return {
        "state": text_or_default(war.get("state"), "unknown"),
        "preparation_start_time": parse_optional_coc_time(war.get("preparationStartTime")),
        "start_time": parse_optional_coc_time(war.get("startTime")),
        "end_time": parse_optional_coc_time(war.get("endTime")),
        "attacks_per_member": attack_summary["attacks_allowed"],
        "clan": {
            "tag": clan.get("tag"),
            "name": text_or_default(clan.get("name"), "Clan"),
            "stars": safe_number(clan.get("stars")),
            "destruction_percentage": clan.get("destructionPercentage"),
        },
        "opponent": {
            "tag": opponent.get("tag"),
            "name": text_or_default(opponent.get("name"), "Opponent"),
            "stars": safe_number(opponent.get("stars")),
            "destruction_percentage": opponent.get("destructionPercentage"),
        },
        "attack_summary": attack_summary,
    }


def war_key_fields(war):
    return {
        "clan_tag": war.get("clan", {}).get("tag"),
        "opponent_tag": war.get("opponent", {}).get("tag"),
        "preparationStartTime": war.get("preparationStartTime"),
        "startTime": war.get("startTime"),
        "endTime": war.get("endTime"),
    }


def stable_war_key(war):
    fields = war_key_fields(war)
    if not any(fields.values()):
        return None
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))

import json
import os
import sys


SOURCE_PATH = "recovery/2026-05-23-missed-war/latest_current_war.json"
DESTINATION_PATH = "data/war_results/final_war_2026-05-23_19-24.json"


def player_count(war, side):
    members = war.get(side, {}).get("members", [])
    if not isinstance(members, list):
        return 0
    return len(members)


def attack_count(war, side):
    attacks = 0
    members = war.get(side, {}).get("members", [])
    if not isinstance(members, list):
        return 0

    for member in members:
        member_attacks = member.get("attacks", [])
        if isinstance(member_attacks, list):
            attacks += len(member_attacks)
    return attacks


def has_complete_war_payload(war):
    required_top_level = [
        "state",
        "teamSize",
        "attacksPerMember",
        "preparationStartTime",
        "startTime",
        "endTime",
        "clan",
        "opponent",
    ]
    if any(key not in war for key in required_top_level):
        return False

    for side in ("clan", "opponent"):
        side_data = war.get(side)
        if not isinstance(side_data, dict):
            return False
        if not side_data.get("tag") or not side_data.get("name"):
            return False
        members = side_data.get("members")
        if not isinstance(members, list) or not members:
            return False

    return True


def describe_war(war):
    clan = war.get("clan", {})
    opponent = war.get("opponent", {})
    return {
        "state": war.get("state", "unknown"),
        "clan": f"{clan.get('name', 'Unknown')} ({clan.get('tag', 'no tag')})",
        "opponent": f"{opponent.get('name', 'Unknown')} ({opponent.get('tag', 'no tag')})",
        "endTime": war.get("endTime"),
        "clan_members": player_count(war, "clan"),
        "opponent_members": player_count(war, "opponent"),
        "clan_attacks": attack_count(war, "clan"),
        "opponent_attacks": attack_count(war, "opponent"),
    }


def main():
    print(f"Source path: {SOURCE_PATH}")
    print(f"Destination path: {DESTINATION_PATH}")

    if not os.path.exists(SOURCE_PATH):
        print("Recovery source file does not exist; nothing written.")
        return 1

    if os.path.exists(DESTINATION_PATH):
        print("Destination already exists; refusing to overwrite. written=False")
        return 1

    with open(SOURCE_PATH) as f:
        war = json.load(f)

    details = describe_war(war)
    print(f"War state: {details['state']}")
    print(f"Clan: {details['clan']}")
    print(f"Opponent: {details['opponent']}")
    print(f"endTime: {details['endTime']}")
    print(
        "Payload counts: "
        f"clan_members={details['clan_members']} "
        f"opponent_members={details['opponent_members']} "
        f"clan_attacks={details['clan_attacks']} "
        f"opponent_attacks={details['opponent_attacks']}"
    )

    if details["state"] != "inWar":
        print("Recovery source is not an inWar payload; nothing written.")
        return 1

    if not has_complete_war_payload(war):
        print("Recovery source is missing required war data; nothing written.")
        return 1

    os.makedirs(os.path.dirname(DESTINATION_PATH), exist_ok=True)
    with open(DESTINATION_PATH, "w") as f:
        json.dump(war, f, indent=2)

    print(f"Written: True")
    print(f"Saved final war snapshot: {DESTINATION_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

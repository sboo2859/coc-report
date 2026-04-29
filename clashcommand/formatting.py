from clashcommand.clash.war import current_war_overview, remaining_attack_members


def normalize_player_name(name):
    return str(name or "").strip().lower()


def linked_user_for_player(player_name, linked_players):
    normalized_player_name = normalize_player_name(player_name)
    for discord_user_id, linked_player_name in linked_players.items():
        if normalize_player_name(linked_player_name) == normalized_player_name:
            return discord_user_id
    return None


def missed_player_label(player, linked_players):
    discord_user_id = linked_user_for_player(player["name"], linked_players)
    if discord_user_id is not None:
        return f"<@{discord_user_id}>"
    return player["name"]


def missing_attack_lines(war, linked_players=None):
    linked_players = linked_players or {}
    return [
        f"{missed_player_label(player, linked_players)} ({player['remaining']} left)"
        for player in remaining_attack_members(war)
    ]


def build_missed_response(war, linked_players=None):
    overview = current_war_overview(war)
    state = overview["state"]

    if state == "notInWar":
        return f"No active war is currently in progress for `{overview['clan']['name']}`."

    lines = missing_attack_lines(war, linked_players)
    if not lines:
        return "Everyone has used all attacks."

    return "\n".join(["**Players with attacks remaining:**", *[f"- {line}" for line in lines]])

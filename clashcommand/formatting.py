from clashcommand.clash.war import current_war_overview, remaining_attack_members


def normalize_player_name(name):
    return str(name or "").strip().lower()


def normalize_player_tag(player_tag):
    normalized = str(player_tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def linked_player_name(linked_player):
    if isinstance(linked_player, dict):
        return linked_player.get("player_name")
    return linked_player


def linked_player_tag(linked_player):
    if isinstance(linked_player, dict):
        return linked_player.get("player_tag")
    return None


def linked_user_for_player(player_name, linked_players):
    normalized_player_name = normalize_player_name(player_name)
    for discord_user_id, linked_player in linked_players.items():
        if normalize_player_name(linked_player_name(linked_player)) == normalized_player_name:
            return discord_user_id
    return None


def linked_user_for_player_record(player, linked_players):
    player_tag = normalize_player_tag(player.get("tag"))
    if player_tag:
        for discord_user_id, linked_player in linked_players.items():
            if normalize_player_tag(linked_player_tag(linked_player)) == player_tag:
                return discord_user_id

    player_name = player.get("name")
    normalized_player_name = normalize_player_name(player_name)
    for discord_user_id, linked_player in linked_players.items():
        if normalize_player_name(linked_player_name(linked_player)) == normalized_player_name:
            return discord_user_id
    return None


def missed_player_label(player, linked_players):
    discord_user_id = linked_user_for_player_record(player, linked_players)
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

"""Shared war-performance scoring for recap MVP / top-performer ranking.

Ranks a clan's attackers by real impact rather than falling back to
alphabetical order on ties. The ranking key, in descending priority:

1. total stars earned,
2. target difficulty (sum of stars weighted by how strong the base attacked
   was — a 3-star on the enemy #1 base beats a 3-star on the #30),
3. average destruction,
4. a war-seeded deterministic tiebreak (a hash of the war seed + player tag),
   so genuinely identical performances do not always resolve to the same
   (e.g. alphabetically first) player across wars, while any single war's
   recap stays reproducible.

All functions are pure and Discord-free.
"""

import hashlib


def _safe_stars(attack):
    value = attack.get("stars", 0)
    return value if isinstance(value, int) else 0


def _safe_destruction(attack):
    value = attack.get("destructionPercentage")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _text_or_default(value, default="Unknown"):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def _member_attacks(member):
    attacks = member.get("attacks", [])
    if not isinstance(attacks, list):
        return []
    return [attack for attack in attacks if isinstance(attack, dict)]


def defender_strength_by_tag(opponent_members):
    """Map each opponent base tag to a strength score.

    Strength = team_size - mapPosition + 1, so the #1 base scores highest and
    the last base scores 1. Bases without a usable position score 0.
    """
    if not isinstance(opponent_members, list):
        opponent_members = []

    positions = [
        member.get("mapPosition")
        for member in opponent_members
        if isinstance(member.get("mapPosition"), int)
    ]
    team_size = max(positions) if positions else len(opponent_members)

    strength = {}
    for member in opponent_members:
        tag = member.get("tag")
        if not tag:
            continue
        position = member.get("mapPosition")
        if isinstance(position, int) and team_size:
            strength[tag] = max(0, team_size - position + 1)
        else:
            strength[tag] = 0
    return strength


def member_performance(members, opponent_members):
    """Per-attacker stats for one clan side, including target difficulty."""
    if not isinstance(members, list):
        members = []
    strength = defender_strength_by_tag(opponent_members)

    players = []
    for member in members:
        attacks = _member_attacks(member)
        stars = sum(_safe_stars(attack) for attack in attacks)
        destruction_values = [
            _safe_destruction(attack)
            for attack in attacks
            if isinstance(attack.get("destructionPercentage"), (int, float))
        ]
        avg_destruction = (
            sum(destruction_values) / len(destruction_values)
            if destruction_values
            else None
        )
        difficulty_points = sum(
            _safe_stars(attack) * strength.get(attack.get("defenderTag"), 0)
            for attack in attacks
        )
        players.append(
            {
                "tag": member.get("tag"),
                "name": _text_or_default(member.get("name")),
                "attacks": len(attacks),
                "stars": stars,
                "avg_destruction": avg_destruction,
                "difficulty_points": difficulty_points,
                "perfect_attacks": sum(
                    1 for attack in attacks if _safe_stars(attack) == 3
                ),
            }
        )
    return players


def _seed_rank(seed, tag):
    key = f"{seed or ''}:{tag or ''}".encode("utf-8")
    return int(hashlib.sha256(key).hexdigest(), 16)


def performer_sort_key(player, seed):
    return (
        -player["stars"],
        -player["difficulty_points"],
        -(player["avg_destruction"] or 0),
        _seed_rank(seed, player.get("tag")),
    )


def rank_performers(players, seed, limit=None):
    """Sort attackers (who used at least one attack) best-first."""
    ranked = sorted(
        (player for player in players if player["attacks"] > 0),
        key=lambda player: performer_sort_key(player, seed),
    )
    if limit is not None:
        return ranked[:limit]
    return ranked

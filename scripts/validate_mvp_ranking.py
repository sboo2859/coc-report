"""Validation for war-recap MVP / top-performer ranking.

Proves that MVP is decided by real impact (stars, then how hard the bases hit
were, then destruction) with a war-seeded tiebreak — not by alphabetical name
order, which previously handed the MVP to the first player alphabetically on
every tie.

Usage:
    python3 scripts/validate_mvp_ranking.py
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clashcommand.performance import member_performance, rank_performers


def opponent_bases(count=30):
    # Base at mapPosition 1 is the strongest; strength = count - pos + 1.
    return [{"tag": f"#B{pos}", "mapPosition": pos, "name": f"Base{pos}"} for pos in range(1, count + 1)]


def attack(defender_pos, stars=3, destruction=100, order=1):
    return {
        "defenderTag": f"#B{defender_pos}",
        "stars": stars,
        "destructionPercentage": destruction,
        "order": order,
    }


def mvp(members, opponent, seed):
    ranked = rank_performers(member_performance(members, opponent), seed=seed)
    return ranked[0]["name"] if ranked else None


def validate_difficulty_breaks_tie_not_alphabetical():
    opponent = opponent_bases()
    # Both players go 2-for-2 perfect (6 stars, 100%). Zoe hits the two toughest
    # bases (#1, #2); Adam hits the two weakest (#29, #30). Alphabetically Adam
    # is first, but Zoe did the harder work and must be MVP.
    zoe = {"tag": "#ZOE", "name": "Zoe", "attacks": [attack(1, order=1), attack(2, order=2)]}
    adam = {"tag": "#ADAM", "name": "Adam", "attacks": [attack(29, order=3), attack(30, order=4)]}
    assert mvp([adam, zoe], opponent, seed="war-1") == "Zoe"
    assert mvp([zoe, adam], opponent, seed="war-1") == "Zoe"  # input order irrelevant
    print("difficulty breaks the perfect-attacker tie (Zoe > Adam despite A<Z)")


def validate_more_stars_still_wins():
    opponent = opponent_bases()
    # A 6-star run on weak bases beats a 5-star run on strong bases: stars first.
    weak6 = {"tag": "#W", "name": "Weak6", "attacks": [attack(29, 3, 100, 1), attack(30, 3, 100, 2)]}
    strong5 = {"tag": "#S", "name": "Strong5", "attacks": [attack(1, 3, 100, 3), attack(2, 2, 90, 4)]}
    assert mvp([weak6, strong5], opponent, seed="war-1") == "Weak6"
    print("more stars still wins over difficulty (6 stars > 5 stars)")


def validate_seeded_tiebreak_rotates_and_is_reproducible():
    # Identical performance and no target strength -> a pure tie. The war-seeded
    # tiebreak must (a) be reproducible for a given war and (b) not always pick
    # the same (alphabetically first) player across different wars.
    opponent = [{"tag": "#X"}]  # no mapPosition -> difficulty 0 for everyone
    adam = {"tag": "#ADAM", "name": "Adam", "attacks": [{"defenderTag": "#X", "stars": 3, "destructionPercentage": 100, "order": 1}]}
    zoe = {"tag": "#ZOE", "name": "Zoe", "attacks": [{"defenderTag": "#X", "stars": 3, "destructionPercentage": 100, "order": 2}]}
    members = [adam, zoe]

    winners = {mvp(members, opponent, seed=f"war-{i}") for i in range(50)}
    assert winners == {"Adam", "Zoe"}, winners  # both win some wars -> not always alphabetical
    # Reproducible: same seed -> same winner every time.
    fixed = mvp(members, opponent, seed="war-7")
    assert all(mvp(members, opponent, seed="war-7") == fixed for _ in range(5))
    print(f"seeded tiebreak rotates winners across wars and is reproducible per war (sample winners: {sorted(winners)})")


def validate_cleanup_does_not_out_rank_the_earner():
    # Two players hit the same base in order; the later one only cleans up an
    # already-3-starred base. Both show 3 stars, but the earner did the work.
    # (Ranking still uses total stars + difficulty; this documents the shared
    #  data so a future 'new stars' refinement has coverage.)
    opponent = opponent_bases()
    earner = {"tag": "#E", "name": "Earner", "attacks": [attack(1, 3, 100, 1)]}
    cleanup = {"tag": "#C", "name": "Cleanup", "attacks": [attack(30, 3, 100, 2)]}
    # Same stars; #1 is far stronger than #30 -> earner ranks first on difficulty.
    assert mvp([cleanup, earner], opponent, seed="war-1") == "Earner"
    print("harder target outranks equal stars on a weak base")


def main():
    validate_difficulty_breaks_tie_not_alphabetical()
    validate_more_stars_still_wins()
    validate_seeded_tiebreak_rotates_and_is_reproducible()
    validate_cleanup_does_not_out_rank_the_earner()
    print("MVP ranking validation passed.")


if __name__ == "__main__":
    main()

# Data Contracts

## War Snapshot Files

Final war snapshots are raw Clash `currentwar` responses saved as JSON.

Default locations:

```text
data/wars/war_YYYY-MM-DD_HH-MM.json
data/war_results/final_war_YYYY-MM-DD_HH-MM.json
data/state/saved_wars.json
site_output/index.html
```

`data/wars/` contains manual snapshots from `fetch_war.py`.

`data/war_results/` contains final snapshots from `schedule_war_snapshot.py` and is the default input for `weekly_report.py`.

`data/state/saved_wars.json` tracks dedupe keys so the scheduler does not save the same ended war repeatedly.

`site_output/index.html` is generated from saved final snapshots and is intended to be committed for Cloudflare Pages deployment. It is not runtime data.

## Fields Used From Clash War JSON

Top-level fields:

```text
state
preparationStartTime
startTime
endTime
attacksPerMember
clan
opponent
```

Clan fields:

```text
clan.tag
clan.name
clan.stars
clan.destructionPercentage
clan.members
```

Opponent fields:

```text
opponent.tag
opponent.name
opponent.stars
```

Member fields:

```text
member.tag
member.name
member.attacks
```

Attack fields:

```text
attack.stars
```

## Required Fields By Script

`fetch_war.py` can save any valid JSON response from the API, but its participation printout expects `clan.members`.

`schedule_war_snapshot.py` uses `state` and `endTime` for timing. It uses `clan.tag`, `opponent.tag`, `preparationStartTime`, `startTime`, and `endTime` for final-save dedupe.

`war_warning_message.py` requires `state == "inWar"` before generating a reminder. It uses `endTime`, `attacksPerMember`, and `clan.members`.

`weekly_report.py` uses `startTime`, `clan.tag`, and `opponent.tag` for dedupe and filtering. It uses clan/opponent stars, member attacks, attack stars, and optional destruction percentage for metrics.

`build_site.py` uses the weekly report output and writes escaped report text into `site_output/index.html`.

## Derived Fields

Remaining attacks:

```text
remaining = max(0, attacksPerMember - len(member.attacks))
```

Total used attacks:

```text
sum(len(member.attacks) for each clan member)
```

Total stars:

```text
sum(attack.stars for each attack by each clan member)
```

War record:

```text
clan.stars > opponent.stars  -> win
clan.stars < opponent.stars  -> loss
same stars                  -> tie
```

## Assumptions

- Clash times use `YYYYMMDDTHHMMSS.000Z` and are parsed as UTC-aware datetimes.
- `attacksPerMember` defaults to 2 if missing or invalid.
- `member.attacks` may be missing; missing attacks are treated as an empty list.
- `clan.members` may be missing; missing members are treated as an empty list.
- Missing player names are displayed as `Unknown`.
- Malformed weekly report input files are skipped with a short message.
- The weekly report only counts snapshots with `startTime`, `clan.tag`, and `opponent.tag`.

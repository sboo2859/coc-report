import asyncio
import glob
import json
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from clashcommand.clash.war import current_war_overview, member_attacks, stable_war_key


LOGGER = logging.getLogger("clashcommand.post_war_reports")

POST_WAR_REPORT_CHECK_SECONDS = 300
DEFAULT_WAR_RESULTS_DIR = "data/war_results"
POST_WAR_REPORT_REMINDER_TYPE = "post_war_report"


def text_or_default(value, default="Unknown"):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def format_percent(value):
    if isinstance(value, (int, float)):
        return f"{float(value):.1f}%"
    return "N/A"


def normalize_clan_tag(clan_tag):
    normalized = str(clan_tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def load_war_file(path):
    with open(path) as f:
        return json.load(f)


def iter_war_snapshot_paths(data_dir=DEFAULT_WAR_RESULTS_DIR):
    pattern = os.path.join(data_dir, "final_war_*.json")
    return sorted(glob.glob(pattern))


def war_file_mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    except OSError:
        return None


def load_war_snapshots(data_dir=DEFAULT_WAR_RESULTS_DIR):
    snapshots = []
    for path in iter_war_snapshot_paths(data_dir):
        try:
            war = load_war_file(path)
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Skipping unreadable war snapshot %s: %s", path, exc)
            continue
        snapshots.append((path, war))
    return snapshots


def latest_war_snapshot(data_dir=DEFAULT_WAR_RESULTS_DIR, clan_tag=None):
    snapshots = load_war_snapshots(data_dir)
    if clan_tag:
        normalized = normalize_clan_tag(clan_tag)
        snapshots = [
            (path, war)
            for path, war in snapshots
            if normalize_clan_tag(war.get("clan", {}).get("tag")) == normalized
        ]

    if not snapshots:
        return None, None

    return max(
        snapshots,
        key=lambda item: war_file_mtime(item[0]) or datetime.min.replace(tzinfo=timezone.utc),
    )


def war_result_label(overview):
    clan_stars = overview["clan"]["stars"]
    opponent_stars = overview["opponent"]["stars"]

    if clan_stars > opponent_stars:
        return "Win"
    if clan_stars < opponent_stars:
        return "Loss"

    clan_destruction = overview["clan"]["destruction_percentage"]
    opponent_destruction = overview["opponent"]["destruction_percentage"]
    if isinstance(clan_destruction, (int, float)) and isinstance(opponent_destruction, (int, float)):
        if clan_destruction > opponent_destruction:
            return "Win"
        if clan_destruction < opponent_destruction:
            return "Loss"

    return "Tie"


def player_attack_stats(war):
    players = []
    for member in war.get("clan", {}).get("members", []) or []:
        attacks = member_attacks(member)
        stars = sum(attack.get("stars", 0) for attack in attacks if isinstance(attack, dict))
        destruction_values = [
            float(attack["destructionPercentage"])
            for attack in attacks
            if isinstance(attack, dict) and isinstance(attack.get("destructionPercentage"), (int, float))
        ]
        avg_destruction = (
            sum(destruction_values) / len(destruction_values)
            if destruction_values
            else None
        )
        players.append(
            {
                "name": text_or_default(member.get("name")),
                "attacks": len(attacks),
                "stars": stars,
                "avg_destruction": avg_destruction,
                "perfect_attacks": sum(
                    1
                    for attack in attacks
                    if isinstance(attack, dict) and attack.get("stars") == 3
                ),
            }
        )
    return players


def top_performers(war, limit=3):
    players = [player for player in player_attack_stats(war) if player["attacks"] > 0]
    players.sort(
        key=lambda player: (
            -player["stars"],
            -(player["avg_destruction"] or 0),
            player["name"].lower(),
        )
    )
    return players[:limit]


def perfect_attackers(war, limit=5):
    players = [
        player
        for player in player_attack_stats(war)
        if player["perfect_attacks"] > 0
    ]
    players.sort(key=lambda player: (-player["perfect_attacks"], player["name"].lower()))
    return players[:limit]


def build_post_war_report(war, website_url=None):
    overview = current_war_overview(war)
    clan = overview["clan"]
    opponent = overview["opponent"]
    attacks = overview["attack_summary"]
    result = war_result_label(overview)

    lines = [
        f"**War Recap: {result}**",
        f"**{clan['name']} vs {opponent['name']}**",
        f"Final score: `{clan['stars']}-{opponent['stars']}` stars",
        (
            "Destruction: "
            f"`{format_percent(clan['destruction_percentage'])}` / "
            f"`{format_percent(opponent['destruction_percentage'])}`"
        ),
        (
            "Attacks: "
            f"`{attacks['used_attacks']}/{attacks['possible_attacks']}` used, "
            f"`{attacks['unused_attacks']}` missed"
        ),
    ]

    if attacks["remaining_members"]:
        lines.extend(["", "**Missed attacks:**"])
        for player in attacks["remaining_members"][:10]:
            attack_label = "attack" if player["remaining"] == 1 else "attacks"
            lines.append(f"- {player['name']}: {player['remaining']} missed {attack_label}")
        extra_count = len(attacks["remaining_members"]) - 10
        if extra_count > 0:
            lines.append(f"- and {extra_count} more")
    else:
        lines.extend(["", "No missed attacks."])

    performers = top_performers(war)
    if performers:
        lines.extend(["", "**Top performers:**"])
        for player in performers:
            destruction = (
                f", {player['avg_destruction']:.1f}% avg"
                if player["avg_destruction"] is not None
                else ""
            )
            lines.append(f"- {player['name']}: {player['stars']} stars{destruction}")

    perfect = perfect_attackers(war)
    if perfect:
        lines.extend(["", "**Perfect attackers:**"])
        for player in perfect:
            attack_label = "3-star attack" if player["perfect_attacks"] == 1 else "3-star attacks"
            lines.append(f"- {player['name']}: {player['perfect_attacks']} {attack_label}")

    if performers:
        mvp = performers[0]
        lines.extend(["", f"**MVP:** {mvp['name']} with {mvp['stars']} stars."])

    if website_url:
        lines.extend(["", f"Full report: {website_url}"])

    return "\n".join(lines)


class PostWarReportScheduler:
    def __init__(
        self,
        bot,
        interval_seconds=POST_WAR_REPORT_CHECK_SECONDS,
        data_dir=DEFAULT_WAR_RESULTS_DIR,
    ):
        self.bot = bot
        self.interval_seconds = interval_seconds
        self.data_dir = data_dir
        self.scheduler = None
        self.startup_time = datetime.now(timezone.utc)
        self.seen_war_keys = set()

    def start(self):
        if self.scheduler and self.scheduler.running:
            return

        self.mark_existing_wars_seen()
        self.scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self.scheduler.add_job(
            self.check_completed_wars,
            "interval",
            seconds=self.interval_seconds,
            id="post_war_report_check",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        LOGGER.info("Started post-war report scheduler.")

    def shutdown(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            LOGGER.info("Stopped post-war report scheduler.")

    def mark_existing_wars_seen(self):
        for _path, war in load_war_snapshots(self.data_dir):
            key = stable_war_key(war)
            if key:
                self.seen_war_keys.add(key)
        LOGGER.info("Marked %s existing completed war(s) as seen.", len(self.seen_war_keys))

    async def check_completed_wars(self):
        for path, war in load_war_snapshots(self.data_dir):
            key = stable_war_key(war)
            if not key or key in self.seen_war_keys:
                continue

            mtime = war_file_mtime(path)
            if mtime is not None and mtime < self.startup_time:
                self.seen_war_keys.add(key)
                continue

            await self.post_war_to_configured_guilds(key, war)
            self.seen_war_keys.add(key)

    async def post_war_to_configured_guilds(self, war_key, war):
        saved_channels = await asyncio.to_thread(
            self.bot.linked_player_store.reminder_channels
        )
        channel_by_guild = dict(self.bot.command_channels)
        channel_by_guild.update(saved_channels)

        if not channel_by_guild:
            LOGGER.debug("Skipping post-war report; no configured channel is known.")
            return

        for guild_id, channel_id in list(channel_by_guild.items()):
            clan_tag = await self.clan_tag_for_guild(guild_id)
            if not clan_tag:
                continue
            if normalize_clan_tag(war.get("clan", {}).get("tag")) != normalize_clan_tag(clan_tag):
                continue

            already_posted = await asyncio.to_thread(
                self.bot.linked_player_store.has_reminder_event,
                guild_id,
                war_key,
                POST_WAR_REPORT_REMINDER_TYPE,
            )
            if already_posted:
                continue

            sent = await self.send_report(guild_id, channel_id, war_key, war)
            if sent:
                await asyncio.to_thread(
                    self.bot.linked_player_store.record_reminder_event,
                    guild_id,
                    war_key,
                    POST_WAR_REPORT_REMINDER_TYPE,
                )

    async def clan_tag_for_guild(self, guild_id):
        stored_clan_tag = await asyncio.to_thread(
            self.bot.linked_player_store.get_clan_tag,
            guild_id,
        )
        stored_clan_tag = normalize_clan_tag(stored_clan_tag)
        if stored_clan_tag:
            return stored_clan_tag

        fallback_clan_tag = normalize_clan_tag(self.bot.settings.clan_tag)
        return fallback_clan_tag or None

    async def send_report(self, guild_id, channel_id, war_key, war):
        channel = await self.resolve_sendable_channel(channel_id)
        if channel is None:
            LOGGER.warning("Could not resolve post-war report channel for guild %s.", guild_id)
            return False

        website_url = os.environ.get("REPORT_SITE_URL", "").strip()
        message = build_post_war_report(war, website_url=website_url)
        try:
            await channel.send(message)
        except Exception:
            LOGGER.warning("Could not send post-war report to channel %s.", channel_id)
            return False

        LOGGER.info("Posted war recap for guild %s and war %s.", guild_id, war_key)
        return True

    async def resolve_sendable_channel(self, channel_id):
        try:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
        except Exception:
            LOGGER.warning("Could not resolve post-war report channel %s.", channel_id)
            return None

        if hasattr(channel, "send"):
            return channel
        return None

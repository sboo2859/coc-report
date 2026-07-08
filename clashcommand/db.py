import os
import sqlite3
import threading
from datetime import datetime, timezone


def utc_now_text():
    return datetime.now(timezone.utc).isoformat()


class LinkedPlayerStore:
    def __init__(self, db_path):
        self.db_path = db_path
        self._connection = None
        # All access goes through asyncio.to_thread (a thread pool), so the
        # single shared connection is opened with check_same_thread=False and
        # every access is serialized under this lock.
        self._lock = threading.Lock()

    def _connect(self):
        # Callers must hold self._lock.
        if self._connection is None:
            directory = os.path.dirname(self.db_path)
            if directory:
                os.makedirs(directory, exist_ok=True)

            connection = sqlite3.connect(self.db_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            self._connection = connection
        return self._connection

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def initialize(self):
        with self._lock:
            connection = self._connect()
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS linked_players (
                    guild_id TEXT NOT NULL,
                    discord_user_id TEXT NOT NULL,
                    coc_player_name TEXT NOT NULL,
                    player_tag TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, discord_user_id)
                )
                """
            )
            self.ensure_linked_player_columns(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminder_events (
                    guild_id TEXT NOT NULL,
                    war_key TEXT NOT NULL,
                    reminder_type TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, war_key, reminder_type)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id TEXT PRIMARY KEY,
                    reminder_channel_id TEXT,
                    clan_tag TEXT
                )
                """
            )
            connection.commit()

    def ensure_linked_player_columns(self, connection):
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(linked_players)").fetchall()
        }
        if "player_tag" not in columns:
            connection.execute("ALTER TABLE linked_players ADD COLUMN player_tag TEXT")

    def upsert_linked_player(self, guild_id, discord_user_id, coc_player_name, player_tag=None):
        now = utc_now_text()
        with self._lock:
            connection = self._connect()
            connection.execute(
                """
                INSERT INTO linked_players (
                    guild_id,
                    discord_user_id,
                    coc_player_name,
                    player_tag,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, discord_user_id)
                DO UPDATE SET
                    coc_player_name = excluded.coc_player_name,
                    player_tag = excluded.player_tag,
                    updated_at = excluded.updated_at
                """,
                (str(guild_id), str(discord_user_id), coc_player_name, player_tag, now, now),
            )
            connection.commit()

    def linked_players_for_guild(self, guild_id):
        with self._lock:
            connection = self._connect()
            rows = connection.execute(
                """
                SELECT discord_user_id, coc_player_name, player_tag
                FROM linked_players
                WHERE guild_id = ?
                """,
                (str(guild_id),),
            ).fetchall()

        return {
            row["discord_user_id"]: {
                "player_name": row["coc_player_name"],
                "player_tag": row["player_tag"],
            }
            for row in rows
        }

    def linked_player_rows_for_guild(self, guild_id):
        with self._lock:
            connection = self._connect()
            rows = connection.execute(
                """
                SELECT discord_user_id, coc_player_name, player_tag
                FROM linked_players
                WHERE guild_id = ?
                ORDER BY lower(coc_player_name), discord_user_id
                """,
                (str(guild_id),),
            ).fetchall()

        return [
            {
                "discord_user_id": row["discord_user_id"],
                "player_name": row["coc_player_name"],
                "player_tag": row["player_tag"],
            }
            for row in rows
        ]

    def has_reminder_event(self, guild_id, war_key, reminder_type):
        with self._lock:
            connection = self._connect()
            row = connection.execute(
                """
                SELECT 1
                FROM reminder_events
                WHERE guild_id = ?
                    AND war_key = ?
                    AND reminder_type = ?
                LIMIT 1
                """,
                (str(guild_id), war_key, reminder_type),
            ).fetchone()

        return row is not None

    def reminder_types_for_war(self, guild_id, war_key):
        with self._lock:
            connection = self._connect()
            rows = connection.execute(
                """
                SELECT reminder_type
                FROM reminder_events
                WHERE guild_id = ?
                    AND war_key = ?
                """,
                (str(guild_id), war_key),
            ).fetchall()

        return {row["reminder_type"] for row in rows}

    def record_reminder_event(self, guild_id, war_key, reminder_type):
        with self._lock:
            connection = self._connect()
            connection.execute(
                """
                INSERT OR IGNORE INTO reminder_events (
                    guild_id,
                    war_key,
                    reminder_type,
                    sent_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (str(guild_id), war_key, reminder_type, utc_now_text()),
            )
            connection.commit()

    def set_reminder_channel(self, guild_id, channel_id):
        with self._lock:
            connection = self._connect()
            connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id,
                    reminder_channel_id
                )
                VALUES (?, ?)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    reminder_channel_id = excluded.reminder_channel_id
                """,
                (str(guild_id), str(channel_id)),
            )
            connection.commit()

    def get_reminder_channel(self, guild_id):
        with self._lock:
            connection = self._connect()
            row = connection.execute(
                """
                SELECT reminder_channel_id
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (str(guild_id),),
            ).fetchone()

        if row is None:
            return None
        return row["reminder_channel_id"]

    def reminder_channels(self):
        with self._lock:
            connection = self._connect()
            rows = connection.execute(
                """
                SELECT guild_id, reminder_channel_id
                FROM guild_settings
                WHERE reminder_channel_id IS NOT NULL
                    AND reminder_channel_id != ''
                """
            ).fetchall()

        return {
            row["guild_id"]: row["reminder_channel_id"]
            for row in rows
        }

    def guild_settings_map(self):
        """Return {guild_id: {"channel_id": ..., "clan_tag": ...}} for all guilds.

        One query so schedulers can resolve both the reminder channel and the
        clan tag per cycle without a separate get_clan_tag call per guild.
        """
        with self._lock:
            connection = self._connect()
            rows = connection.execute(
                """
                SELECT guild_id, reminder_channel_id, clan_tag
                FROM guild_settings
                """
            ).fetchall()

        return {
            row["guild_id"]: {
                "channel_id": row["reminder_channel_id"],
                "clan_tag": row["clan_tag"],
            }
            for row in rows
        }

    def set_clan_tag(self, guild_id, clan_tag):
        with self._lock:
            connection = self._connect()
            connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id,
                    clan_tag
                )
                VALUES (?, ?)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    clan_tag = excluded.clan_tag
                """,
                (str(guild_id), clan_tag),
            )
            connection.commit()

    def get_clan_tag(self, guild_id):
        with self._lock:
            connection = self._connect()
            row = connection.execute(
                """
                SELECT clan_tag
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (str(guild_id),),
            ).fetchone()

        if row is None:
            return None
        return row["clan_tag"]

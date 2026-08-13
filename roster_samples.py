"""Rolling donation samples for the roster table.

The Clash `/clans/{tag}/members` endpoint reports donation counters as
season-to-date totals, so a single reading cannot answer "how much has this
player donated lately". This module keeps a small time series of readings so the
roster table can show a real 7-day delta instead of a placeholder.

Samples live in `data/state/roster_samples.json`, which is runtime data and is
not committed. The site build writes at most one sample per player per hour and
prunes anything past the retention window, so the file stays small (roughly
50 players x 24 samples/day x 8 days).
"""

import json
import os
from datetime import datetime, timedelta, timezone


DEFAULT_SAMPLE_FILE = os.environ.get(
    "ROSTER_SAMPLE_FILE", "data/state/roster_samples.json"
)
DEFAULT_MIN_INTERVAL_MINUTES = 60
DEFAULT_RETENTION_DAYS = 8
DEFAULT_WINDOW_DAYS = 7


def normalize_tag(tag):
    normalized = str(tag or "").strip().upper()
    if not normalized:
        return ""
    if not normalized.startswith("#"):
        normalized = f"#{normalized}"
    return normalized


def parse_sample_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_roster_samples(path=None):
    path = path or DEFAULT_SAMPLE_FILE
    if not os.path.exists(path):
        return {}

    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        # A corrupt sample file is not worth failing a site build over; the
        # series simply restarts.
        return {}

    samples = data.get("samples")
    if not isinstance(samples, dict):
        return {}
    return samples


def write_roster_samples(samples, path=None):
    path = path or DEFAULT_SAMPLE_FILE
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump({"samples": samples}, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
    return path


def optional_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def prune_samples(samples, now, retention_days=DEFAULT_RETENTION_DAYS):
    cutoff = now - timedelta(days=retention_days)
    pruned = {}
    for tag, entries in samples.items():
        kept = []
        for entry in entries or []:
            sampled_at = parse_sample_time(entry.get("at"))
            if sampled_at is None or sampled_at < cutoff:
                continue
            kept.append(entry)
        if kept:
            kept.sort(key=lambda item: item.get("at") or "")
            pruned[tag] = kept
    return pruned


def record_clan_member_samples(
    members,
    now=None,
    samples=None,
    min_interval_minutes=DEFAULT_MIN_INTERVAL_MINUTES,
    retention_days=DEFAULT_RETENTION_DAYS,
):
    """Add one reading per member, rate-limited to min_interval_minutes."""
    now = now or datetime.now(timezone.utc)
    samples = dict(samples or {})

    for member in members or []:
        tag = normalize_tag(member.get("tag"))
        if not tag:
            continue

        donations = optional_int(member.get("donations"))
        received = optional_int(member.get("donationsReceived"))
        if donations is None and received is None:
            continue

        entries = list(samples.get(tag) or [])
        if entries:
            last_at = parse_sample_time(entries[-1].get("at"))
            if last_at is not None:
                age_minutes = (now - last_at).total_seconds() / 60
                if age_minutes < min_interval_minutes:
                    # Too soon; keep the existing series unchanged.
                    continue

        entries.append(
            {
                "at": now.isoformat(),
                "donations": donations,
                "received": received,
            }
        )
        samples[tag] = entries

    return prune_samples(samples, now, retention_days=retention_days)


def counter_increase(previous, current):
    """Increase between two readings of a counter that resets to zero.

    Donation counters reset each season. A drop means a reset happened, so
    everything currently on the counter accrued since it.
    """
    if previous is None or current is None:
        return 0
    if current < previous:
        return max(0, current)
    return current - previous


def series_delta(entries, field, start):
    """Total increase across the window, plus how many increments were measured.

    The count is of transitions, not readings: one reading on its own proves
    nothing about change, and must not be reported as a delta of zero.
    """
    total = 0
    previous = None
    transitions = 0

    for entry in entries:
        sampled_at = parse_sample_time(entry.get("at"))
        if sampled_at is None:
            continue
        value = optional_int(entry.get(field))
        if value is None:
            continue

        if sampled_at < start:
            # Keep the last reading before the window as the baseline so the
            # first in-window increment is not lost.
            previous = value
            continue

        if previous is not None:
            total += counter_increase(previous, value)
            transitions += 1
        previous = value

    return total, transitions


def donation_deltas(samples, now=None, window_days=DEFAULT_WINDOW_DAYS):
    """Per-tag donation/received increase over the trailing window.

    Returns {tag: {"donations": int, "received": int, "measurements": int}}. A
    tag with no measurable increment is omitted rather than reported as zero,
    so "nothing donated" stays distinguishable from "no history yet".
    """
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=window_days)
    deltas = {}

    for tag, entries in (samples or {}).items():
        ordered = sorted(entries or [], key=lambda item: item.get("at") or "")
        donated, donated_steps = series_delta(ordered, "donations", start)
        received, received_steps = series_delta(ordered, "received", start)
        measurements = max(donated_steps, received_steps)
        if measurements < 1:
            continue
        deltas[normalize_tag(tag)] = {
            "donations": donated,
            "received": received,
            "measurements": measurements,
        }

    return deltas

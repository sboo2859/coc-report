import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clashcommand.reminders import reminder_decision


def assert_decision(seconds_left, sent_keys, expected_key):
    reminder, reason = reminder_decision(seconds_left, sent_keys)
    actual_key = reminder[0] if reminder else None
    assert actual_key == expected_key, (
        f"{seconds_left=} {sent_keys=} expected {expected_key!r}, "
        f"got {actual_key!r} ({reason})"
    )


def main():
    assert_decision((3 * 60 + 10) * 60, set(), "3h")
    assert_decision((3 * 60 + 10) * 60 + 1, set(), None)
    assert_decision((2 * 60 + 45) * 60, set(), "3h")
    assert_decision((2 * 60 + 45) * 60 - 1, set(), None)
    assert_decision(3 * 60 * 60, {"3h"}, None)

    assert_decision((60 + 10) * 60, set(), "1h")
    assert_decision((60 + 10) * 60 + 1, set(), None)
    assert_decision(45 * 60, set(), "1h")
    assert_decision(45 * 60 - 1, set(), None)
    assert_decision(60 * 60, {"1h"}, None)

    assert_decision(3 * 60 * 60, {"1h"}, None)

    print("Reminder window validation passed.")


if __name__ == "__main__":
    main()

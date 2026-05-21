import argparse
import sys

from fetch_war import DEFAULT_CURRENT_WAR_FILE, fetch_current_war, save_latest_current_war


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch current war from Clash API and save the latest site snapshot."
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_CURRENT_WAR_FILE,
        help="Path to write the latest current war JSON snapshot.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        war, status_code = fetch_current_war()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    output_path = save_latest_current_war(war, output_path=args.output)
    print(f"Status Code: {status_code}")
    print(f"Saved latest current war snapshot to {output_path}")


if __name__ == "__main__":
    main()

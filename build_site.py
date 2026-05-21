import argparse

from fetch_war import (
    DEFAULT_CURRENT_WAR_FILE,
    fetch_current_war,
    load_latest_current_war,
    save_latest_current_war,
)
from weekly_report import generate_weekly_report_data, write_current_war_site, write_history_site, write_site


def parse_args():
    parser = argparse.ArgumentParser(description="Build the static CoC report site.")
    parser.add_argument(
        "--include-current-war",
        action="store_true",
        help="Fetch current war data and generate site_output/current-war.html.",
    )
    parser.add_argument(
        "--current-war-file",
        default=DEFAULT_CURRENT_WAR_FILE,
        help="Preferred current war JSON snapshot for site_output/current-war.html.",
    )
    parser.add_argument(
        "--live-current-war-fallback",
        action="store_true",
        help="Call the Clash API if the current war snapshot file is unavailable.",
    )
    return parser.parse_args()


def load_current_war_for_site(input_path):
    try:
        war = load_latest_current_war(input_path)
    except Exception as exc:
        print(f"Current war snapshot unreadable ({input_path}: {exc}); falling back.")
        return None

    if war is None:
        print(f"Current war snapshot not found at {input_path}; falling back.")
        return None

    print(f"Loaded current war snapshot from {input_path}.")
    return war


def fetch_current_war_for_site(output_path=None):
    try:
        war, _status_code = fetch_current_war()
    except Exception as exc:
        print(f"Current war data unavailable ({exc}); writing fallback page.")
        return None

    if output_path:
        try:
            save_latest_current_war(war, output_path=output_path)
            print(f"Saved latest current war snapshot to {output_path}.")
        except Exception as exc:
            print(f"Could not save latest current war snapshot ({output_path}: {exc}).")

    return war


def main():
    args = parse_args()
    report_data = generate_weekly_report_data()
    output_path = write_site(
        report_data["report_text"],
        report_data["days"],
        report_data=report_data,
    )
    print(f"Wrote static report site: {output_path}")

    history_path = write_history_site()
    print(f"Wrote total history site: {history_path}")

    if args.include_current_war:
        current_war = load_current_war_for_site(args.current_war_file)
        if current_war is None and args.live_current_war_fallback:
            current_war = fetch_current_war_for_site(output_path=args.current_war_file)
        elif current_war is None:
            print("Current war data unavailable; writing fallback page.")

        current_war_path = write_current_war_site(war=current_war)
        print(f"Wrote current war site: {current_war_path}")


if __name__ == "__main__":
    main()

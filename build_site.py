import argparse

from fetch_war import fetch_current_war
from weekly_report import generate_weekly_report_data, write_current_war_site, write_site


def parse_args():
    parser = argparse.ArgumentParser(description="Build the static CoC report site.")
    parser.add_argument(
        "--include-current-war",
        action="store_true",
        help="Fetch current war data and generate site_output/current-war.html.",
    )
    return parser.parse_args()


def fetch_current_war_for_site():
    try:
        war, _status_code = fetch_current_war()
    except Exception:
        print("Current war data unavailable; writing fallback page.")
        return None

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

    if args.include_current_war:
        current_war = fetch_current_war_for_site()
        current_war_path = write_current_war_site(war=current_war)
        print(f"Wrote current war site: {current_war_path}")


if __name__ == "__main__":
    main()

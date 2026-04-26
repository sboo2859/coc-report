from weekly_report import generate_weekly_report_text, write_site


def main():
    report_text, days = generate_weekly_report_text()
    output_path = write_site(report_text, days)
    print(f"Wrote static report site: {output_path}")


if __name__ == "__main__":
    main()

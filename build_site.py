from weekly_report import generate_weekly_report_data, write_site


def main():
    report_data = generate_weekly_report_data()
    output_path = write_site(
        report_data["report_text"],
        report_data["days"],
        report_data=report_data,
    )
    print(f"Wrote static report site: {output_path}")


if __name__ == "__main__":
    main()

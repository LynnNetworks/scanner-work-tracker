import csv
from pathlib import Path

import pandas as pd


DEFAULT_SOURCE = Path(r"C:\Users\egenova\Downloads\Fiber Roster.xlsx")
DEFAULT_OUTPUT = Path(__file__).with_name("operators.csv")


def export_roster(source_path=DEFAULT_SOURCE, output_path=DEFAULT_OUTPUT):
    df = pd.read_excel(source_path, sheet_name="Data", dtype=str).fillna("")
    df = df[
        [
            "Position ID",
            "Payroll Name",
            "Benefits Eligibility Class Description",
            "Reports To Name",
            "Position Status",
        ]
    ]
    df = df[df["Position ID"].str.strip() != ""]
    df = df.sort_values(["Payroll Name", "Position ID"])

    rows = [
        {
            "position_id": row["Position ID"].strip(),
            "payroll_name": row["Payroll Name"].strip(),
            "benefits_class": row["Benefits Eligibility Class Description"].strip(),
            "reports_to_name": row["Reports To Name"].strip(),
            "position_status": row["Position Status"].strip(),
        }
        for _, row in df.iterrows()
    ]

    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "position_id",
                "payroll_name",
                "benefits_class",
                "reports_to_name",
                "position_status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    return len(rows), output_path


if __name__ == "__main__":
    count, output = export_roster()
    print(f"Exported {count} operators to {output}")

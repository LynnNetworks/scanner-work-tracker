import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_CSV = Path(__file__).with_name("operator_areas.csv")


def read_assignments(csv_path=DEFAULT_CSV):
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        rows = []
        for row in csv.DictReader(handle):
            position_id = (row.get("position_id") or row.get("Position ID") or "").strip().upper()
            if not position_id:
                continue
            rows.append(
                {
                    "position_id": position_id,
                    "payroll_name": (row.get("payroll_name") or row.get("Payroll Name") or "").strip(),
                    "area": (row.get("area") or row.get("Area") or "").strip().lower(),
                }
            )
        return rows


def sync(endpoint_url, token, csv_path=DEFAULT_CSV):
    assignments = read_assignments(csv_path)
    separator = "&" if "?" in endpoint_url else "?"
    url = f"{endpoint_url}{separator}token={token}"
    body = json.dumps({"assignments": assignments}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Sync failed: HTTP {error.code}: {detail}") from error

    return json.loads(payload or "{}")


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: python sync_operator_areas.py "
            "https://<function-app>.azurewebsites.net/api/operator-areas-sync <token> [operator_areas.csv]",
            file=sys.stderr,
        )
        return 2

    endpoint_url = sys.argv[1]
    token = sys.argv[2]
    csv_path = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_CSV
    result = sync(endpoint_url, token, csv_path)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

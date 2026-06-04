import csv
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def main():
    base_dir = Path(__file__).resolve().parent
    combined_workbook_path = base_dir.parent / "Scanner_Work_Tracker.xlsx"
    legacy_workbook_path = base_dir / "operator_areas.xlsx"
    workbook_path = (
        combined_workbook_path
        if combined_workbook_path.exists()
        else legacy_workbook_path
    )
    csv_path = base_dir / "operator_areas.csv"

    rows = read_sheet_rows(workbook_path, ["Area Assignments", "Assignments"])
    if not rows:
        raise RuntimeError("Assignments sheet is empty.")

    headers = [value.strip().lower() for value in rows[0]]
    required = ["position_id", "payroll_name", "area"]
    if headers[:3] != required:
        raise RuntimeError(f"Expected first columns to be: {', '.join(required)}")

    with csv_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(required)
        for row in rows[1:]:
            row = row + [""] * max(0, 3 - len(row))
            if row[0].strip():
                writer.writerow([row[0].strip(), row[1].strip(), row[2].strip()])


def read_sheet_rows(workbook_path, sheet_names):
    with ZipFile(workbook_path) as workbook_zip:
        shared_strings = read_shared_strings(workbook_zip)
        workbook_root = ET.fromstring(workbook_zip.read("xl/workbook.xml"))
        rels_root = ET.fromstring(workbook_zip.read("xl/_rels/workbook.xml.rels"))

        rel_targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels_root.findall(f"{{{NS_PACKAGE_REL}}}Relationship")
        }
        sheet_path = None
        for sheet in workbook_root.findall(f".//{{{NS_MAIN}}}sheet"):
            if sheet.attrib.get("name") in sheet_names:
                rel_id = sheet.attrib[f"{{{NS_REL}}}id"]
                target = rel_targets[rel_id]
                sheet_path = f"xl/{target}" if not target.startswith("/") else target.lstrip("/")
                break
        if not sheet_path:
            raise RuntimeError(f"Sheet not found: {', '.join(sheet_names)}")

        sheet_root = ET.fromstring(workbook_zip.read(sheet_path))

    rows = []
    for row_element in sheet_root.findall(f".//{{{NS_MAIN}}}sheetData/{{{NS_MAIN}}}row"):
        row_values = []
        last_col = 0
        for cell in row_element.findall(f"{{{NS_MAIN}}}c"):
            col_index = column_index(cell.attrib.get("r", "A1"))
            while last_col + 1 < col_index:
                row_values.append("")
                last_col += 1
            row_values.append(cell_text(cell, shared_strings))
            last_col = col_index
        rows.append(row_values)
    return rows


def read_shared_strings(workbook_zip):
    if "xl/sharedStrings.xml" not in workbook_zip.namelist():
        return []

    root = ET.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall(f"{{{NS_MAIN}}}si"):
        text_parts = [node.text or "" for node in item.findall(f".//{{{NS_MAIN}}}t")]
        values.append("".join(text_parts))
    return values


def cell_text(cell, shared_strings):
    inline = cell.find(f"{{{NS_MAIN}}}is/{{{NS_MAIN}}}t")
    if inline is not None and inline.text is not None:
        return inline.text

    value = cell.find(f"{{{NS_MAIN}}}v")
    if value is None or value.text is None:
        return ""

    if cell.attrib.get("t") == "s":
        return shared_strings[int(value.text)]
    return value.text


def column_index(cell_ref):
    letters = ""
    for char in cell_ref:
        if char.isalpha():
            letters += char.upper()
        else:
            break

    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


if __name__ == "__main__":
    main()

import csv
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


HEADERS = [
    "Created At",
    "Position ID",
    "Payroll Name",
    "Batch ID",
    "Benefits Class",
    "Reports To Name",
    "Position Status",
]

PRODUCTION_AREAS = [
    "cut",
    "prep(reg)",
    "prep(mtp)",
    "prep(288)",
    "prep(flat)",
    "term(reg)",
    "term(mtp)",
    "term(288)",
    "term(flat)",
    "polish(reg)",
    "polish(mtp)",
    "polish(288)",
    "polish(flat)",
    "scope(reg)",
    "scope(mtp)",
    "scope(288)",
    "scope(flat)",
    "test(reg)",
    "test(mtp)",
    "test(288)",
    "test(flat)",
]

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"


class WorkTrackerExcelStore:
    def __init__(self, workbook_path, roster_csv_path, operator_areas_csv_path=None):
        self.workbook_path = Path(workbook_path)
        self.workbook_path.parent.mkdir(parents=True, exist_ok=True)
        self.operators = self._load_operators(roster_csv_path)
        if not self.workbook_path.exists():
            self._write_rows([])

    def _load_operators(self, csv_path):
        csv_path = Path(csv_path)
        if not csv_path.exists():
            return {}

        with csv_path.open(newline="", encoding="utf-8-sig") as csvfile:
            return {
                row["position_id"].strip().upper(): {
                    "position_id": row["position_id"].strip(),
                    "payroll_name": row["payroll_name"].strip(),
                    "benefits_class": row.get("benefits_class", "").strip(),
                    "reports_to_name": row.get("reports_to_name", "").strip(),
                    "position_status": row.get("position_status", "").strip(),
                }
                for row in csv.DictReader(csvfile)
                if row.get("position_id", "").strip()
            }

    def get_operator(self, position_id):
        return self.operators.get(position_id.strip().upper())

    def add_operator(self, operator):
        position_id = str(operator.get("position_id", "")).strip()
        if not position_id:
            return
        self.operators[position_id.upper()] = {
            "position_id": position_id,
            "payroll_name": str(operator.get("payroll_name", "")).strip(),
            "benefits_class": str(operator.get("benefits_class", "")).strip(),
            "reports_to_name": str(operator.get("reports_to_name", "")).strip(),
            "position_status": str(operator.get("position_status", "Active")).strip() or "Active",
        }

    def add_entry(self, position_id, batch_id):
        operator = self.get_operator(position_id)
        if not operator:
            raise ValueError("Position ID was not found in the roster.")

        rows = self._read_rows()
        rows.append(
            [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                operator["position_id"],
                operator["payroll_name"],
                str(int(batch_id)),
                operator["benefits_class"],
                operator["reports_to_name"],
                operator["position_status"],
            ]
        )
        self._write_rows(rows)

    def recent_entries(self, limit=10):
        rows = self._read_rows()
        recent_rows = rows[-limit:][::-1]
        return [
            (row[1], row[2], row[3], row[0])
            for row in recent_rows
            if len(row) >= 4
        ]

    def _read_rows(self):
        if not self.workbook_path.exists():
            return []

        with ZipFile(self.workbook_path) as workbook_zip:
            xml_bytes = workbook_zip.read("xl/worksheets/sheet1.xml")

        root = ET.fromstring(xml_bytes)
        rows = []
        for row_element in root.findall(f".//{{{NS_MAIN}}}sheetData/{{{NS_MAIN}}}row"):
            values = []
            for cell in row_element.findall(f"{{{NS_MAIN}}}c"):
                value = self._cell_text(cell)
                values.append(value)
            rows.append(values)

        if not rows:
            return []

        if rows[0] == HEADERS:
            return rows[1:]

        area_headers = [
            "Created At",
            "Position ID",
            "Payroll Name",
            "Area",
            "Batch ID",
            "Benefits Class",
            "Reports To Name",
            "Position Status",
        ]
        if rows[0] == area_headers:
            return [self._drop_area_from_row(row) for row in rows[1:]]

        return [self._normalize_row(row) for row in rows]

    def _drop_area_from_row(self, row):
        row = row + [""] * max(0, 8 - len(row))
        return [
            row[0],
            row[1],
            row[2],
            row[4],
            row[5],
            row[6],
            row[7],
        ]

    def _normalize_row(self, row):
        row = row + [""] * max(0, len(HEADERS) - len(row))
        return row[: len(HEADERS)]

    def _cell_text(self, cell):
        inline = cell.find(f"{{{NS_MAIN}}}is/{{{NS_MAIN}}}t")
        if inline is not None and inline.text is not None:
            return inline.text
        value = cell.find(f"{{{NS_MAIN}}}v")
        if value is not None and value.text is not None:
            return value.text
        return ""

    def _write_rows(self, rows):
        all_rows = [HEADERS] + rows
        temp_path = self.workbook_path.with_suffix(".tmp.xlsx")
        with ZipFile(temp_path, "w", ZIP_DEFLATED) as workbook_zip:
            workbook_zip.writestr("[Content_Types].xml", self._content_types_xml())
            workbook_zip.writestr("_rels/.rels", self._root_rels_xml())
            workbook_zip.writestr("xl/workbook.xml", self._workbook_xml())
            workbook_zip.writestr("xl/_rels/workbook.xml.rels", self._workbook_rels_xml())
            workbook_zip.writestr("xl/styles.xml", self._styles_xml())
            workbook_zip.writestr("xl/worksheets/sheet1.xml", self._sheet_xml(all_rows))
        temp_path.replace(self.workbook_path)

    def _sheet_xml(self, rows):
        dimension = f"A1:G{max(len(rows), 1)}"
        row_xml = []
        for row_index, row in enumerate(rows, start=1):
            cells = []
            for col_index, value in enumerate(row, start=1):
                cell_ref = f"{self._column_name(col_index)}{row_index}"
                text = self._escape(value)
                cells.append(
                    f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>'
                )
            row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
            f'<dimension ref="{dimension}"/>'
            "<sheetViews><sheetView workbookViewId=\"0\"><pane ySplit=\"1\" "
            'topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft"/></sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="15"/>'
            '<cols><col min="1" max="1" width="20" customWidth="1"/>'
            '<col min="2" max="2" width="16" customWidth="1"/>'
            '<col min="3" max="3" width="28" customWidth="1"/>'
            '<col min="4" max="4" width="12" customWidth="1"/>'
            '<col min="5" max="5" width="22" customWidth="1"/>'
            '<col min="6" max="6" width="24" customWidth="1"/>'
            '<col min="7" max="7" width="16" customWidth="1"/></cols>'
            f'<sheetData>{"".join(row_xml)}</sheetData>'
            "</worksheet>"
        )

    def _content_types_xml(self):
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Types xmlns="{NS_CONTENT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        )

    def _root_rels_xml(self):
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{NS_PACKAGE_REL}">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>"
        )

    def _workbook_xml(self):
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
            "<sheets>"
            '<sheet name="Work Tracker" sheetId="1" r:id="rId1"/>'
            "</sheets>"
            "</workbook>"
        )

    def _workbook_rels_xml(self):
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{NS_PACKAGE_REL}">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
            "</Relationships>"
        )

    def _styles_xml(self):
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{NS_MAIN}">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
            "</styleSheet>"
        )

    def _column_name(self, index):
        name = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            name = chr(65 + remainder) + name
        return name

    def _escape(self, value):
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

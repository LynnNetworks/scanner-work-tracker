from pathlib import Path

from kivy.app import App
from kivy.lang import Builder
from kivy.properties import BooleanProperty, StringProperty
from kivy.utils import platform
from kivy.uix.boxlayout import BoxLayout

try:
    from app_config import CLOUD_ENDPOINT_URL, CLOUD_FUNCTION_KEY, TABLET_ID, READ_ACCESS_TOKEN
except ImportError:
    from app_config import CLOUD_ENDPOINT_URL, CLOUD_FUNCTION_KEY, TABLET_ID
    READ_ACCESS_TOKEN = ""
from cloud_client import CloudClient
from excel_store import WorkTrackerExcelStore


KV = """
<TrackerRoot>:
    orientation: "vertical"
    padding: dp(18)
    spacing: dp(12)

    Label:
        text: "Scanner Work Tracker"
        font_size: "24sp"
        bold: True
        size_hint_y: None
        height: dp(42)

    TextInput:
        id: operator_id
        hint_text: "Position ID"
        multiline: False
        write_tab: False
        size_hint_y: None
        height: dp(52)
        on_text_validate: batch_id.focus = True

    TextInput:
        id: batch_id
        hint_text: "Batch ID"
        multiline: False
        write_tab: False
        input_filter: "int"
        size_hint_y: None
        height: dp(52)
        on_text_validate: root.save_entry()

    Button:
        text: "Save Entry"
        size_hint_y: None
        height: dp(52)
        on_release: root.save_entry()

    Label:
        text: root.status
        color: (0.1, 0.45, 0.15, 1) if root.status_ok else (0.75, 0.12, 0.12, 1)
        size_hint_y: None
        height: dp(34)

    Label:
        text: "Recent Entries"
        bold: True
        size_hint_y: None
        height: dp(30)

    RecycleView:
        id: recent_entries
        viewclass: "Label"
        RecycleBoxLayout:
            default_size: None, dp(30)
            default_size_hint: 1, None
            size_hint_y: None
            height: self.minimum_height
            orientation: "vertical"
"""


class TrackerRoot(BoxLayout):
    status = StringProperty("")
    status_ok = BooleanProperty(True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        app = App.get_running_app()
        self.store = WorkTrackerExcelStore(
            app.workbook_path,
            Path(__file__).with_name("operators.csv"),
        )
        self.cloud = CloudClient(
            CLOUD_ENDPOINT_URL,
            CLOUD_FUNCTION_KEY,
            TABLET_ID,
            READ_ACCESS_TOKEN,
        )
        self.refresh_recent_entries()

    def save_entry(self):
        operator_id = self.ids.operator_id.text.strip()
        batch_id = self.ids.batch_id.text.strip()

        if not operator_id:
            self.set_status("Position ID is required.", False)
            return
        if not batch_id:
            self.set_status("Batch ID is required.", False)
            return
        operator = self.store.get_operator(operator_id)
        if not operator:
            try:
                operator = self.cloud.find_live_operator(operator_id)
            except RuntimeError as error:
                self.set_status(str(error), False)
                return
            if not operator:
                self.set_status("Position ID was not found in the roster.", False)
                return
            self.store.add_operator(operator)
        if operator["position_status"].lower() != "active":
            self.set_status(f"{operator['payroll_name']} is not active.", False)
            return

        try:
            if self.cloud.enabled:
                self.cloud.save_entry(operator, batch_id)
                self.store.add_entry(operator_id, batch_id)
            else:
                self.store.add_entry(operator_id, batch_id)
        except PermissionError:
            self.set_status("Close work_tracker.xlsx, then save again.", False)
            return
        except (OSError, RuntimeError) as error:
            self.set_status(f"Could not save entry: {error}", False)
            return

        self.ids.operator_id.text = ""
        self.ids.batch_id.text = ""
        self.ids.operator_id.focus = True
        self.set_status(f"Entry saved for {operator['payroll_name']}.", True)
        self.refresh_recent_entries()

    def set_status(self, message, ok):
        self.status = message
        self.status_ok = ok

    def refresh_recent_entries(self):
        self.ids.recent_entries.data = [
            {
                "text": (
                    f"{created_at} | {payroll_name or 'Unknown'} "
                    f"({operator_id}) | Batch {batch_id}"
                ),
                "halign": "left",
                "valign": "middle",
            }
            for operator_id, payroll_name, batch_id, created_at in self.store.recent_entries()
        ]


class ScannerWorkTrackerApp(App):
    workbook_path = StringProperty("work_tracker.xlsx")

    def build(self):
        if platform == "android":
            self.workbook_path = str(Path(self.user_data_dir) / "work_tracker.xlsx")
        else:
            self.workbook_path = str(Path(__file__).with_name("work_tracker.xlsx"))
        Builder.load_string(KV)
        return TrackerRoot()


if __name__ == "__main__":
    ScannerWorkTrackerApp().run()

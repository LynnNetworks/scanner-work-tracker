import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import certifi


class CloudClient:
    def __init__(self, endpoint_url="", function_key="", tablet_id="", read_access_token=""):
        self.endpoint_url = endpoint_url.strip()
        self.function_key = function_key.strip()
        self.tablet_id = tablet_id.strip()
        self.read_access_token = read_access_token.strip()

    @property
    def enabled(self):
        return bool(self.endpoint_url)

    def save_entry(self, operator, batch_id):
        url = self.endpoint_url
        if self.function_key:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}code={self.function_key}"

        body = json.dumps(
            {
                "position_id": operator["position_id"],
                "payroll_name": operator["payroll_name"],
                "batch_id": str(batch_id),
                "tablet_id": self.tablet_id,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(request, timeout=15, context=context) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Cloud save failed: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Could not reach cloud endpoint: {error.reason}") from error

        result = json.loads(payload or "{}")
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Cloud save failed."))
        return result

    def find_live_operator(self, position_id):
        if not self.enabled or not self.read_access_token:
            raise RuntimeError("Live tracker access is not configured on this tablet.")

        target_position_id = str(position_id or "").strip().upper()
        if not target_position_id:
            return None

        url = self._operator_areas_url(target_position_id)
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(url, timeout=15, context=context) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Cloud roster lookup failed: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Could not reach cloud roster: {error.reason}") from error

        result = json.loads(payload or "{}")
        row = result.get("assignment")
        if not row:
            return None
        return {
            "position_id": str(row.get("position_id", "")).strip(),
            "payroll_name": str(row.get("payroll_name", "")).strip(),
            "benefits_class": "",
            "reports_to_name": "",
            "position_status": "Active",
        }

    def _operator_areas_url(self, position_id):
        if "/save-entry" in self.endpoint_url:
            url = self.endpoint_url.replace("/save-entry", "/operator-areas")
        else:
            url = self.endpoint_url.rstrip("/") + "/operator-areas"

        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}position_id={urllib.parse.quote(position_id)}"
        url = f"{url}&token={urllib.parse.quote(self.read_access_token)}"

        if self.function_key:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}code={self.function_key}"
        return url

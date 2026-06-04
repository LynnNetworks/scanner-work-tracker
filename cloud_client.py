import json
import ssl
import urllib.error
import urllib.request

import certifi


class CloudClient:
    def __init__(self, endpoint_url="", function_key="", tablet_id=""):
        self.endpoint_url = endpoint_url.strip()
        self.function_key = function_key.strip()
        self.tablet_id = tablet_id.strip()

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

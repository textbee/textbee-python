"""End-to-end through real urllib and a real socket, against a local stand-in API."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from textbee import Textbee, TextbeeError, __version__

API_KEY = "local-key"

Seen = dict[str, Any]


class FakeApi(BaseHTTPRequestHandler):
    seen: ClassVar[list[Seen]] = []

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def _record(self) -> Seen:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        entry: Seen = {
            "method": self.command,
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": json.loads(raw) if raw else None,
        }
        FakeApi.seen.append(entry)
        return entry

    def _send(self, status: int, body: Any, content_type: str = "application/json") -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        entry = self._record()
        if entry["headers"].get("x-api-key") != API_KEY:
            self._send(401, {"error": "Unauthorized"})
        elif self.path == "/api/v1/gateway/devices":
            self._send(200, {"data": [{"_id": "dev-1", "enabled": True, "isDefault": True}]})
        elif self.path.startswith("/api/v1/gateway/messages"):
            self._send(200, {"data": [{"_id": "m-1"}], "meta": {"limit": 50, "nextCursor": None}})
        elif self.path == "/api/v1/gateway/devices/broken":
            self._send(502, b"<html>bad gateway</html>", "text/html")
        else:
            self._send(404, {"message": "Not found"})

    def do_POST(self) -> None:
        entry = self._record()
        if self.path == "/api/v1/gateway/send-sms":
            self._send(
                200,
                {
                    "data": {
                        "success": True,
                        "message": "SMS added to queue for processing",
                        "smsBatchId": "batch-1",
                        "recipientCount": len(entry["body"]["recipients"]),
                    }
                },
            )
        elif self.path.endswith("/set-default"):
            self._send(200, {"data": {"_id": "dev-1", "isDefault": True}})
        else:
            self._send(404, {"message": "Not found"})


@pytest.fixture
def server() -> Iterator[tuple[str, list[Seen]]]:
    FakeApi.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/api/v1/", FakeApi.seen
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_round_trip_against_a_self_hosted_base_url(server: tuple[str, list[Seen]]) -> None:
    base_url, seen = server
    textbee = Textbee(api_key=API_KEY, base_url=base_url)

    devices = textbee.get_devices()
    result = textbee.send_sms(message="hi ✓", recipients=["+12025550123", "+12025550124"])
    page = textbee.get_messages(direction="received", search="a b&c")
    device = textbee.set_default_device("dev-1")

    assert devices[0]["_id"] == "dev-1"
    assert "smsBatchId" in result and result["recipientCount"] == 2
    assert page["data"][0]["_id"] == "m-1"
    assert device["isDefault"] is True

    send = seen[1]
    assert send["headers"]["user-agent"] == f"textbee-python/{__version__}"
    assert send["headers"]["x-sdk-client"] == f"textbee-python/{__version__}"
    assert send["headers"]["content-type"] == "application/json"
    assert send["body"] == {"message": "hi ✓", "recipients": ["+12025550123", "+12025550124"]}
    assert seen[2]["path"] == "/api/v1/gateway/messages?direction=received&search=a+b%26c"
    assert seen[3]["method"] == "POST"
    assert seen[3]["body"] is None


def test_real_http_errors_become_textbee_errors(server: tuple[str, list[Seen]]) -> None:
    base_url, _seen = server

    with pytest.raises(TextbeeError) as unauthorized:
        Textbee(api_key="wrong", base_url=base_url).get_devices()
    assert unauthorized.value.status == 401
    assert unauthorized.value.message == "Unauthorized"

    textbee = Textbee(api_key=API_KEY, base_url=base_url)

    with pytest.raises(TextbeeError) as not_found:
        textbee.get_sms("dev-1", "missing")
    assert not_found.value.status == 404
    assert not_found.value.message == "Not found"

    with pytest.raises(TextbeeError) as bad_gateway:
        textbee.get_device("broken")
    assert bad_gateway.value.status == 502
    assert bad_gateway.value.message == "HTTP 502"
    assert bad_gateway.value.body == "<html>bad gateway</html>"


def test_refuses_redirects_so_the_key_never_reaches_another_host() -> None:
    other_seen: list[Seen] = []

    class Other(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return None

        def do_GET(self) -> None:
            other_seen.append({"headers": dict(self.headers.items())})
            self.send_response(200)
            self.end_headers()

    other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
    target = f"http://localhost:{other.server_address[1]}/api/v1/gateway/devices"

    class Redirecting(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return None

        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("location", target)
            self.send_header("content-length", "0")
            self.end_headers()

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Redirecting)
    for httpd in (other, origin):
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        textbee = Textbee(api_key=API_KEY, base_url=f"http://127.0.0.1:{origin.server_address[1]}")

        with pytest.raises(TextbeeError) as raised:
            textbee.get_devices()

        assert raised.value.status == 302
        assert target in raised.value.message
        assert other_seen == []
    finally:
        for httpd in (other, origin):
            httpd.shutdown()
            httpd.server_close()

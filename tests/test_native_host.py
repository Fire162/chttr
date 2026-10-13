from __future__ import annotations

import io
import json
import struct
import sys

from chttr import native_host as NH


def frame(obj: dict) -> bytes:
    data = json.dumps(obj).encode()
    return struct.pack("<I", len(data)) + data


def unframe(buf: bytes) -> list[dict]:
    out = []
    while buf:
        (n,) = struct.unpack("<I", buf[:4])
        out.append(json.loads(buf[4:4 + n]))
        buf = buf[4 + n:]
    return out


def test_extension_id_is_derived_from_the_manifest_key():
    ext = NH.extension_id()
    assert len(ext) == 32 and set(ext) <= set("abcdefghijklmnop")
    assert ext == NH.extension_id()


def test_native_host_relays_messages_and_echoes_the_tab(monkeypatch):
    sent = []

    def fake_send(command, timeout=5.0):
        sent.append(command)
        return {"ok": True, "recording": True}

    monkeypatch.setattr(NH, "send", fake_send)
    stdin = io.BytesIO(frame({"type": "joined", "code": "abc-defg-hij", "tab": 7}) + frame({"type": "heartbeat"}))
    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": stdin})())
    monkeypatch.setattr(sys, "stdout", type("S", (), {"buffer": stdout})())
    assert NH.run() == 0
    assert sent[0].startswith("meet {") and '"code":"abc-defg-hij"' in sent[0]
    replies = unframe(stdout.getvalue())
    assert replies[0] == {"ok": True, "recording": True, "tab": 7}
    assert "tab" not in replies[1]


def test_native_host_survives_a_missing_daemon(monkeypatch):
    def down(command, timeout=5.0):
        raise NH.DaemonNotRunning("not running")

    monkeypatch.setattr(NH, "send", down)
    stdin = io.BytesIO(frame({"type": "heartbeat", "tab": 1}))
    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": stdin})())
    monkeypatch.setattr(sys, "stdout", type("S", (), {"buffer": stdout})())
    NH.run()
    assert unframe(stdout.getvalue())[0]["ok"] is False


def test_load_extension_flag_is_added_once_and_replaces_old_copies(tmp_path):
    flags = tmp_path / "chromium-flags.conf"
    flags.write_text("--ozone-platform=wayland\n--load-extension=/opt/other\n")
    ext = str(NH.EXTENSION_DIR)
    assert NH._add_load_extension(flags, ext).startswith("added")
    assert NH._add_load_extension(flags, ext) == "already loaded"
    text = flags.read_text()
    assert f"--load-extension=/opt/other,{ext}" in text
    assert (tmp_path / "chromium-flags.conf.bak-chttr").exists()

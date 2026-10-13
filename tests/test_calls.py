from __future__ import annotations

from chttr import calls as K


def node(binary: str, app: str = "", state: str = "running", media: str = "Stream/Input/Audio", **extra) -> dict:
    props = {"media.class": media, "application.process.binary": binary, "application.name": app or binary,
             "node.name": f"{binary}-node", **extra}
    return {"type": "PipeWire:Interface:Node", "info": {"state": state, "props": props}}


def test_mic_users_keeps_running_capture_streams_only():
    dump = [
        node("zoom"),
        node("chromium", app="Chromium"),
        node("firefox", state="idle"),                                   # not capturing right now
        node("spotify", media="Stream/Output/Audio"),                    # playback
        node("pw-record", app="pw-record"),                              # chttr's own recorder
        node("obs", **{"stream.capture.sink": "true"}),                  # recording the speakers
        {"type": "PipeWire:Interface:Link", "info": {}},
    ]
    assert [u.binary for u in K.mic_users(dump)] == ["zoom", "chromium"]


def test_classify():
    assert K.classify(K.MicUser("zoom", "ZOOM VoiceEngine", "")) == ("Zoom", False)
    assert K.classify(K.MicUser("teams-for-linux", "", "")) == ("Microsoft Teams", False)
    assert K.classify(K.MicUser("/opt/brave.com/brave/brave", "Brave", "")) == ("Brave", True)
    assert K.classify(K.MicUser("", "Firefox", "")) == ("Firefox", True)
    assert K.classify(K.MicUser("audacity", "Audacity", "")) is None


def test_current_call_prefers_call_apps_and_defers_meet_to_the_extension():
    zoom, chrome = K.MicUser("zoom", "", ""), K.MicUser("chrome", "", "")
    assert K.current_call([chrome, zoom], meet_call_active=False) == "Zoom"
    assert K.current_call([chrome], meet_call_active=False) == "Chrome"
    assert K.current_call([chrome], meet_call_active=True) is None
    assert K.current_call([], meet_call_active=False) is None


def test_watcher_offers_once_after_a_few_seconds_and_ends_after_silence():
    w = K.CallWatcher(start_after=5, end_after=20)
    assert w.update("Zoom", 0) is None
    assert w.update("Zoom", 3) is None
    assert w.update("Zoom", 6) == ("start", "Zoom")
    assert w.update("Zoom", 9) is None                    # not offered twice
    assert w.update(None, 15) is None                     # a short gap (muted in the app) doesn't end it
    assert w.update("Zoom", 16) is None
    assert w.update(None, 30) is None
    assert w.update(None, 37) == ("end", "Zoom")
    assert w.update("Zoom", 40) is None                   # a new call...
    assert w.update("Zoom", 46) == ("start", "Zoom")      # ...is offered again


def test_a_brief_mic_grab_is_not_a_call():
    w = K.CallWatcher(start_after=5, end_after=20)
    assert w.update("Chrome", 0) is None
    assert w.update(None, 2) is None
    assert w.update(None, 30) == ("end", "Chrome")        # never offered, quietly forgotten

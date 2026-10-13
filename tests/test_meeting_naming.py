from __future__ import annotations

import numpy as np

from chttr import diarize as D
from chttr.meeting.naming import assign_speakers
from chttr.meeting.store import Utterance

T0 = 1_700_000_000.0


def unit(seed: int, dim: int = 32) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=dim)
    return (v / np.linalg.norm(v)).astype(np.float32)


def attendee(name: str, email: str, me: bool = False, status: str = "ACCEPTED") -> dict:
    return {"name": name, "email": email, "organizer": False, "status": status, "is_me": me}


def speaking(name: str, *times: float) -> list[dict]:
    return [{"type": "speaking", "name": name, "t": T0 + t} for t in times]


def test_captions_name_a_cluster_and_link_the_invite():
    mine = [Utterance(0, 2, "mic", "hi all")]
    them = [Utterance(3, 6, "them", "hey", cluster=0), Utterance(7, 10, "them", "so the plan", cluster=0)]
    events = speaking("Dana Lee", 3.5, 4.5, 8.0)
    attendees = [attendee("Rob B", "rob@x.com", me=True), attendee("Dana Lee", "dana@x.com")]
    speakers, me, _ = assign_speakers(mine, them, events, T0, attendees, "", {0: unit(1)}, {})
    assert me == "Rob B"
    assert speakers["Dana Lee"].source == "captions"
    assert speakers["Dana Lee"].email == "dana@x.com"
    assert {u.speaker for u in them} == {"Dana Lee"}
    assert mine[0].speaker == "Rob B"


def test_too_few_caption_votes_fall_through_to_the_calendar():
    them = [Utterance(3, 9, "them", "words", cluster=0)]
    events = speaking("Dana Lee", 4.0)                                 # one vote is not enough
    attendees = [attendee("Me", "me@x.com", me=True), attendee("Ali Khan", "ali@x.com")]
    speakers, _, _ = assign_speakers([], them, events, T0, attendees, "", {0: unit(2)}, {})
    assert list(speakers) == ["Ali Khan"]
    assert speakers["Ali Khan"].source == "calendar"


def test_remembered_voice_beats_the_calendar_guess():
    dana = unit(3)
    voices = {"dana@x.com": D.Voice("dana@x.com", "Dana", "dana@x.com", dana, 5)}
    them = [Utterance(0, 5, "them", "a", cluster=0)]
    attendees = [attendee("Dana", "dana@x.com"), attendee("Ali", "ali@x.com")]
    speakers, _, _ = assign_speakers([], them, [], T0, attendees, "", {0: dana}, voices)
    assert speakers["Dana"].source == "voice"


def test_unknown_voices_become_numbered_speakers_in_order_of_appearance():
    them = [Utterance(10, 12, "them", "b", cluster=1), Utterance(1, 3, "them", "a", cluster=0)]
    speakers, me, _ = assign_speakers([], them, [], T0, [], "Rob", {0: unit(4), 1: unit(5)}, {})
    assert me == "Rob"
    assert [s.label for s in speakers.values()] == ["Speaker 1", "Speaker 2"]
    assert next(u for u in them if u.cluster == 0).speaker == "Speaker 1"


def test_strong_captions_override_the_cluster_for_one_utterance():
    # The voice clustering put utterance 2 with Dana, but Meet's captions are sure it was Ali.
    them = [Utterance(0, 4, "them", "x", cluster=0), Utterance(5, 9, "them", "y", cluster=0),
            Utterance(20, 24, "them", "z", cluster=0), Utterance(30, 34, "them", "w", cluster=1)]
    events = (speaking("Dana", 0.5, 1, 2, 3, 6, 7, 8, 9) + speaking("Ali", 20.5, 21, 22, 23, 24)
              + speaking("Ali", 30.5, 31, 32, 33))
    attendees = [attendee("Dana", "d@x.com"), attendee("Ali", "a@x.com")]
    speakers, _, _ = assign_speakers([], them, events, T0, attendees, "", {0: unit(6), 1: unit(8)}, {})
    assert speakers["Dana"].source == speakers["Ali"].source == "captions"
    assert [u.speaker for u in them] == ["Dana", "Dana", "Ali", "Ali"]


def test_caption_override_needs_a_name_that_already_has_a_voice():
    them = [Utterance(0, 4, "them", "x", cluster=0), Utterance(5, 9, "them", "y", cluster=0),
            Utterance(10, 14, "them", "z", cluster=0)]
    events = speaking("Dana", 0.5, 1, 2, 3, 6, 7, 8, 9, 9.5) + speaking("Ali", 10.5, 11, 12, 13)
    speakers, _, _ = assign_speakers([], them, events, T0, [], "", {0: unit(6)}, {})
    assert them[2].speaker == "Dana"
    assert "Ali" not in speakers


def test_declined_invitees_are_not_used_for_the_calendar_guess():
    them = [Utterance(0, 9, "them", "w", cluster=0)]
    attendees = [attendee("Ali", "a@x.com", status="DECLINED")]
    speakers, _, _ = assign_speakers([], them, [], T0, attendees, "", {0: unit(7)}, {})
    assert list(speakers) == ["Speaker 1"]


def test_room_voices_get_their_own_labels():
    from chttr.meeting.naming import ROOM_OFFSET, room_clusters

    mine = [Utterance(i * 10, i * 10 + 8, "mic", "x", cluster=0) for i in range(8)]          # you: 64 s
    mine += [Utterance(100 + i * 10, 100 + i * 10 + 7, "mic", "y", cluster=1) for i in range(4)]  # colleague: 28 s
    mine += [Utterance(200, 202, "mic", "z", cluster=2)]                                     # a cough: 2 s
    centroids = {0: unit(10), 1: unit(11), 2: unit(12)}
    me, room = room_clusters(mine, centroids, None)
    assert me == 0 and room == {1: "In the room 1"}
    speakers, _, _ = assign_speakers(mine, [], [], T0, [], "Rob", {}, {}, room)
    assert speakers["In the room 1"].clusters == [ROOM_OFFSET + 1]
    assert {u.speaker for u in mine if u.cluster in (0, 2)} == {"Rob"}
    assert {u.speaker for u in mine if u.cluster == 1} == {"In the room 1"}


def test_a_remembered_own_voice_picks_you_even_when_you_talk_less():
    from chttr.meeting.naming import room_clusters

    me_voice = unit(20)
    mine = [Utterance(i * 10, i * 10 + 5, "mic", "a", cluster=0) for i in range(5)]           # 25 s, you
    mine += [Utterance(100 + i * 10, 100 + i * 10 + 8, "mic", "b", cluster=1) for i in range(8)]  # 64 s, a talker
    me, room = room_clusters(mine, {0: me_voice, 1: unit(21)}, me_voice)
    assert me == 0 and room == {1: "In the room 1"}


def test_one_voice_on_the_mic_is_just_you():
    from chttr.meeting.naming import room_clusters

    mine = [Utterance(0, 30, "mic", "a", cluster=0)]
    assert room_clusters(mine, {0: unit(1)}, None) == (0, {})
    assert room_clusters(mine, {}, None) == (None, {})

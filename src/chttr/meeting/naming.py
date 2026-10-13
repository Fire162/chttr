"""Who is who: name each voice cluster from Meet captions, remembered voices and the
calendar, in that order of trust. Whatever is left becomes "Speaker N" (and may be
named later by Claude while writing the notes, or by `chttr meeting rename`)."""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .. import diarize as D
from .analysis import SELF_CAPTION_NAMES, _caption_votes, _match_attendee, _norm
from .store import Speaker, Utterance

ROOM_OFFSET = 1000          # mic-channel cluster ids are stored as 1000 + n, apart from the them channel's
ROOM_MIN_SECONDS = 20.0
ROOM_MIN_SHARE = 0.15


def room_clusters(mine: list[Utterance], mic_centroids: dict[int, np.ndarray],
                  own_voice: np.ndarray | None) -> tuple[int | None, dict[int, str]]:
    """Which mic-channel voice is you, and which are other people in the room.
    You: the voice that matches the one remembered for you, else the one that talks
    most. Someone else only counts with real speaking time (ROOM_MIN_SECONDS and
    ROOM_MIN_SHARE of the mic's speech); anything smaller is folded into you."""
    seconds: dict[int, float] = defaultdict(float)
    first: dict[int, float] = {}
    for u in mine:
        seconds[u.cluster] += u.end - u.start
        first.setdefault(u.cluster, u.start)
    if not mic_centroids or not seconds:
        return None, {}
    if len(seconds) == 1:
        return next(iter(seconds)), {}
    if own_voice is not None:
        me = max(mic_centroids, key=lambda c: float(mic_centroids[c] @ own_voice))
    else:
        me = max(seconds, key=lambda c: seconds[c])
    total = sum(seconds.values()) or 1.0
    others = [c for c in seconds if c != me and seconds[c] >= ROOM_MIN_SECONDS and seconds[c] / total >= ROOM_MIN_SHARE]
    return me, {c: f"In the room {n}" for n, c in enumerate(sorted(others, key=lambda c: first[c]), 1)}


def assign_speakers(mine: list[Utterance], them: list[Utterance], events: list[dict], started: float,
                    attendees: list[dict], me_setting: str, centroids: dict[int, np.ndarray],
                    voices: dict[str, D.Voice], room: dict[int, str] | None = None
                    ) -> tuple[dict[str, Speaker], str, dict[int, float]]:
    """Label every utterance (sets .speaker) and return (speakers by label, your name,
    seconds per them-cluster). `room` maps mic-channel clusters to labels for other
    people in the room; every other mic utterance is you."""
    me_att = next((a for a in attendees if a["is_me"]), None)
    me_name = me_setting or (me_att["name"] if me_att and me_att["name"] else "") or "Me"
    others = [a for a in attendees if not a["is_me"]]
    invited = {a["email"] for a in others}

    speakers: dict[str, Speaker] = {}
    room = room or {}
    if mine:
        speakers[me_name] = Speaker(me_name, me_name, me_att["email"] if me_att else "", "me")
        for u in mine:
            label = room.get(u.cluster)
            if label:
                if label not in speakers:
                    speakers[label] = Speaker(label, label, "", "unknown", clusters=[ROOM_OFFSET + u.cluster])
                u.speaker = label
            else:
                u.speaker = me_name

    self_names = SELF_CAPTION_NAMES | {_norm(me_name)} | ({_norm(me_att["name"])} if me_att and me_att["name"] else set())
    per_cluster, per_utt = _caption_votes(them, events, started, self_names)
    cluster_label: dict[int, str] = {}
    cluster_seconds: dict[int, float] = defaultdict(float)
    for u in them:
        cluster_seconds[u.cluster] += u.end - u.start

    def add_speaker(label: str, cluster: int, source: str, name: str = "", email: str = "", evidence: str = "") -> None:
        sp = speakers.get(label)
        if sp is None:
            sp = speakers[label] = Speaker(label, name or label, email, source, evidence)
        if cluster not in sp.clusters:
            sp.clusters.append(cluster)
        cluster_label[cluster] = label

    for c, votes in per_cluster.items():
        total = sum(votes.values())
        if not total:
            continue
        name, n = votes.most_common(1)[0]
        if n >= 3 and n / total >= 0.6:
            att = _match_attendee(name, others)
            add_speaker(name, c, "captions", name, att["email"] if att else "",
                        f"Meet captions named {name} for {n} of {total} caption updates")

    taken = {sp.email or sp.label.lower() for sp in speakers.values()}
    for c in sorted(centroids, key=lambda c: -cluster_seconds[c]):
        if c in cluster_label:
            continue
        v, score = D.match_voice({k: x for k, x in voices.items() if k not in taken}, centroids[c], invited)
        if v:
            att = next((a for a in others if a["email"] == v.email), None)
            label = (att["name"] if att and att["name"] else v.name) or v.email
            add_speaker(label, c, "voice", label, v.email, f"voice matches the one remembered for {label} ({score:.2f})")
            taken.add(v.key)

    unnamed = [c for c in cluster_seconds if c not in cluster_label]
    them_total = sum(cluster_seconds.values()) or 1
    left = [a for a in others if (a["email"] not in taken) and a["status"] != "DECLINED"]
    if len(unnamed) == 1 and len(left) == 1 and len(cluster_label) == 0 \
            and cluster_seconds[unnamed[0]] / them_total > 0.8:
        a = left[0]
        label = a["name"] or a["email"]
        add_speaker(label, unnamed[0], "calendar", label, a["email"], "the only other person invited")
        unnamed = []

    for n, c in enumerate(sorted(unnamed, key=lambda c: min(u.start for u in them if u.cluster == c)), 1):
        add_speaker(f"Speaker {n}", c, "unknown")

    for u, votes in zip(them, per_utt, strict=True):
        u.speaker = cluster_label.get(u.cluster, "")
        total = sum(votes.values())
        if total >= 4:
            name, n = votes.most_common(1)[0]
            if n / total >= 0.85 and name in speakers and name != u.speaker:
                u.speaker = name        # captions clearly disagree with the voice clustering here
    for sp in speakers.values():
        sp.seconds = sum(u.end - u.start for u in [*mine, *them] if u.speaker == sp.label and u.text)
    return speakers, me_name, cluster_seconds

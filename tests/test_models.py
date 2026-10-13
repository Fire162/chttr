from __future__ import annotations

import hashlib
import io

import pytest

from chttr import models as M


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def spec_for(data: bytes) -> M.ModelFile:
    return M.ModelFile("org/repo", "0" * 40, "model.onnx", hashlib.sha256(data).hexdigest(), len(data))


def test_every_pinned_model_uses_a_commit_and_a_full_hash():
    for spec in [*M.PARAKEET, M.SILERO_VAD, M.WESPEAKER]:
        assert len(spec.revision) == 40 and "/resolve/main/" not in spec.url
        assert len(spec.sha256) == 64 and spec.size > 0


def test_download_verifies_and_moves_into_place(tmp_path, monkeypatch):
    data = b"onnx" * 1000
    monkeypatch.setattr(M.urllib.request, "urlopen", lambda req, timeout: FakeResponse(data))
    dest = tmp_path / "m" / "model.onnx"
    progress = []
    M.download(spec_for(data), dest, lambda done, total: progress.append(done))
    assert dest.read_bytes() == data
    assert progress[-1] == len(data)
    assert M.verify(spec_for(data), dest)


def test_tampered_download_is_rejected_and_removed(tmp_path, monkeypatch):
    good = b"good model"
    monkeypatch.setattr(M.urllib.request, "urlopen", lambda req, timeout: FakeResponse(b"evil model"))
    dest = tmp_path / "model.onnx"
    with pytest.raises(M.ChecksumError):
        M.download(spec_for(good), dest)
    assert not dest.exists()
    assert not (tmp_path / "model.onnx.part").exists()


def test_existing_file_of_the_right_size_is_kept(tmp_path, monkeypatch):
    data = b"x" * 10
    dest = tmp_path / "model.onnx"
    dest.write_bytes(data)

    def boom(*a, **k):
        raise AssertionError("should not download")

    monkeypatch.setattr(M.urllib.request, "urlopen", boom)
    assert M.download(spec_for(data), dest) == dest

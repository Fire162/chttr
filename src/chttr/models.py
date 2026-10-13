"""Model files chttr downloads, pinned to a repository commit and a SHA-256.

Every download is fetched from a fixed Hugging Face revision (never `main`) and
checked against its published hash before it is moved into place, so a changed
or tampered file is rejected instead of being loaded into the process.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("chttr.models")

HF = "https://huggingface.co"


@dataclass(frozen=True)
class ModelFile:
    repo: str
    revision: str
    name: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return f"{HF}/{self.repo}/resolve/{self.revision}/{self.name}"


_PARAKEET = ("istupakov/parakeet-tdt-0.6b-v3-onnx", "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce")
PARAKEET = [
    ModelFile(*_PARAKEET, "encoder-model.int8.onnx",
              "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09", 652183999),
    ModelFile(*_PARAKEET, "decoder_joint-model.int8.onnx",
              "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70", 18202004),
    ModelFile(*_PARAKEET, "vocab.txt",
              "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d", 93939),
    ModelFile(*_PARAKEET, "config.json",
              "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466", 97),
]
SILERO_VAD = ModelFile("istupakov/silero-vad-onnx", "b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6", "silero_vad.onnx",
                       "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3", 2327524)
WESPEAKER = ModelFile("Wespeaker/wespeaker-voxceleb-resnet34-LM", "f0c48c298fd835726c27956a5d617bad7115627e",
                      "voxceleb_resnet34_LM.onnx",
                      "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068", 26530309)


class ChecksumError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(spec: ModelFile, dest: Path, progress: Callable[[int, int], None] | None = None) -> Path:
    """Fetch `spec` to `dest`, verifying size and SHA-256 on the way. A file already at
    `dest` is trusted if its size matches (hashing 650 MB on every setup run is slow);
    `chttr doctor --verify` hashes everything."""
    if dest.exists() and dest.stat().st_size == spec.size:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    done = 0
    log.info("downloading %s", spec.url)
    req = urllib.request.Request(spec.url, headers={"User-Agent": "chttr"})
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as fh:
        while chunk := r.read(1 << 20):
            fh.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, spec.size)
    if done != spec.size or h.hexdigest() != spec.sha256:
        tmp.unlink(missing_ok=True)
        raise ChecksumError(f"{spec.name}: download does not match the pinned checksum "
                            f"(got {done} bytes, sha256 {h.hexdigest()[:16]}…); refusing to use it")
    os.replace(tmp, dest)
    return dest


def verify(spec: ModelFile, path: Path) -> bool:
    return path.exists() and path.stat().st_size == spec.size and sha256_of(path) == spec.sha256


def console_progress(name: str) -> Callable[[int, int], None]:
    last = [0.0]

    def show(done: int, total: int) -> None:
        if time.monotonic() - last[0] > 1 or done == total:
            last[0] = time.monotonic()
            end = "\n" if done == total else ""
            print(f"\r    {name}: {done * 100 // max(total, 1)}% of {total >> 20} MB", end=end, flush=True)

    return show

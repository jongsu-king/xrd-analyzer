"""Reader registry: chooses a reader by file extension (never by guessing contents)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

from ..models.dataset import XRDPattern
from . import validators
from .bruker import BrukerBrmlReader
from .readers import (
    VENDOR_STUBS,
    ProbeResult,
    ReadOptions,
    TextPatternReader,
    UnsupportedFormatError,
)


class PatternReader(Protocol):
    name: str
    status: str
    extensions: tuple[str, ...]
    description: str

    def supports(self, path: str | os.PathLike[str]) -> bool: ...
    def probe(
        self, path: str | os.PathLike[str], options: ReadOptions | None = None
    ) -> ProbeResult: ...
    def parse(
        self, path: str | os.PathLike[str], options: ReadOptions | None = None
    ) -> XRDPattern: ...
    def validate(self, pattern: XRDPattern) -> list[validators.Issue]: ...
    def metadata(self, path: str | os.PathLike[str]) -> dict[str, str]: ...


READERS: list[PatternReader] = [TextPatternReader(), BrukerBrmlReader(), *VENDOR_STUBS]


def get_reader(path: str | os.PathLike[str]) -> PatternReader:
    for r in READERS:
        if r.supports(path):
            return r
    exts = sorted({e for r in READERS for e in r.extensions})
    raise UnsupportedFormatError(
        f"{Path(path).name}: unknown extension {Path(path).suffix!r}; known: {', '.join(exts)}"
    )


def probe_file(path: str | os.PathLike[str], options: ReadOptions | None = None) -> ProbeResult:
    return get_reader(path).probe(path, options)


def read_pattern(path: str | os.PathLike[str], options: ReadOptions | None = None) -> XRDPattern:
    return get_reader(path).parse(path, options)


def list_formats() -> list[dict[str, Any]]:
    return [
        {
            "extensions": list(r.extensions),
            "parser": r.name,
            "status": r.status,
            "description": r.description,
        }
        for r in READERS
    ]

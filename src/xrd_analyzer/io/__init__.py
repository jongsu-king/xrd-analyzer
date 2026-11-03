"""File readers. Every reader exposes ``supports / probe / parse / validate / metadata``."""

from .readers import (
    AxisConfirmationRequired,
    ReadOptions,
    UnsupportedFormatError,
    XRDFormatError,
)
from .registry import list_formats, probe_file, read_pattern

__all__ = [
    "AxisConfirmationRequired",
    "ReadOptions",
    "UnsupportedFormatError",
    "XRDFormatError",
    "list_formats",
    "probe_file",
    "read_pattern",
]

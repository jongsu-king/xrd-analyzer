"""Readers for 2θ–intensity data.

Policy (see README "Format support"):

* Generic delimited text (CSV/TXT/XY/DAT) is implemented and tested **with synthetic
  fixtures only** (status ``SYNTHETIC_ONLY``) — real instrument exports have not been
  verified yet.
* The axis quantity (2θ vs θ) and unit (deg vs rad) are never guessed. They must be
  declared unambiguously in the column header (e.g. ``2theta_deg``, ``2θ (°)``) or confirmed
  through :class:`ReadOptions`; otherwise :class:`AxisConfirmationRequired` is raised.
* Vendor formats (.xrdml, .ras, .rasx, .raw, .brml, .udf) are recognised but raise
  :class:`UnsupportedFormatError` until a parser is validated against real files.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..models.dataset import SYNTHETIC_ONLY, UNSUPPORTED, XRDPattern, sha256_file
from . import validators

MAX_FILE_BYTES = 512 * 1024 * 1024
_COMMENT_PREFIXES = ("#", ";", "!", "//", "%")
_DECIMAL_COMMA = re.compile(r"^[+-]?\d+,\d+([eE][+-]?\d+)?$")


class XRDFormatError(ValueError):
    """The file cannot be interpreted as a 2θ–intensity pattern."""


class AxisConfirmationRequired(XRDFormatError):
    """The axis quantity/unit or the column assignment must be confirmed by the user."""

    def __init__(self, message: str, probe: ProbeResult | None = None) -> None:
        super().__init__(message)
        self.probe = probe


class UnsupportedFormatError(XRDFormatError):
    """The format is recognised but not (yet) supported."""


@dataclass
class ReadOptions:
    """User confirmations for reading delimited text.

    ``axis_quantity`` is ``"2theta"`` or ``"theta"``; ``axis_unit`` is ``"deg"`` or ``"rad"``.
    Selecting ``"theta"`` converts with 2θ = 2·θ — only do so when the file really stores θ.
    """

    x_column: int | None = None
    y_column: int | None = None
    axis_quantity: str | None = None
    axis_unit: str | None = None
    intensity_unit: str | None = None
    delimiter: str | None = None
    drop_nonfinite: bool = False
    encoding: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReadOptions:
        return cls(**d)


@dataclass
class ProbeResult:
    path: str
    parser: str
    status: str
    delimiter: str | None = None
    n_columns: int = 0
    n_rows: int = 0
    header: list[str] = field(default_factory=list)
    x_column: int | None = None
    y_column: int | None = None
    axis_quantity: str | None = None
    axis_unit: str | None = None
    intensity_unit: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    preview: list[list[float]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def axis_confirmed(self) -> bool:
        return self.axis_quantity == "2theta" and self.axis_unit in {"deg", "rad"}


# ---------------------------------------------------------------------------- helpers


def _normalise(name: str) -> str:
    s = name.strip().lower().replace("θ", "theta").replace("°", "deg")
    # Keep '/' significant: counts and counts/second are different units.
    return re.sub(r"[\s_\-\[\]\(\)]", "", s)


_UNCERTAINTY_WORDS = {
    "err",
    "error",
    "errors",
    "esd",
    "sigma",
    "σ",
    "unc",
    "uncertainty",
    "std",
    "stdev",
    "stddev",
    "offset",
    "delta",
    "δ",
    "dev",
    "deviation",
    "residual",
    "resid",
}
_ANGLE = re.compile(r"(2theta|twotheta|2th|theta|omega)(deg|degrees?|rad|radians?)?")
_INTENSITY = re.compile(r"(intensity|int|i|y|signal)(.*)")
_RATE = re.compile(r"cps|(?:counts?|cts|c)(?:/|per)(?:s|sec|second|seconds)")
_COUNTS = {"counts", "count", "cts"}
_ARBITRARY = {"a.u.", "a.u", "au", "arb", "arbitraryunits"}


def classify_column(name: str) -> tuple[str | None, str | None]:
    """Return (role, unit) for a header token.

    role: ``"2theta"``, ``"theta"`` (ambiguous: needs confirmation), ``"intensity"``,
    ``"uncertainty"`` (error/esd/offset columns — never used as axis or intensity) or None.
    unit: ``"deg"``/``"rad"`` for angles, an intensity unit (rates before counts), or None.
    """
    # Split camel case before lowercasing so 2thetaError_deg is excluded too.
    raw = re.sub(r"([a-zθ])([A-Z])", r"\1 \2", name).strip().lower().replace("θ", "theta")
    if not raw:
        return None, None
    words = set(re.split(r"[^a-zσδ]+", raw)) - {""}
    if words & _UNCERTAINTY_WORDS:
        return "uncertainty", None
    s = _normalise(name)
    # A spaced slash after a bare quantity separates quantity and unit. Leave
    # rate expressions (including "counts / second") intact.
    parts = re.split(r"\s+/\s+", name)
    if len(parts) == 2:
        quantity, declared_unit = map(_normalise, parts)
        bare_angle = _ANGLE.fullmatch(quantity)
        if bare_angle and bare_angle.group(2) is None:
            if not re.fullmatch(r"deg|degrees?|rad|radians?", declared_unit):
                return None, None
            s = quantity + declared_unit
        elif quantity in {
            "intensity",
            "int",
            "i",
            "y",
            "signal",
        }:
            if not (_RATE.fullmatch(declared_unit) or declared_unit in _COUNTS | _ARBITRARY):
                return None, None
            s = quantity + declared_unit
    angle = _ANGLE.fullmatch(s)
    if angle:
        quantity, unit = angle.groups()
        role = "2theta" if quantity in {"2theta", "twotheta", "2th"} else "theta"
        angle_unit = None if unit is None else ("deg" if unit.startswith("deg") else "rad")
        return role, angle_unit
    intensity = _INTENSITY.fullmatch(s)
    unit = intensity.group(2) if intensity else s
    if _RATE.fullmatch(unit):
        return "intensity", "cps"
    if unit in _COUNTS:
        return "intensity", "counts"
    if unit in _ARBITRARY:
        return "intensity", "a.u."
    if intensity and unit == "":
        return "intensity", None
    return None, None


def _is_comment(s: str) -> bool:
    return s.startswith(_COMMENT_PREFIXES)


def _split(s: str, delim: str | None) -> list[str]:
    if delim is None:
        return s.split()
    return [t.strip() for t in s.split(delim)]


def _detect_delimiter(s: str) -> str | None:
    for d in ("\t", ";", ","):  # ';' before ',' (European CSV with decimal commas)
        if d in s:
            return d
    return None  # whitespace


def _to_float(tok: str) -> float | None:
    try:
        return float(tok)
    except ValueError:
        return None


def _parse_meta_line(s: str, delim: str | None = None) -> tuple[str, str] | None:
    body = s.lstrip("#;!/% ").strip()
    for sep in (":", "="):
        if sep in body:
            k, v = body.split(sep, 1)
            if k.strip():
                return k.strip(), v.strip()
    if delim is not None and delim in body:
        k, v = body.split(delim, 1)
        if k.strip():
            return k.strip(), v.strip()
    return None


def _read_text(path: Path, encoding: str | None) -> tuple[str, str]:
    size = path.stat().st_size
    if size == 0:
        raise XRDFormatError(f"{path.name}: file is empty")
    if size > MAX_FILE_BYTES:
        raise XRDFormatError(f"{path.name}: file is larger than {MAX_FILE_BYTES // 2**20} MiB")
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise XRDFormatError(f"{path.name}: binary content detected — not a delimited text file")
    for enc in [encoding] if encoding else ["utf-8-sig", "cp949", "latin-1"]:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    raise XRDFormatError(f"{path.name}: cannot decode text with encoding {encoding!r}")


@dataclass
class _Scan:
    delimiter: str | None
    header: list[str]
    rows: np.ndarray
    row_lines: np.ndarray
    metadata: dict[str, str]


def _scan(text: str, name: str, delimiter: str | None) -> _Scan:
    lines = text.splitlines()
    pre: list[tuple[int, str, list[str]]] = []
    metadata: dict[str, str] = {}
    delim = delimiter
    delim_fixed = delimiter is not None
    rows: list[list[float]] = []
    row_lines: list[int] = []
    ncols = 0
    header_line: str | None = None  # declaration containing both axis and intensity roles
    header_ncols = 0
    for lineno, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s:
            continue
        if _is_comment(s):
            kv = _parse_meta_line(s)
            if kv and not rows:
                metadata.setdefault(kv[0], kv[1])
            continue
        if not rows and not delim_fixed:
            delim = _detect_delimiter(s)
        toks = _split(s, delim)
        if toks and toks[-1] == "" and delim is not None:
            toks = toks[:-1]  # trailing delimiter
        vals = [_to_float(t) for t in toks]
        if all(v is not None for v in vals) and len(vals) >= 1:
            if not rows:
                ncols = len(vals)
                if ncols < 2:
                    raise XRDFormatError(
                        f"{name}: line {lineno}: only one numeric column; a 2θ column and an "
                        "intensity column are required (start/step formats are not supported)"
                    )
                if header_line is not None and ncols != header_ncols:
                    raise XRDFormatError(
                        f"{name}: line {lineno}: expected {header_ncols} columns from the "
                        f"column header, found {ncols}"
                    )
            elif len(vals) != ncols:
                raise XRDFormatError(
                    f"{name}: line {lineno}: expected {ncols} columns, found {len(vals)}"
                )
            rows.append([float(v) for v in vals if v is not None])
            row_lines.append(lineno)
            continue
        if any(_DECIMAL_COMMA.match(t) for t in toks):
            raise XRDFormatError(
                f"{name}: line {lineno}: decimal comma detected ({s[:40]!r}); "
                "convert to decimal points or choose another delimiter"
            )
        if rows:
            raise XRDFormatError(f"{name}: line {lineno}: non-numeric data row {s[:60]!r}")
        n_num = sum(v is not None for v in vals)
        if header_line is not None and n_num > 0:
            # after a recognised column header, a partly numeric line is a malformed data row
            raise XRDFormatError(
                f"{name}: line {lineno}: malformed data row {s[:60]!r} after the column header"
            )
        roles = {classify_column(t)[0] for t in toks} if n_num == 0 else set()
        if roles & {"2theta", "theta"} and "intensity" in roles:
            header_line = s
            header_ncols = len(toks)
            delim_fixed = True
        pre.append((lineno, s, toks))
    if not rows:
        raise XRDFormatError(f"{name}: no numeric data rows found")
    header: list[str] = []
    fallback_decl: str | None = None
    if header_line is None:
        # The data width is known only now. Header-shaped records are all text, data width,
        # ≥ 1 recognised column role. The boundary is the FIRST header-shaped record that
        # names an axis (else the first header-shaped record): from there on, every pre-data
        # record must be number-free, so a later units-like line cannot move the boundary
        # past malformed observations. Role-bearing metadata BEFORE an axis-naming
        # header stays valid. Ambiguity is rejected and names the first
        # offending line; fully recognised headers are handled in the loop above.
        decls = [
            k
            for k, (_, _, tk) in enumerate(pre)
            if len(tk) == ncols
            and all(_to_float(t) is None for t in tk)
            and any(classify_column(t)[0] is not None for t in tk)
        ]
        with_axis = [
            k for k in decls if any(classify_column(t)[0] in {"2theta", "theta"} for t in pre[k][2])
        ]
        if decls:
            boundary = (with_axis or decls)[0]
            first = pre[boundary][1]
            for bad_lineno, bad_line, tk in pre[boundary + 1 :]:
                if any(_to_float(t) is not None for t in tk):
                    raise XRDFormatError(
                        f"{name}: line {bad_lineno}: malformed data row {bad_line[:60]!r} "
                        f"after the column header {first[:40]!r}. If this line is metadata, "
                        "move it above the header or use recognised column names "
                        "(e.g. 2theta_deg,intensity_counts)"
                    )
            # name the columns from the last axis-naming declaration (a names row rather
            # than a following units row), else the last declaration
            fallback_decl = pre[(with_axis or decls)[-1]][1]
    chosen = header_line or fallback_decl or (pre[-1][1] if pre else None)
    if chosen is not None:
        cand = _split(chosen, delim)
        if cand and cand[-1] == "" and delim is not None:
            cand = cand[:-1]
        if len(cand) == ncols and all(_to_float(t) is None for t in cand):
            header = cand
            pre = [q for q in pre if q[1] is not chosen]
    for _, s, _ in pre:
        kv = _parse_meta_line(s, delim)
        if kv:
            metadata.setdefault(kv[0], kv[1])
    return _Scan(
        delimiter=delim,
        header=header,
        rows=np.asarray(rows, dtype=float),
        row_lines=np.asarray(row_lines),
        metadata=metadata,
    )


# ----------------------------------------------------------------------------- readers


class TextPatternReader:
    name = "delimited-text"
    extensions: tuple[str, ...] = (".csv", ".txt", ".xy", ".dat", ".tsv")
    status = SYNTHETIC_ONLY
    description = "Delimited text with a 2θ column and an intensity column"

    def supports(self, path: str | os.PathLike[str]) -> bool:
        return Path(path).suffix.lower() in self.extensions

    def metadata(self, path: str | os.PathLike[str]) -> dict[str, str]:
        return self.probe(path).metadata

    def probe(
        self, path: str | os.PathLike[str], options: ReadOptions | None = None
    ) -> ProbeResult:
        return self._probe(path, options)[0]

    def _probe(
        self, path: str | os.PathLike[str], options: ReadOptions | None
    ) -> tuple[ProbeResult, _Scan]:
        p = Path(path)
        opts = options or ReadOptions()
        text, enc = _read_text(p, opts.encoding)
        scan = _scan(text, p.name, opts.delimiter)
        res = ProbeResult(
            path=str(p),
            parser=self.name,
            status=self.status,
            delimiter=scan.delimiter,
            n_columns=scan.rows.shape[1],
            n_rows=scan.rows.shape[0],
            header=scan.header,
            metadata={**scan.metadata, "encoding": enc},
            preview=scan.rows[:5].tolist(),
        )
        roles = [classify_column(h) for h in scan.header] if scan.header else []
        xs = [i for i, (r, _) in enumerate(roles) if r in {"2theta", "theta"}]
        ys = [i for i, (r, _) in enumerate(roles) if r == "intensity"]
        x_guess = xs[0] if len(xs) == 1 else None
        y_guess = ys[0] if len(ys) == 1 else None
        # Positional defaults apply only to headerless two-column files; named but
        # unsupported/uncertainty columns and duplicate roles require explicit selection.
        if not scan.header and res.n_columns == 2:
            x_guess, y_guess = 0, 1
        res.x_column = opts.x_column if opts.x_column is not None else x_guess
        res.y_column = opts.y_column if opts.y_column is not None else y_guess
        if res.x_column is not None and roles and res.x_column < len(roles):
            res.axis_quantity, res.axis_unit = roles[res.x_column]
        if res.y_column is not None and roles and res.y_column < len(roles):
            res.intensity_unit = roles[res.y_column][1]
        if opts.axis_quantity:
            res.axis_quantity = opts.axis_quantity
        if opts.axis_unit:
            res.axis_unit = opts.axis_unit
        if opts.intensity_unit:
            res.intensity_unit = opts.intensity_unit
        if not scan.header:
            res.issues.append("no column header: axis quantity/unit must be confirmed")
        if res.axis_quantity == "theta" and opts.axis_quantity != "theta":
            res.issues.append("header says θ/theta: confirm whether the values are θ or 2θ")
        return res, scan

    def parse(self, path: str | os.PathLike[str], options: ReadOptions | None = None) -> XRDPattern:
        p = Path(path)
        opts = options or ReadOptions()
        probe, scan = self._probe(p, opts)
        ncols = scan.rows.shape[1]
        xc, yc = probe.x_column, probe.y_column
        if xc is None or yc is None:
            raise AxisConfirmationRequired(
                f"{p.name}: {ncols} columns and no recognisable header — choose the 2θ and "
                "intensity columns",
                probe,
            )
        for c in (xc, yc):
            if not 0 <= c < ncols:
                raise XRDFormatError(f"{p.name}: column index {c} out of range (0–{ncols - 1})")
        if xc == yc:
            raise XRDFormatError(f"{p.name}: 2θ and intensity columns must differ")
        quantity = opts.axis_quantity or probe.axis_quantity
        unit = opts.axis_unit or probe.axis_unit
        if quantity == "theta" and opts.axis_quantity != "theta":
            raise AxisConfirmationRequired(
                f"{p.name}: column header {probe.header[xc]!r} is ambiguous (θ or 2θ?). "
                "Confirm axis_quantity='2theta' or 'theta' explicitly.",
                probe,
            )
        if quantity not in {"2theta", "theta"} or unit not in {"deg", "rad"}:
            raise AxisConfirmationRequired(
                f"{p.name}: axis quantity/unit not declared (found quantity={quantity!r}, "
                f"unit={unit!r}). Confirm e.g. axis_quantity='2theta', axis_unit='deg'.",
                probe,
            )
        x = scan.rows[:, xc].copy()
        y = scan.rows[:, yc].copy()
        flags: list[str] = []
        bad = ~(np.isfinite(x) & np.isfinite(y))
        if np.any(bad):
            lines = scan.row_lines[bad][:5].tolist()
            if not opts.drop_nonfinite:
                raise XRDFormatError(
                    f"{p.name}: {int(bad.sum())} rows contain NaN/Inf (first lines: {lines}); "
                    "enable 'drop non-finite rows' to exclude them explicitly"
                )
            x, y = x[~bad], y[~bad]
            flags.append(f"dropped_{int(bad.sum())}_nonfinite_rows")
        if unit == "rad":
            x = np.rad2deg(x)
            flags.append("axis_converted_from_radians")
        if quantity == "theta":
            x = 2.0 * x
            flags.append("axis_converted_from_theta")
        issues = validators.check_arrays(x, y)
        errs = validators.errors(issues)
        if errs:
            raise XRDFormatError(f"{p.name}: " + "; ".join(e.message for e in errs))
        flags += [i.code for i in issues]
        iunit = opts.intensity_unit or probe.intensity_unit
        if iunit is None:
            iunit = "a.u."
            flags.append("intensity_unit_unconfirmed")
        md = dict(probe.metadata)
        md.update(
            {
                "file_name": p.name,
                "file_size_bytes": str(p.stat().st_size),
                "x_column": probe.header[xc] if probe.header else str(xc),
                "y_column": probe.header[yc] if probe.header else str(yc),
            }
        )
        synthetic = any("synthetic" in f"{k} {v}".lower() for k, v in scan.metadata.items())
        return XRDPattern(
            name=p.stem,
            two_theta_deg=x,
            intensity=y,
            intensity_unit=iunit,
            metadata=md,
            source_path=str(p.resolve()),
            sha256=sha256_file(str(p)),
            parser=self.name,
            format_status=self.status,
            synthetic=synthetic,
            quality_flags=tuple(flags),
        )

    def validate(self, pattern: XRDPattern) -> list[validators.Issue]:
        return validators.check_arrays(pattern.two_theta_deg, pattern.intensity)


class VendorFormatStub:
    """Recognises a vendor format and refuses to guess its contents."""

    status = UNSUPPORTED

    def __init__(self, extensions: tuple[str, ...], name: str, vendor_hint: str) -> None:
        self.extensions = extensions
        self.name = name
        self.description = vendor_hint

    def supports(self, path: str | os.PathLike[str]) -> bool:
        return Path(path).suffix.lower() in self.extensions

    def _message(self, path: str | os.PathLike[str]) -> str:
        return (
            f"{Path(path).name}: {self.description} format is UNSUPPORTED in this version — "
            "no parser has been validated against real instrument files. Export the scan as "
            "CSV/TXT (2θ, intensity) from the instrument software, or add an adapter "
            "following docs/adapters.md."
        )

    def probe(
        self, path: str | os.PathLike[str], options: ReadOptions | None = None
    ) -> ProbeResult:
        return ProbeResult(
            path=str(path), parser=self.name, status=self.status, issues=[self._message(path)]
        )

    def metadata(self, path: str | os.PathLike[str]) -> dict[str, str]:
        return {}

    def parse(self, path: str | os.PathLike[str], options: ReadOptions | None = None) -> XRDPattern:
        raise UnsupportedFormatError(self._message(path))

    def validate(self, pattern: XRDPattern) -> list[validators.Issue]:
        return []


VENDOR_STUBS = (
    VendorFormatStub((".xrdml",), "xrdml", "Malvern Panalytical XRDML (.xrdml)"),
    VendorFormatStub((".ras",), "rigaku-ras", "Rigaku RAS (.ras)"),
    VendorFormatStub((".rasx",), "rigaku-rasx", "Rigaku RASX (.rasx)"),
    VendorFormatStub((".raw",), "raw", "Bruker/other RAW (.raw, version-dependent binary)"),
    VendorFormatStub((".brml",), "bruker-brml", "Bruker BRML (.brml)"),
    VendorFormatStub((".udf",), "philips-udf", "Philips UDF (.udf)"),
)

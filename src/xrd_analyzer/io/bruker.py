"""Bruker BRML reader (.brml: a zip archive of XML files written by DIFFRAC.SUITE).

Checked against real files from one instrument and software version: Bruker D2 PHASER,
``CreatingVersion`` 8.6.3.0, coupled 2θ/θ, continuous PSD fast scan. Each file's raw counts
and 2θ grid were compared with the same scan's text export from the vendor software.
Anything else (other versions, other scan types, several scans, an absorber in the beam)
raises an explicit error, per docs/adapters.md, rather than being read on assumption.

The column meaning comes from the ``DataViews`` section of each file, not from fixed
positions. Wavelengths in the file are reported in the metadata and never override the recipe.
"""

from __future__ import annotations

import os
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

from ..models.dataset import COUNT_UNITS, VERIFIED_NATIVE, XRDPattern, sha256_file
from . import validators
from .readers import (
    MAX_FILE_BYTES,
    ProbeResult,
    ReadOptions,
    UnsupportedFormatError,
    XRDFormatError,
)

TESTED_CREATING_VERSIONS = ("8.6.3.0",)
TESTED_SCANS = (("LockedCoupled", "PsdFastScan"),)
MAX_XML_BYTES = 256 * 1024 * 1024
_CONTAINER = "Experiment0/DataContainer.xml"
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
_EXPORT_HINT = "Export the scan as text from the instrument software, or see docs/adapters.md."


def _member(z: zipfile.ZipFile, name: str, label: str) -> ET.Element:
    """Parse one XML member in memory. Nothing from the archive is written to disk."""
    try:
        info = z.getinfo(name)
    except KeyError:
        raise XRDFormatError(f"{label}: archive has no member {name!r}") from None
    if info.file_size > MAX_XML_BYTES:
        raise XRDFormatError(f"{label}: member {name!r} is larger than the size limit")
    try:
        raw = z.read(info)
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        raise XRDFormatError(f"{label}: archive member {name!r} is corrupted: {exc}") from exc
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise XRDFormatError(f"{label}: member {name!r} declares a DTD, which BRML files do not")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise XRDFormatError(f"{label}: member {name!r} is not valid XML: {exc}") from exc


def _raw_data_name(container: ET.Element, label: str) -> str:
    refs = [e.text or "" for e in container.findall("./RawDataReferenceList/string")]
    if len(refs) != 1:
        raise UnsupportedFormatError(
            f"{label}: {len(refs)} raw-data sets; only single-scan files are supported. "
            + _EXPORT_HINT
        )
    name = refs[0].replace("\\", "/")
    normal = posixpath.normpath(name)
    if posixpath.isabs(normal) or normal.startswith(".."):
        raise XRDFormatError(f"{label}: unsafe raw-data path {name!r} in the archive")
    return normal


def _columns(route: ET.Element, label: str) -> tuple[dict[str, int], ET.Element]:
    """Column index of each quantity, as declared by the file's ``DataViews`` section."""
    layout: dict[str, int] = {}
    counters: list[tuple[int, ET.Element]] = []
    for view in route.findall("./DataViews/RawDataView"):
        kind, start = view.get(_XSI_TYPE), int(view.get("Start", "-1"))
        if kind == "FixedRawDataView":
            layout[view.get("LogicName", "")] = start
        elif kind == "VaryingRawDataView":
            for offset, fd in enumerate(view.findall("./Varying/FieldDefinitions")):
                layout[fd.get("FieldName", "")] = start + offset
                unit = fd.find("./Restriction")
                if unit is None or unit.get("Unit") != "°":
                    raise UnsupportedFormatError(
                        f"{label}: axis {fd.get('FieldName')!r} is not in degrees. " + _EXPORT_HINT
                    )
        elif kind == "RecordedRawDataView":
            rec = view.find("./Recording")
            if rec is not None:
                counters.append((start, rec))
    missing = {"MeasuredTime", "AbsorptionFactor", "TwoTheta"} - layout.keys()
    if missing:
        raise XRDFormatError(f"{label}: data layout does not declare {sorted(missing)}")
    if len(counters) != 1:
        raise UnsupportedFormatError(
            f"{label}: {len(counters)} recorded channels; expected one counter. " + _EXPORT_HINT
        )
    start, rec = counters[0]
    base = rec.find("./Unit")
    if rec.get("Category") != "Count" or base is None or base.get("Base") != "Counts":
        raise UnsupportedFormatError(
            f"{label}: the recorded channel is not raw counts. " + _EXPORT_HINT
        )
    layout["counts"] = start
    return layout, rec


def _check_scan(raw: ET.Element, route: ET.Element, label: str) -> ET.Element:
    status = raw.findtext("MeasurementStatus")
    if status != "Measured":
        raise XRDFormatError(f"{label}: measurement status is {status!r}, not a completed scan")
    scan = route.find("./ScanInformation")
    if scan is None:
        raise XRDFormatError(f"{label}: no scan information")
    if any(scan.get(a) == "true" for a in ("IsMultiScan", "IsAdvScan", "IsAdvMultiScan")):
        raise UnsupportedFormatError(
            f"{label}: multi-scan files are not supported. " + _EXPORT_HINT
        )
    pair = (scan.get("ScanName"), scan.findtext("ScanMode"))
    if pair not in TESTED_SCANS:
        raise UnsupportedFormatError(
            f"{label}: scan {pair[0]!r} in mode {pair[1]!r} has not been checked against real "
            "files; only coupled 2θ/θ PSD fast scans are read. " + _EXPORT_HINT
        )
    subs = route.findall("./SubScans/SubScanInfo")
    if len(subs) != 1:
        raise UnsupportedFormatError(f"{label}: {len(subs)} sub-scans. " + _EXPORT_HINT)
    if subs[0].get("Steps") != subs[0].get("MeasuredSteps"):
        raise XRDFormatError(
            f"{label}: scan incomplete ({subs[0].get('MeasuredSteps')} of "
            f"{subs[0].get('Steps')} steps measured)"
        )
    return scan


def _data(route: ET.Element, rec: ET.Element, layout: dict[str, int], label: str) -> np.ndarray:
    try:
        rows = np.array(
            [[float(v) for v in (d.text or "").split(",")] for d in route.findall("Datum")],
            dtype=float,
        )
    except ValueError as exc:
        raise XRDFormatError(f"{label}: malformed data row: {exc}") from exc
    if rows.ndim != 2 or rows.shape[1] <= max(layout.values()):
        raise XRDFormatError(f"{label}: data rows do not match the declared layout")
    blank = rec.findtext("BlankingValue")
    if blank is not None and np.any(rows[:, layout["counts"]] == float(blank)):
        raise XRDFormatError(f"{label}: scan contains blanked (unmeasured) points")
    if np.any(rows[:, layout["AbsorptionFactor"]] != 1.0):
        raise UnsupportedFormatError(
            f"{label}: an absorber was in the beam (absorption factor ≠ 1) and its correction "
            "has not been checked against real files. " + _EXPORT_HINT
        )
    t = rows[:, layout["MeasuredTime"]]
    if np.any(t <= 0) or np.ptp(t) > 1e-6 * t.max():
        raise UnsupportedFormatError(
            f"{label}: counting time varies across the scan. " + _EXPORT_HINT
        )
    counts = rows[:, layout["counts"]]
    if np.any(counts < 0) or validators.non_integer_count(counts):
        raise XRDFormatError(f"{label}: the counter column is not whole non-negative counts")
    return rows


def _text(root: ET.Element, path: str, attr: str | None = None) -> str | None:
    el = root.find(path)
    if el is None:
        return None
    return el.get(attr) if attr else el.text


def _metadata(
    container: ET.Element, raw: ET.Element, scan: ET.Element, version: str, time_s: float
) -> dict[str, str]:
    md = {
        "creating_version": version,
        "instrument": _text(container, ".//DeviceTypeDesc"),
        "sample_name": _text(raw, ".//InfoItem[@Name='SampleName']", "Value"),
        "measurement_started": raw.findtext("TimeStampStarted"),
        "measurement_finished": raw.findtext("TimeStampFinished"),
        "scan_type": scan.get("VisibleName"),
        "scan_mode": scan.findtext("ScanMode"),
        "time_per_step_s": str(time_s),
        "file_wavelength_A": _text(raw, ".//WaveLengthAlpha1", "Value"),
        "file_wavelength2_A": _text(raw, ".//WaveLengthAlpha2", "Value"),
        "file_wavelength_average_A": _text(raw, ".//WaveLengthAverage", "Value"),
        "file_wavelength_beta_A": _text(raw, ".//WaveLengthBeta", "Value"),
        "file_ka2_ratio": _text(raw, ".//WaveLengthRatio", "Value"),
        "tube_material": raw.findtext(".//TubeMaterial"),
        "tube_voltage_kV": _text(raw, ".//Voltage", "Value"),
        "tube_current_mA": _text(raw, ".//Current", "Value"),
        "goniometer_radius_mm": _text(raw, ".//Radius", "Value"),
    }
    return {k: v for k, v in md.items() if v}


class BrukerBrmlReader:
    name = "bruker-brml"
    extensions: tuple[str, ...] = (".brml",)
    status = VERIFIED_NATIVE
    description = "Bruker BRML, coupled 2θ/θ PSD fast scan, DIFFRAC version 8.6.3.0 only"

    def supports(self, path: str | os.PathLike[str]) -> bool:
        return Path(path).suffix.lower() in self.extensions

    def _read(
        self, path: str | os.PathLike[str]
    ) -> tuple[np.ndarray, dict[str, int], dict[str, str]]:
        p = Path(path)
        label = p.name
        if p.stat().st_size > MAX_FILE_BYTES:
            raise XRDFormatError(f"{label}: file is larger than {MAX_FILE_BYTES // 2**20} MiB")
        try:
            archive = zipfile.ZipFile(p)
        except zipfile.BadZipFile as exc:
            raise XRDFormatError(f"{label}: not a valid BRML (zip) archive: {exc}") from exc
        with archive as z:
            if any(
                n.startswith("Experiment") and not n.startswith("Experiment0/")
                for n in z.namelist()
            ):
                raise UnsupportedFormatError(
                    f"{label}: several experiments in one file are not supported. " + _EXPORT_HINT
                )
            container = _member(z, _CONTAINER, label)
            version = container.findtext("CreatingVersion") or "unknown"
            if version not in TESTED_CREATING_VERSIONS:
                raise UnsupportedFormatError(
                    f"{label}: written by DIFFRAC version {version}, which has not been checked "
                    f"against real files (checked: {', '.join(TESTED_CREATING_VERSIONS)}). "
                    + _EXPORT_HINT
                )
            raw = _member(z, _raw_data_name(container, label), label)
        routes = raw.findall("./DataRoutes/DataRoute")
        if len(routes) != 1 or routes[0].get("RouteFlag") != "Measured":
            raise UnsupportedFormatError(
                f"{label}: expected one originally measured data route. " + _EXPORT_HINT
            )
        route = routes[0]
        scan = _check_scan(raw, route, label)
        layout, rec = _columns(route, label)
        rows = _data(route, rec, layout, label)
        expected = scan.findtext("MeasurementPoints")
        if expected is not None and int(expected) != rows.shape[0]:
            raise XRDFormatError(f"{label}: {rows.shape[0]} data rows, header says {expected}")
        time_s = float(rows[0, layout["MeasuredTime"]])
        return rows, layout, _metadata(container, raw, scan, version, time_s)

    def metadata(self, path: str | os.PathLike[str]) -> dict[str, str]:
        return self._read(path)[2]

    def probe(
        self, path: str | os.PathLike[str], options: ReadOptions | None = None
    ) -> ProbeResult:
        rows, layout, md = self._read(path)
        keep = [layout["TwoTheta"], layout["counts"]]
        return ProbeResult(
            path=str(path),
            parser=self.name,
            status=self.status,
            n_columns=2,
            n_rows=rows.shape[0],
            header=["2theta_deg", "counts"],
            x_column=0,
            y_column=1,
            axis_quantity="2theta",
            axis_unit="deg",
            intensity_unit="counts",
            metadata=md,
            preview=rows[:5][:, keep].tolist(),
        )

    def parse(self, path: str | os.PathLike[str], options: ReadOptions | None = None) -> XRDPattern:
        p = Path(path)
        opts = options or ReadOptions()
        if opts.axis_quantity not in (None, "2theta") or opts.axis_unit not in (None, "deg"):
            raise XRDFormatError(f"{p.name}: the file declares its axis as 2θ in degrees")
        if opts.intensity_unit and opts.intensity_unit.strip().lower() not in COUNT_UNITS:
            raise XRDFormatError(f"{p.name}: the file stores raw counts, not {opts.intensity_unit}")
        if opts.x_column is not None or opts.y_column is not None:
            raise XRDFormatError(f"{p.name}: the file declares its own columns; do not choose them")
        rows, layout, md = self._read(p)
        x, y = rows[:, layout["TwoTheta"]], rows[:, layout["counts"]]
        issues = validators.check_arrays(x, y)
        errs = validators.errors(issues)
        if errs:
            raise XRDFormatError(f"{p.name}: " + "; ".join(e.message for e in errs))
        md.update(
            {
                "file_name": p.name,
                "file_size_bytes": str(p.stat().st_size),
                "x_column": "TwoTheta",
                "y_column": "counts",
            }
        )
        return XRDPattern(
            name=p.stem,
            two_theta_deg=x,
            intensity=y,
            intensity_unit="counts",
            metadata=md,
            source_path=str(p.resolve()),
            sha256=sha256_file(str(p)),
            parser=self.name,
            format_status=self.status,
            synthetic=False,
            quality_flags=tuple(i.code for i in issues),
        )

    def validate(self, pattern: XRDPattern) -> list[validators.Issue]:
        return validators.check_arrays(pattern.two_theta_deg, pattern.intensity)

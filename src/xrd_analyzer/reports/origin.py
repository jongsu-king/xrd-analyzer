"""A folder from which OriginLab Origin builds a project (.opju) from analysis results.

The .opju format is not public, and OriginLab's OrgLab library, which can write it, may not be
combined with other programs under its EULA. So Origin makes the project itself: the folder holds
the data as CSV, a LabTalk script that imports it, plots it and saves the project, and a launcher
that starts Origin with that script. The folder can be moved to the Windows PC that has Origin.

**Not yet run against real Origin.** Every command follows OriginLab's documentation, and the
launcher is tested on Windows with a stand-in for Origin. Text in these files is plain ASCII,
because the encoding Origin assumes for scripts and CSV files is not documented.
"""

from __future__ import annotations

import csv
import math
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..core.analysis import AnalysisResult
from .figures import _local_bg

SCRIPT_NAME = "xrd_origin.ogs"
LAUNCHER_NAME = "open_in_origin.bat"
LINK_NAME = "open_in_origin.xrdorigin"
README_NAME = "README.txt"
LOG_NAME = "xrd_origin_log.txt"
EXIT_ORIGIN_NOT_FOUND = 9
EXIT_ORIGIN_NOT_STARTED = 10
MAX_LABEL = 100

# Origin's own colour list; the values are arbitrary apart from staying readable.
_BLACK, _RED, _BLUE = 1, 2, 4
_OVERLAY_COLORS = (1, 2, 3, 4, 6, 8, 9, 10, 11, 12, 14, 15)
_PLOT_LINE, _PLOT_SCATTER = 200, 201
_ENTRY = re.compile(r'newbook name:="([A-Za-z0-9 ._+\-]+) pattern" result:=bk(\d+)\$;')
_PROJECT = re.compile(r'string project\$ = here\$\+"\\([A-Za-z0-9._+\-]+\.opju)";')

_IMPORT_OPTIONS = (
    "options.FirstMode:=0 options.HeaderLines.AutoSubHeaderLines:=0 "
    "options.HeaderLines.SubHeaderLines:=2 options.HeaderLines.LongNames:=1 "
    "options.HeaderLines.Units:=2 options.names.FNameToSht:=0 options.names.FNameToBk:=0 "
    "options.names.FNameToBkComm:=0"
)
_DEVICE_NAME = re.compile(r"con|prn|aux|nul|com\d|lpt\d", re.IGNORECASE)
_ASCII_WORDS = {"σ": "sigma", "χ²": "chi2", "√": "sqrt", "×": "x", "≥": ">=", "≤": "<=", "°": "deg"}

_LAUNCHER = rf"""@echo off
rem Written by XRD Analyzer. Starts Origin, which builds the workbooks and graphs from the CSV
rem files in this folder and saves them here as an Origin project (.opju).
setlocal
rem The short form of the folder, on disks that keep one, avoids spaces in what Origin parses.
set "HERE=%~sdp0"
set "HERE=%HERE:~0,-1%"
set "ORIGIN="
if not defined XRD_ORIGIN_EXE goto association
set "ORIGIN=%XRD_ORIGIN_EXE:"=%"
goto found

:association
rem The program Windows opens .opju files with, for this user or for all of them.
call :registered "HKCR\.opju"
if not defined VALUE goto search
call :registered "HKCR\%VALUE%\shell\open\command"
if not defined VALUE goto search
call :associated %VALUE%
if defined ORIGIN goto found

:search
rem The usual installation folders. The last match is the newest version.
for %%P in ("%ProgramW6432%" "%ProgramFiles%") do (
  for /d %%D in ("%%~P\OriginLab\Origin*") do (
    for %%E in ("%%D\Origin*64.exe") do call :consider "%%~E"
  )
)
if defined ORIGIN goto found
goto notfound

:found
if not exist "%ORIGIN%" goto notfound
if defined XRD_ORIGIN_DRYRUN goto dryrun
start "" "%ORIGIN%" -rs run.section("%HERE%\{SCRIPT_NAME}", Main, "%HERE%")
if errorlevel 1 goto notstarted
exit /b 0

:dryrun
echo "%ORIGIN%" -rs run.section("%HERE%\{SCRIPT_NAME}", Main, "%HERE%")
exit /b 0

:notstarted
echo Origin could not be started: %ORIGIN%
if not defined XRD_NO_PAUSE pause
exit /b {EXIT_ORIGIN_NOT_STARTED}

:notfound
echo Origin was not found on this computer.
echo Set XRD_ORIGIN_EXE to the full path of the Origin program, or paste this into
echo Origin's Script Window:
echo run.section("%HERE%\{SCRIPT_NAME}", Main, "%HERE%");
if not defined XRD_NO_PAUSE pause
exit /b {EXIT_ORIGIN_NOT_FOUND}

:registered
rem Sets VALUE to the default value of a registry key, whatever Windows calls that value.
set "VALUE="
set "LINE="
set "REG=%SystemRoot%\System32\reg.exe"
for /f "delims=" %%L in ('%REG% query "%~1" /ve 2^>nul') do set "LINE=%%L"
if not defined LINE exit /b 0
set "LINE=%LINE:*REG_=%"
for /f "tokens=1,*" %%A in ("%LINE%") do set "VALUE=%%B"
exit /b 0

:associated
rem %1 is the registered program. It may be a helper, so Origin beside it comes first.
for %%E in ("%~dp1Origin*64.exe") do call :consider "%%~E"
if not defined ORIGIN call :consider "%~1"
exit /b 0

:consider
rem %1 is a candidate. Origin Viewer cannot run scripts, so it does not count.
if not exist "%~1" exit /b 0
set "CANDIDATE=%~n1"
if not "%CANDIDATE:viewer=%"=="%CANDIDATE%" exit /b 0
set "ORIGIN=%~1"
exit /b 0
"""

_README = f"""XRD Analyzer: results for OriginLab Origin

Windows, with Origin installed:
  Double-click {LAUNCHER_NAME}. Origin opens, builds the workbooks and graphs, and saves the
  project (.opju) in this folder. After that, open the .opju like any Origin project.
  If the XRD Analyzer installer registered it, {LINK_NAME} does the same.

Origin runs on Windows only. On a Mac or Linux PC, copy this whole folder to the Windows PC.
pattern_NN.csv and peaks_NN.csv can also be imported into any other program.

With a Kalpha1/Kalpha2 doublet fit, Height and Area in the peak table are Kalpha1 only.

If a step fails in Origin, the others still run, and {LOG_NAME} in this folder says which one.

Origin 쪽 실행은 아직 실제 Origin으로 시험하지 않았습니다. 문제가 있으면 이 폴더의 {LOG_NAME}
내용과 Origin Script Window에 표시된 오류를 알려 주세요.
"""


@dataclass(frozen=True)
class OriginBundle:
    folder: Path
    launcher: Path
    script: Path
    link: Path
    project: Path  # made by Origin when the script runs; does not exist before that


class OriginUnavailable(RuntimeError):
    """Origin cannot be started from here."""


def _ascii(text: str) -> str:
    for word, plain in _ASCII_WORDS.items():
        text = text.replace(word, plain)
    return text.encode("ascii", "replace").decode("ascii")


def _label(text: Any, fallback: str) -> str:
    """A name safe inside a LabTalk string, where %, $ and quotes have meanings of their own."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._+\-]", "_", str(text or ""))[:MAX_LABEL].strip(" _")
    return cleaned if re.search(r"[A-Za-z0-9]", cleaned) else fallback


def _project_file(stem: str) -> str:
    """A file name Windows accepts, which rules out CON, NUL and the like and a trailing dot."""
    name = re.sub(r"[ +]+", "_", stem).strip("._")
    if _DEVICE_NAME.fullmatch(name.split(".")[0]):
        name = f"xrd_{name}"
    return f"{name}.opju"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _ascii(value)
    v = float(value)
    return f"{v:.10g}" if math.isfinite(v) else ""


def _write_table(
    path: Path, names: Sequence[str], units: Sequence[str], rows: Iterable[Sequence[Any]]
) -> None:
    """CSV with the long names on line 1 and the units on line 2, as the import script expects."""
    with path.open("w", encoding="ascii", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(names)
        w.writerow(units)
        for row in rows:
            w.writerow([_cell(v) for v in row])


def _fit_curve(result: AnalysisResult) -> np.ndarray:
    pr = result.processed
    x = pr.two_theta_deg
    return pr.baseline + result.fit.model(x) + _local_bg(result, x)


def _write_pattern(result: AnalysisResult, path: Path) -> None:
    pr = result.processed
    unit = _ascii(str(result.dataset.get("intensity_unit") or "a.u."))
    signal = pr.ka2_stripped if pr.ka2_stripped is not None else pr.background_subtracted
    fit = _fit_curve(result)
    has_fit = bool(result.fit.peaks)
    rows = (
        (x, s, b, n, f if has_fit else None, s - f if has_fit else None)
        for x, s, b, n, f in zip(pr.two_theta_deg, signal, pr.baseline, pr.net, fit, strict=True)
    )
    _write_table(
        path,
        (r"2\g(q)", "Intensity", "Baseline", "Net intensity", "Fit", "Residual"),
        ("deg", unit, unit, unit, unit, unit),
        rows,
    )


_PEAK_COLUMNS = (
    (r"2\g(q)", "deg", "two_theta_deg"),
    ("Top of fit", "unit", None),
    (r"\g(s)(2\g(q))", "deg", "two_theta_err_deg"),
    ("d", "A", "d_A"),
    (r"\g(s)(d)", "A", "d_err_A"),
    ("FWHM", "deg", "fwhm_deg"),
    (r"\g(s)(FWHM)", "deg", "fwhm_err_deg"),
    ("eta", "", "eta"),
    ("Height", "unit", "height"),
    (r"\g(s)(Height)", "unit", "height_err"),
    ("Area", "unit deg", "area"),
    (r"\g(s)(Area)", "unit deg", "area_err"),
    ("Significance", "sigma", "significance_sigma"),
    ("Scherrer size", "nm", "scherrer_nm"),
    (r"\g(s)(size)", "nm", "scherrer_err_nm"),
    ("Scherrer status", "", "scherrer_status"),
    ("Flags", "", "flags"),
)
_PEAK_TOP_COLUMN = 2


def _write_peaks(result: AnalysisResult, path: Path) -> bool:
    rows = [r for r in result.peak_rows() if r["d_A"] is not None]
    if not rows:
        return False
    unit = _ascii(str(result.dataset.get("intensity_unit") or "a.u."))
    x, fit = result.processed.two_theta_deg, _fit_curve(result)
    table = []
    for r in rows:
        center = r["two_theta_deg"]
        # the marker sits on the curve, so its height includes the baseline and any Kα2 overlap
        top = float(np.interp(center, x, fit))
        table.append([top if key is None else r[key] for _, _, key in _PEAK_COLUMNS])
    _write_table(
        path,
        [name for name, _, _ in _PEAK_COLUMNS],
        [u.replace("unit", unit) for _, u, _ in _PEAK_COLUMNS],
        table,
    )
    return True


def _guarded(step: str, body: Sequence[str]) -> list[str]:
    """``body`` in braces. LabTalk skips to the closing brace on an error and carries on from it."""
    return [
        "done = 0;",
        "{",
        f'  step$ = "{step}";',
        *(f"  {line}" for line in body),
        "  done = 1;",
        "}",
        'if (done == 0) { failures = failures + 1; type "FAILED: %(step$)"; }',
    ]


def _import(book: str, label: str, table: str) -> list[str]:
    return [
        f'newbook name:="{label}" result:={book}$;',
        f'table$ = here$+"\\{table}";',
        f"impCSV fname:=table$ {_IMPORT_OPTIONS};",
    ]


def _script(entries: Sequence[tuple[str, str, str | None]], project_file: str) -> str:
    # Variables declared inside braces end with them, so these come first.
    lines = [
        "// Written by XRD Analyzer. Builds an Origin project from the CSV files in this folder.",
        f"// Started by {LAUNCHER_NAME}, or in the Script Window with:",
        f'//   run.section("<this folder>\\{SCRIPT_NAME}", Main, "<this folder>");',
        "[Main]",
        'string here$ = "%1";',
        'string step$ = "start";',
        "string table$;",
        f'string logfile$ = here$+"\\{LOG_NAME}";',
        f'string project$ = here$+"\\{project_file}";',
        "int done = 0;",
        "int failures = 0;",
        "{",
        '  type -gbe "%(logfile$)";',
        "}",
        'type "XRD Analyzer: building the project";',
    ]
    for i, (_, _, peaks) in enumerate(entries, 1):
        lines.append(f"string bk{i}$;")
        if peaks is not None:
            lines.append(f"string pk{i}$;")
    for i, (label, pattern, peaks) in enumerate(entries, 1):
        lines += _guarded(
            f"scan {i}, import {pattern}", _import(f"bk{i}", f"{label} pattern", pattern)
        )
        if peaks is not None:
            lines += _guarded(
                f"scan {i}, import {peaks}", _import(f"pk{i}", f"{label} peaks", peaks)
            )
        # Curves first: without the peak table, the graph still has them and a legend.
        graph = [
            "win -t Plot;",
            f'page.longname$ = "{label}";',
            f"plotxy [bk{i}$]1!(1,2) plot:={_PLOT_LINE} rescale:=1 color:={_BLACK} ogl:=!1;",
            f"plotxy [bk{i}$]1!(1,3) plot:={_PLOT_LINE} rescale:=1 color:={_BLUE} ogl:=!1;",
            f"plotxy [bk{i}$]1!(1,5) plot:={_PLOT_LINE} rescale:=1 color:={_RED} ogl:=!1 "
            "legend:=1;",
        ]
        if peaks is not None:
            graph.append(
                f"plotxy [pk{i}$]1!(1,{_PEAK_TOP_COLUMN}) plot:={_PLOT_SCATTER} rescale:=1 "
                f"color:={_RED} ogl:=!1 legend:=1;"
            )
        lines += _guarded(f"scan {i}, graph", graph)
    if len(entries) > 1:
        lines += _guarded(
            "overlay, new graph", ["win -t Plot;", 'page.longname$ = "All scans, net intensity";']
        )
        for i, _ in enumerate(entries, 1):
            color = _OVERLAY_COLORS[(i - 1) % len(_OVERLAY_COLORS)]
            lines += _guarded(
                f"overlay, scan {i}",
                [
                    f"plotxy [bk{i}$]1!(1,4) plot:={_PLOT_LINE} rescale:=1 color:={color} "
                    "ogl:=!1 legend:=1;"
                ],
            )
    # Last, so that the workbooks and graphs are there even if saving fails.
    lines += _guarded(
        "save the project",
        ['save -DIX "%(project$)";', f'type "XRD Analyzer: project saved as {project_file}";'],
    )
    lines += [
        f'if (failures > 0) type -a "XRD Analyzer: $(failures) step(s) failed, see {LOG_NAME}";',
        "type -ge;",
    ]
    return "\r\n".join(lines) + "\r\n"


def _launcher_bytes() -> bytes:
    return _LAUNCHER.replace("\n", "\r\n").encode("ascii")


def write_origin_bundle(
    results: Sequence[AnalysisResult], folder: str | os.PathLike[str]
) -> OriginBundle:
    """Write the folder for ``results``: one project with their workbooks and graphs."""
    if not results:
        raise ValueError("nothing analysed yet")
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    if (d / LAUNCHER_NAME).is_file():  # tables from an earlier export would look like results
        for old in (*d.glob("pattern_[0-9]*.csv"), *d.glob("peaks_[0-9]*.csv")):
            old.unlink()
    entries: list[tuple[str, str, str | None]] = []
    for i, result in enumerate(results, 1):
        label = _label(result.dataset.get("name"), f"Dataset {i}")
        pattern, peaks = f"pattern_{i:02d}.csv", f"peaks_{i:02d}.csv"
        _write_pattern(result, d / pattern)
        entries.append((label, pattern, peaks if _write_peaks(result, d / peaks) else None))
    stem = entries[0][0] if len(entries) == 1 else "xrd_analysis"
    project_file = _project_file(stem)
    script, launcher, link = d / SCRIPT_NAME, d / LAUNCHER_NAME, d / LINK_NAME
    script.write_bytes(_script(entries, project_file).encode("ascii"))
    launcher.write_bytes(_launcher_bytes())
    link.write_bytes(
        b"XRD Analyzer folder for Origin. Double-click to open it in Origin, or run "
        + LAUNCHER_NAME.encode("ascii")
        + b".\r\n"
    )
    (d / README_NAME).write_text(_README, encoding="utf-8-sig", newline="\r\n")
    return OriginBundle(d, launcher, script, link, d / project_file)


def _is_our_script(raw: bytes) -> bool:
    """Is this byte for byte what ``_script`` writes? Only safe names are read back from it."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return False
    entries: list[tuple[str, str, str | None]] = []
    for m in _ENTRY.finditer(text):
        i = int(m[2])
        peaks = f"peaks_{i:02d}.csv" if f"result:=pk{i}$;" in text else None
        entries.append((m[1], f"pattern_{i:02d}.csv", peaks))
    project = _PROJECT.search(text)
    if not entries or project is None:
        return False
    return _script(entries, project[1]).encode("ascii") == raw


def _launcher_for(path: str | os.PathLike[str]) -> Path:
    """The launcher of a bundle, if it is safe to run.

    A double-clicked .xrdorigin file runs what lies beside it, and unlike a downloaded .bat it gets
    no warning from Windows. So only files this version writes itself are run.
    """
    p = Path(path)
    launcher = (p if p.is_dir() else p.parent) / LAUNCHER_NAME
    if not launcher.is_file():
        raise OriginUnavailable(f"{launcher} not found: this is not an XRD Analyzer Origin folder")
    script = launcher.parent / SCRIPT_NAME
    if (
        launcher.read_bytes() != _launcher_bytes()
        or not script.is_file()
        or not _is_our_script(script.read_bytes())
    ):
        raise OriginUnavailable(
            f"{launcher.parent} was not written by this version of XRD Analyzer, so it was not "
            f"started. Export the results again, or run {LAUNCHER_NAME} yourself if you trust "
            "this folder."
        )
    if sys.platform != "win32":
        raise OriginUnavailable(
            f"Origin runs on Windows only. Copy {launcher.parent} to the Windows PC and run "
            f"{LAUNCHER_NAME} there."
        )
    return launcher


def start_origin(path: str | os.PathLike[str]) -> None:
    """Start Origin on a bundle without waiting. The launcher's console can show its messages."""
    launcher = _launcher_for(path)
    if sys.platform == "win32":  # repeated here for type checkers: startfile exists only there
        os.startfile(str(launcher))


def run_launcher(path: str | os.PathLike[str]) -> int:
    """Run a bundle's launcher and return its exit code (9 when Origin is not found)."""
    launcher = _launcher_for(path)
    shell = os.environ.get("COMSPEC", "cmd.exe")
    folder = launcher.parent
    if str(folder.resolve()).startswith("\\\\"):  # cmd cannot start in a network folder
        command = f'"{shell}" /d /s /c ""{launcher}""'
        return subprocess.run(command, check=False).returncode
    # By its bare name from its own folder: cmd would parse a full path, and & in it ends a command.
    return subprocess.run([shell, "/d", "/c", LAUNCHER_NAME], cwd=folder, check=False).returncode


__all__ = [
    "EXIT_ORIGIN_NOT_FOUND",
    "EXIT_ORIGIN_NOT_STARTED",
    "LAUNCHER_NAME",
    "LINK_NAME",
    "LOG_NAME",
    "SCRIPT_NAME",
    "OriginBundle",
    "OriginUnavailable",
    "run_launcher",
    "start_origin",
    "write_origin_bundle",
]

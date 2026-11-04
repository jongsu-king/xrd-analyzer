"""Origin bundle: files, script and launcher. Real Origin is not available in tests.

The launcher runs on Windows runners against a stand-in program, which checks everything up to
the point where Origin would take over. It does not show that Origin accepts the script.
"""

import csv
import io
import json
import locale
import os
import re
import subprocess
import sys
import tempfile
import time

import numpy as np
import pytest

from xrd_analyzer.cli import main as cli_main
from xrd_analyzer.core.analysis import run_analysis
from xrd_analyzer.core.synthetic import make_example
from xrd_analyzer.models.dataset import XRDPattern
from xrd_analyzer.models.recipe import AnalysisRecipe
from xrd_analyzer.reports.origin import (
    EXIT_ORIGIN_NOT_FOUND,
    LAUNCHER_NAME,
    LOG_NAME,
    SCRIPT_NAME,
    OriginUnavailable,
    run_launcher,
    start_origin,
    write_origin_bundle,
)

windows = pytest.mark.skipif(sys.platform != "win32", reason="the launcher is a Windows batch file")
not_windows = pytest.mark.skipif(sys.platform == "win32", reason="Origin can start on Windows")


@pytest.fixture(scope="module")
def result():
    ex = make_example("si_like")
    return run_analysis(ex.pattern, ex.recipe)


def _named(result, name):
    r = run_analysis(
        XRDPattern(
            name,
            result.processed.two_theta_deg,
            result.processed.raw,
            "counts",
        ),
        AnalysisRecipe.from_dict(result.recipe),
    )
    return r


def _rows(path):
    return list(csv.reader(io.StringIO(path.read_text(encoding="ascii"))))


def test_bundle_holds_everything_needed(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    names = {p.name for p in b.folder.iterdir()}
    assert {"pattern_01.csv", "peaks_01.csv", "xrd_origin.ogs", LAUNCHER_NAME} <= names
    assert {"open_in_origin.xrdorigin", "README.txt"} <= names
    assert b.project.suffix == ".opju"
    assert not b.project.exists()  # only Origin can make it


def test_nothing_to_export_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="nothing analysed"):
        write_origin_bundle([], tmp_path / "out")


def test_pattern_table_matches_the_analysis(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    rows = _rows(b.folder / "pattern_01.csv")
    assert rows[0] == ["2\\g(q)", "Intensity", "Baseline", "Net intensity", "Fit", "Residual"]
    assert rows[1] == ["deg", "counts", "counts", "counts", "counts", "counts"]
    data = np.array(rows[2:], dtype=float)
    pr = result.processed
    assert data.shape == (pr.two_theta_deg.size, 6)
    assert np.allclose(data[:, 0], pr.two_theta_deg)
    assert np.allclose(data[:, 1], pr.background_subtracted)
    assert np.allclose(data[:, 2], pr.baseline)
    assert np.allclose(data[:, 3], pr.net)
    assert np.allclose(data[:, 5], data[:, 1] - data[:, 4], atol=1e-5)
    # the fit follows the data: it is what the marked peaks are measured on
    assert np.abs(data[:, 5]).max() < 0.2 * data[:, 1].max()


def test_peak_table_matches_the_analysis(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    rows = _rows(b.folder / "peaks_01.csv")
    names = rows[0]
    peaks = [p for p in result.peak_rows() if p["d_A"] is not None]
    assert len(rows) - 2 == len(peaks)
    assert names[0] == "2\\g(q)" and "Height" in names and "Flags" in names
    i_d, i_h = names.index("d"), names.index("Height")
    assert rows[1][names.index("Area")] == "counts deg"
    for row, p in zip(rows[2:], peaks, strict=True):
        assert float(row[0]) == pytest.approx(p["two_theta_deg"])
        assert float(row[i_d]) == pytest.approx(p["d_A"])
        assert float(row[i_h]) == pytest.approx(p["height"])
        assert float(row[1]) >= float(row[i_h])  # the marker sits on the curve, above its baseline


def test_files_are_ascii_with_windows_line_ends(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    for name in ("pattern_01.csv", "peaks_01.csv", "xrd_origin.ogs", LAUNCHER_NAME):
        raw = (b.folder / name).read_bytes()
        raw.decode("ascii")
        assert raw.count(b"\n") == raw.count(b"\r\n") > 0, name


def test_script_uses_only_the_documented_commands(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    script = b.script.read_text(encoding="ascii")
    assert script.count("[Main]") == 1 and 'string here$ = "%1";' in script
    assert f'newbook name:="{result.dataset["name"]} pattern" result:=bk1$;' in script
    assert script.count("impCSV fname:=table$ ") == 2
    assert 'table$ = here$+"\\pattern_01.csv";' in script
    for option in (
        "options.HeaderLines.AutoSubHeaderLines:=0",
        "options.HeaderLines.SubHeaderLines:=2",
        "options.HeaderLines.LongNames:=1",
        "options.HeaderLines.Units:=2",
    ):
        assert script.count(option) == 2
    assert script.count("win -t Plot;") == 1
    assert "plot:=200" in script and "plot:=201" in script and "ogl:=!1" in script
    assert 'save -DIX "%(project$)";' in script
    # saving comes last so a failed save still leaves the workbooks and graphs
    assert script.index("save -DIX") > script.rindex("plotxy")


def test_script_survives_a_failing_step_and_logs_it(result, tmp_path):
    lines = write_origin_bundle([result], tmp_path / "out").script.read_text("ascii").splitlines()
    assert f'string logfile$ = here$+"\\{LOG_NAME}";' in lines
    assert lines.index('  type -gbe "%(logfile$)";') < lines.index(
        'type "XRD Analyzer: building the project";'
    )
    assert lines[-1] == "type -ge;"
    assert f"see {LOG_NAME}" in lines[-2] and lines[-2].startswith("if (failures > 0) type -a ")
    # every X-Function, plot and save sits inside braces, which LabTalk leaves on an error
    depth = 0
    for line in lines[lines.index("[Main]") + 1 :]:
        if line.startswith(("if (done", "if (failures")):
            continue
        depth += line.count("{") - line.count("}")
        if line.lstrip().startswith(("newbook", "impCSV", "plotxy", "win -t", "save -")):
            assert depth == 1, line
    assert depth == 0
    # the block sets done last, so an error leaves it at 0 and is counted
    steps = [i for i, line in enumerate(lines) if line == "}"]
    for i in steps[1:]:
        assert lines[i - 1] == "  done = 1;"
        assert lines[i + 1].startswith("if (done == 0) { failures = failures + 1;")
    # declared before the braces: what is declared inside them ends with them
    assert lines.index("string bk1$;") < lines.index('  step$ = "scan 1, import pattern_01.csv";')
    assert lines.index("int done = 0;") < lines.index("{")


def test_folder_can_be_moved(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    for name in ("xrd_origin.ogs", LAUNCHER_NAME):
        assert str(tmp_path) not in (b.folder / name).read_text(encoding="ascii")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Sample+ A_1", 'name:="Sample+ A_1 pattern"'),
        ('a"b%c$d', 'name:="a_b_c_d pattern"'),
        ("실리콘 시료", 'name:="Dataset 1 pattern"'),
        ("..", 'name:="Dataset 1 pattern"'),
    ],
)
def test_names_cannot_break_the_script(result, tmp_path, name, expected):
    b = write_origin_bundle([_named(result, name)], tmp_path / "out")
    assert expected in b.script.read_text(encoding="ascii")
    b.project.name.encode("ascii")


@pytest.mark.parametrize(
    ("name", "project"),
    [
        ("CON", "xrd_CON.opju"),
        ("nul.txt", "xrd_nul.txt.opju"),
        ("com1", "xrd_com1.opju"),
        ("scan.", "scan.opju"),
        ("Sample+ A_1", "Sample_A_1.opju"),
    ],
)
def test_project_name_is_one_windows_accepts(result, tmp_path, name, project):
    assert write_origin_bundle([_named(result, name)], tmp_path / "o").project.name == project


def test_long_names_are_shortened(result, tmp_path):
    b = write_origin_bundle([_named(result, "x" * 500)], tmp_path / "o")
    assert b.project.name == "x" * 100 + ".opju"


def test_an_earlier_export_leaves_no_tables_behind(result, tmp_path):
    results = [_named(result, "a"), _named(result, "b")]
    folder = tmp_path / "o"
    write_origin_bundle(results, folder)
    assert (folder / "pattern_02.csv").is_file()
    write_origin_bundle(results[:1], folder)
    assert not (folder / "pattern_02.csv").exists() and not (folder / "peaks_02.csv").exists()


def test_other_csv_files_in_a_plain_folder_are_kept(result, tmp_path):
    folder = tmp_path / "o"
    folder.mkdir()
    (folder / "pattern_07.csv").write_text("mine", encoding="ascii")
    write_origin_bundle([result], folder)
    assert (folder / "pattern_07.csv").read_text(encoding="ascii") == "mine"


def test_several_scans_share_one_project_with_an_overlay(result, tmp_path):
    results = [_named(result, "scan A"), _named(result, "scan B"), _named(result, "scan C")]
    b = write_origin_bundle(results, tmp_path / "out")
    script = b.script.read_text(encoding="ascii")
    assert b.project.name == "xrd_analysis.opju"
    assert (b.folder / "pattern_03.csv").is_file() and (b.folder / "peaks_03.csv").is_file()
    assert script.count("win -t Plot;") == 4  # one per scan and the overlay
    for i in (1, 2, 3):
        assert f"plotxy [bk{i}$]1!(1,4)" in script
        assert f'"overlay, scan {i}"' in script  # one failed scan does not stop the others
    assert script.count("save -DIX") == 1


def test_scan_without_peaks_still_exports(tmp_path):
    x = np.round(np.arange(20.0, 30.0, 0.02), 4)
    pat = XRDPattern(
        "flat", x, np.random.default_rng(0).poisson(100, x.size).astype(float), "counts"
    )
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = 1.5406
    b = write_origin_bundle([run_analysis(pat, r)], tmp_path / "out")
    script = b.script.read_text(encoding="ascii")
    assert not (b.folder / "peaks_01.csv").exists()
    assert "plot:=201" not in script and "pk1$" not in script
    rows = _rows(b.folder / "pattern_01.csv")
    assert rows[2][4] == "" and rows[2][5] == ""  # no fit to show


def test_non_ascii_flags_are_spelled_out(result, tmp_path):
    from xrd_analyzer.reports.origin import _ascii

    assert _ascii("low_significance (height < 3σ)") == "low_significance (height < 3sigma)"
    assert _ascii("chi2 χ²") == "chi2 chi2"
    assert _ascii("시료") == "??"


# ------------------------------------------------------------------ CLI


def test_cli_analyze_writes_the_folder(si_csv, tmp_path, capsys):
    out = tmp_path / "o"
    code = cli_main(
        [
            "analyze",
            str(si_csv),
            "--wavelength",
            "cu_ka1",
            "--no-pdf",
            "--origin",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert (out / "origin" / LAUNCHER_NAME).is_file()
    assert LAUNCHER_NAME in capsys.readouterr().out


def test_cli_batch_writes_one_project(si_csv, tmp_path):
    out = tmp_path / "o"
    args = ["batch", str(si_csv), str(si_csv), "--wavelength", "cu_ka1", "--no-pdf", "--origin"]
    code = cli_main([*args, "--out", str(out)])
    assert code == 0
    assert (out / "origin" / "pattern_02.csv").is_file()
    assert "xrd_analysis.opju" in (out / "origin" / "xrd_origin.ogs").read_text(encoding="ascii")


def test_cli_has_no_folder_without_the_option(si_csv, tmp_path):
    out = tmp_path / "o"
    assert (
        cli_main(["analyze", str(si_csv), "--wavelength", "cu_ka1", "--no-pdf", "--out", str(out)])
        == 0
    )
    assert not (out / "origin").exists()


def test_open_refuses_a_folder_that_is_not_a_bundle(tmp_path, capsys):
    assert cli_main(["open-in-origin", str(tmp_path)]) == 2
    assert "not an XRD Analyzer Origin folder" in capsys.readouterr().err


@not_windows
def test_open_explains_that_origin_needs_windows(result, tmp_path, capsys):
    b = write_origin_bundle([result], tmp_path / "out")
    assert cli_main(["open-in-origin", str(b.link)]) == 2
    assert "Windows only" in capsys.readouterr().err
    with pytest.raises(OriginUnavailable, match="Windows only"):
        run_launcher(b.folder)


# ------------------------------------------------------------------ only our own files run


@pytest.mark.parametrize(
    "tamper",
    [
        lambda b: b.launcher.write_bytes(b"@echo off\r\ncalc.exe\r\n"),
        lambda b: b.launcher.write_bytes(b.launcher.read_bytes() + b"calc.exe\r\n"),
        lambda b: b.script.write_bytes(b.script.read_bytes() + b'system "calc.exe";\r\n'),
        lambda b: b.script.write_bytes(
            b.script.read_bytes().replace(b"[Main]\r\n", b'[Main]\r\nsystem "calc.exe";\r\n')
        ),
        # a label is read back from the script, so it must not carry code into the comparison
        lambda b: b.script.write_bytes(
            b.script.read_bytes().replace(b'name:="scan', b'name:="x"; system "calc.exe"; "scan')
        ),
        lambda b: b.script.unlink(),
        lambda b: b.script.write_bytes(b"\xff\xfe"),
    ],
)
def test_a_changed_folder_is_not_started(result, tmp_path, tamper, monkeypatch):
    b = write_origin_bundle([_named(result, "scan")], tmp_path / "out")
    tamper(b)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("the launcher was started"))
    with pytest.raises(OriginUnavailable, match="not written by this version"):
        run_launcher(b.link)
    with pytest.raises(OriginUnavailable, match="not written by this version"):
        start_origin(b.folder)


def test_cli_explains_a_refused_folder(result, tmp_path, capsys):
    b = write_origin_bundle([result], tmp_path / "out")
    b.script.write_bytes(b.script.read_bytes() + b'system "calc.exe";\r\n')
    assert cli_main(["open-in-origin", str(b.link)]) == 2
    assert "not written by this version" in capsys.readouterr().err


@pytest.mark.parametrize("names", [["one"], ["one", "two", "three"], ["a b+c.d-e_f", "x"]])
def test_every_script_we_write_is_recognised(result, tmp_path, names):
    from xrd_analyzer.reports.origin import _is_our_script

    b = write_origin_bundle([_named(result, n) for n in names], tmp_path / "out")
    assert _is_our_script(b.script.read_bytes())


def test_a_scan_without_peaks_is_recognised(tmp_path):
    from xrd_analyzer.reports.origin import _is_our_script

    x = np.round(np.arange(20.0, 30.0, 0.02), 4)
    pat = XRDPattern(
        "flat", x, np.random.default_rng(0).poisson(100, x.size).astype(float), "counts"
    )
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = 1.5406
    b = write_origin_bundle([run_analysis(pat, r)], tmp_path / "out")
    assert _is_our_script(b.script.read_bytes())


# ------------------------------------------------------------------ launcher, on Windows


def _env(tmp_path, programs=None, **extra):
    """os.environ keys are upper case on Windows; another spelling would not replace them."""
    folder = str(programs or tmp_path / "no programs")
    env = {**os.environ, "XRD_NO_PAUSE": "1", "PROGRAMFILES": folder, "PROGRAMW6432": folder}
    env.pop("XRD_ORIGIN_EXE", None)
    env.pop("XRD_ORIGIN_DRYRUN", None)
    return {**env, **extra}


def _run(launcher, env):
    """Output goes to a file: what the launcher starts inherits the handle, and a pipe would stay
    open until that program exits, past any timeout."""
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen([str(launcher)], env=env, stdout=out, stderr=subprocess.STDOUT)
        try:
            code = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], check=False)
            pytest.fail("the launcher did not finish")
        out.seek(0)
        text = out.read().decode(locale.getpreferredencoding(False), "replace")
    return subprocess.CompletedProcess(launcher, code, text, "")


def _section_paths(text):
    """The script and folder in a run.section command; the folder may be in its short form."""
    m = re.search(rf'run\.section\("(.+)\\{SCRIPT_NAME}", Main, "(.+)"\)', text)
    assert m, text
    assert m[1] == m[2]
    return m[1]


def _fake_program(folder, name="Origin64.exe"):
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / name
    exe.write_bytes(b"")
    return exe


@windows
def test_launcher_reports_missing_origin(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "my results (1)")
    done = _run(b.launcher, _env(tmp_path))
    assert done.returncode == EXIT_ORIGIN_NOT_FOUND
    assert "Origin was not found" in done.stdout
    assert os.path.samefile(_section_paths(done.stdout), b.folder)


@windows
def test_launcher_rejects_a_wrong_path(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_EXE=str(tmp_path / "missing.exe")))
    assert done.returncode == EXIT_ORIGIN_NOT_FOUND


@windows
def test_launcher_builds_the_documented_command(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "my results (1)")
    exe = _fake_program(tmp_path / "Program (x86)")
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_EXE=str(exe), XRD_ORIGIN_DRYRUN="1"))
    assert done.returncode == 0
    assert done.stdout.startswith(f'"{exe}" -rs run.section("')
    assert os.path.samefile(_section_paths(done.stdout), b.folder)


@windows
def test_launcher_accepts_a_quoted_program_path(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    exe = _fake_program(tmp_path / "Program (x86)")
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_EXE=f'"{exe}"', XRD_ORIGIN_DRYRUN="1"))
    assert done.returncode == 0
    assert done.stdout.startswith(f'"{exe}" -rs ')


@windows
def test_launcher_finds_the_newest_installed_version(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    programs = tmp_path / "Programs"
    _fake_program(programs / "OriginLab" / "Origin2022", "Origin95_64.exe")
    _fake_program(programs / "OriginLab" / "Origin2024b")
    done = _run(b.launcher, _env(tmp_path, programs, XRD_ORIGIN_DRYRUN="1"))
    assert done.returncode == 0
    assert "Origin2024b" in done.stdout and "Origin64.exe" in done.stdout


@windows
def test_launcher_passes_over_origin_viewer(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    programs = tmp_path / "Programs"
    _fake_program(programs / "OriginLab" / "Origin2024")
    _fake_program(programs / "OriginLab" / "OriginViewer", "OriginViewer64.exe")
    done = _run(b.launcher, _env(tmp_path, programs, XRD_ORIGIN_DRYRUN="1"))
    assert done.returncode == 0
    assert "Origin2024" in done.stdout and "Viewer" not in done.stdout


@pytest.fixture
def opju_opens_with():
    """Register a program for .opju for this user, as an Origin installation does."""
    import winreg

    classes = r"Software\Classes"
    root = winreg.HKEY_CURRENT_USER
    if subprocess.run("assoc .opju", shell=True, capture_output=True, check=False).returncode == 0:
        pytest.skip(".opju is already registered on this machine")

    def register(command):
        winreg.SetValue(root, rf"{classes}\.opju", winreg.REG_SZ, "XRDTest.OPJU")
        winreg.SetValue(root, rf"{classes}\XRDTest.OPJU\shell\open\command", winreg.REG_SZ, command)

    yield register
    for key in (
        r"XRDTest.OPJU\shell\open\command",
        r"XRDTest.OPJU\shell\open",
        r"XRDTest.OPJU\shell",
        "XRDTest.OPJU",
        ".opju",
    ):
        winreg.DeleteKey(root, rf"{classes}\{key}")


@windows
def test_launcher_uses_the_program_registered_for_opju(result, tmp_path, opju_opens_with):
    b = write_origin_bundle([result], tmp_path / "out")
    exe = _fake_program(tmp_path / "D drive" / "My Origin (2024)")
    opju_opens_with(f'"{exe}" "%1"')
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_DRYRUN="1"))
    assert done.returncode == 0
    assert done.stdout.startswith(f'"{exe}" -rs ')


@windows
def test_launcher_prefers_origin_beside_a_helper_program(result, tmp_path, opju_opens_with):
    b = write_origin_bundle([result], tmp_path / "out")
    exe = _fake_program(tmp_path / "Origin")
    helper = _fake_program(tmp_path / "Origin", "FileOpener.exe")
    opju_opens_with(f'"{helper}" "%1"')
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_DRYRUN="1"))
    assert done.returncode == 0
    assert done.stdout.startswith(f'"{exe}" -rs ')


@windows
def test_launcher_passes_over_a_registered_viewer(result, tmp_path, opju_opens_with):
    b = write_origin_bundle([result], tmp_path / "out")
    viewer = _fake_program(tmp_path / "Viewer", "OriginViewer.exe")
    opju_opens_with(f'"{viewer}" "%1"')
    programs = tmp_path / "Programs"
    _fake_program(programs / "OriginLab" / "Origin2024")
    done = _run(b.launcher, _env(tmp_path, programs, XRD_ORIGIN_DRYRUN="1"))
    assert done.returncode == 0
    assert "Origin2024" in done.stdout and "Viewer" not in done.stdout


@windows
def test_launcher_really_starts_the_program_with_korean_and_spaces(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "XRD 결과 (1)")
    record = tmp_path / "args.json"
    recorder = tmp_path / "record.py"
    recorder.write_text(  # the raw command line: argv would have lost the quotes Origin sees
        "import ctypes, json\n"
        "line = ctypes.windll.kernel32.GetCommandLineW\n"
        "line.restype = ctypes.c_wchar_p\n"
        f"json.dump(line(), open({str(record)!r}, 'w', encoding='utf-8'))\n",
        encoding="utf-8",
    )
    # start runs a batch file in cmd /k, whose window stays open unless the file ends with exit
    fake = tmp_path / "fake origin.cmd"
    fake.write_text(f'@"{sys.executable}" "{recorder}" %*\r\n@exit\r\n', encoding="ascii")
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_EXE=str(fake)))
    assert done.returncode == 0, done.stdout
    deadline = time.time() + 30
    while not record.exists() and time.time() < deadline:
        time.sleep(0.2)
    line = json.loads(record.read_text(encoding="utf-8"))
    assert " -rs run.section(" in line
    assert os.path.samefile(_section_paths(line), b.folder)


@windows
def test_cli_opens_a_bundle_through_its_link_file(result, tmp_path, monkeypatch, capfd):
    b = write_origin_bundle([result], tmp_path / "out")
    monkeypatch.setenv("XRD_ORIGIN_EXE", str(_fake_program(tmp_path)))
    monkeypatch.setenv("XRD_ORIGIN_DRYRUN", "1")
    assert cli_main(["open-in-origin", str(b.link)]) == 0
    assert "-rs run.section(" in capfd.readouterr().out


@windows
@pytest.mark.parametrize("folder", ["a&b", "a b&c (1)", "x%PATH%y", "^caret", "semi;colon,comma"])
def test_cli_copes_with_a_folder_name_that_means_something_to_cmd(
    result, tmp_path, monkeypatch, capfd, folder
):
    b = write_origin_bundle([result], tmp_path / folder)
    monkeypatch.setenv("XRD_ORIGIN_EXE", str(_fake_program(tmp_path)))
    monkeypatch.setenv("XRD_ORIGIN_DRYRUN", "1")
    assert cli_main(["open-in-origin", str(b.link)]) == 0
    assert os.path.samefile(_section_paths(capfd.readouterr().out), b.folder)


@windows
def test_cli_opens_a_bundle_on_a_network_path(result, tmp_path, monkeypatch, capfd):
    b = write_origin_bundle([result], tmp_path / "out")
    drive, rest = os.path.splitdrive(str(b.folder.resolve()))
    unc = rf"\\localhost\{drive[0]}${rest}"
    if not os.path.isdir(unc):
        pytest.skip("administrative shares are not reachable here")
    monkeypatch.setenv("XRD_ORIGIN_EXE", str(_fake_program(tmp_path)))
    monkeypatch.setenv("XRD_ORIGIN_DRYRUN", "1")
    assert cli_main(["open-in-origin", unc]) == 0
    assert "-rs run.section(" in capfd.readouterr().out

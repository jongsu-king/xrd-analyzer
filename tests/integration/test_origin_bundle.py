"""Origin bundle: files, script and launcher. Real Origin is not available in tests.

The launcher runs on Windows runners against a stand-in program, which checks everything up to
the point where Origin would take over. It does not show that Origin accepts the script.
"""

import csv
import io
import json
import os
import subprocess
import sys
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
    OriginUnavailable,
    run_launcher,
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
    assert script.count("impCSV fname:=here$+") == 2
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
    ],
)
def test_names_cannot_break_the_script(result, tmp_path, name, expected):
    b = write_origin_bundle([_named(result, name)], tmp_path / "out")
    assert expected in b.script.read_text(encoding="ascii")
    b.project.name.encode("ascii")


def test_several_scans_share_one_project_with_an_overlay(result, tmp_path):
    results = [_named(result, "scan A"), _named(result, "scan B"), _named(result, "scan C")]
    b = write_origin_bundle(results, tmp_path / "out")
    script = b.script.read_text(encoding="ascii")
    assert b.project.name == "xrd_analysis.opju"
    assert (b.folder / "pattern_03.csv").is_file() and (b.folder / "peaks_03.csv").is_file()
    assert script.count("win -t Plot;") == 4  # one per scan and the overlay
    for i in (1, 2, 3):
        assert f"plotxy [bk{i}$]1!(1,4)" in script
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


# ------------------------------------------------------------------ launcher, on Windows


def _env(tmp_path, **extra):
    env = {**os.environ, "XRD_NO_PAUSE": "1", "ProgramFiles": str(tmp_path / "no programs")}
    env.pop("XRD_ORIGIN_EXE", None)
    env.pop("XRD_ORIGIN_DRYRUN", None)
    return {**env, **extra}


def _run(launcher, env):
    return subprocess.run(
        [str(launcher)], env=env, capture_output=True, text=True, timeout=60, check=False
    )


@windows
def test_launcher_reports_missing_origin(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "my results (1)")
    done = _run(b.launcher, _env(tmp_path))
    assert done.returncode == EXIT_ORIGIN_NOT_FOUND
    assert "Origin was not found" in done.stdout
    assert f'run.section("{b.folder}\\xrd_origin.ogs", Main, "{b.folder}");' in done.stdout


@windows
def test_launcher_rejects_a_wrong_path(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_EXE=str(tmp_path / "missing.exe")))
    assert done.returncode == EXIT_ORIGIN_NOT_FOUND


@windows
def test_launcher_builds_the_documented_command(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "my results (1)")
    exe = tmp_path / "Program (x86)" / "Origin64.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_EXE=str(exe), XRD_ORIGIN_DRYRUN="1"))
    assert done.returncode == 0
    assert done.stdout.strip() == (
        f'"{exe}" -rs run.section("{b.folder}\\xrd_origin.ogs", Main, "{b.folder}")'
    )


@windows
def test_launcher_finds_the_newest_installed_version(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "out")
    programs = tmp_path / "Programs"
    for version, exe in (("Origin2022", "Origin95_64.exe"), ("Origin2024b", "Origin64.exe")):
        folder = programs / "OriginLab" / version
        folder.mkdir(parents=True)
        (folder / exe).write_bytes(b"")
    env = _env(tmp_path, ProgramFiles=str(programs), XRD_ORIGIN_DRYRUN="1")
    done = _run(b.launcher, env)
    assert done.returncode == 0
    assert "Origin2024b" in done.stdout and "Origin64.exe" in done.stdout


@windows
def test_launcher_really_starts_the_program_with_korean_and_spaces(result, tmp_path):
    b = write_origin_bundle([result], tmp_path / "XRD 결과 (1)")
    record = tmp_path / "args.json"
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import json, sys\n"
        f"json.dump(sys.argv[1:], open({str(record)!r}, 'w', encoding='utf-8'))\n",
        encoding="utf-8",
    )
    fake = tmp_path / "fake origin.cmd"
    fake.write_text(f'@"{sys.executable}" "{recorder}" %*\r\n', encoding="ascii")
    done = _run(b.launcher, _env(tmp_path, XRD_ORIGIN_EXE=str(fake)))
    assert done.returncode == 0
    deadline = time.time() + 30
    while not record.exists() and time.time() < deadline:
        time.sleep(0.2)
    args = " ".join(json.loads(record.read_text(encoding="utf-8")))
    assert args.startswith("-rs run.section(")
    assert str(b.folder) in args


@windows
def test_cli_opens_a_bundle_through_its_link_file(result, tmp_path, monkeypatch, capfd):
    b = write_origin_bundle([result], tmp_path / "out")
    exe = tmp_path / "Origin64.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("XRD_ORIGIN_EXE", str(exe))
    monkeypatch.setenv("XRD_ORIGIN_DRYRUN", "1")
    assert cli_main(["open-in-origin", str(b.link)]) == 0
    assert "-rs run.section(" in capfd.readouterr().out

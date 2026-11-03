import csv
import json
import warnings

import pytest

from xrd_analyzer.cli import main, write_example_files
from xrd_analyzer.core.analysis import run_analysis
from xrd_analyzer.io import read_pattern
from xrd_analyzer.models.dataset import XRDPattern
from xrd_analyzer.reports.exports import (
    export_all,
    write_peak_table_csv,
    write_result_json,
)
from xrd_analyzer.reports.figures import PlotSettings, result_figure, save_figure
from xrd_analyzer.reports.report import cjk_font, write_html_report, write_pdf_report


@pytest.fixture(scope="module")
def nano_result(examples):
    ex = examples["nano_fcc"]
    return run_analysis(ex.pattern, ex.recipe)


def test_export_all_files_and_round_trip(tmp_path, nano_result):
    paths = export_all(nano_result, tmp_path)
    names = {p.suffix for p in paths}
    assert names == {".csv", ".json", ".png", ".svg", ".html", ".pdf"}
    peaks_csv = next(p for p in paths if p.name.endswith("_peaks.csv"))
    rows = list(
        csv.DictReader(
            line for line in peaks_csv.open(encoding="utf-8") if not line.startswith("#")
        )
    )
    assert len(rows) == len(nano_result.peaks)
    assert float(rows[0]["two_theta_deg [deg]"]) == pytest.approx(
        nano_result.peaks[0].fit["center_deg"]
    )
    js = json.loads(next(p for p in paths if p.suffix == ".json").read_text(encoding="utf-8"))
    assert js["williamson_hall"]["size"]["unit"] == "nm"
    assert "NaN" not in next(p for p in paths if p.suffix == ".json").read_text(encoding="utf-8")
    pdf = next(p for p in paths if p.suffix == ".pdf")
    assert pdf.read_bytes().startswith(b"%PDF")
    html = next(p for p in paths if p.suffix == ".html").read_text(encoding="utf-8")
    assert "SYNTHETIC EXAMPLE DATA" in html and "data:image/png;base64" in html


def test_peak_csv_reimport_matches(tmp_path, nano_result):
    p = write_peak_table_csv(nano_result, tmp_path / "pk.csv")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# Independent open-source software")
    js = write_result_json(nano_result, tmp_path / "r.json", include_arrays=True)
    back = json.loads(js.read_text(encoding="utf-8"))
    assert len(back["processed"]["two_theta_deg"]) == nano_result.processed.two_theta_deg.size


def test_korean_names_render_in_pdf_without_missing_glyphs(tmp_path, examples):
    if cjk_font() is None:
        pytest.skip("no CJK font installed on this machine")
    ex = examples["si_like"]
    pat = XRDPattern(
        "시료_실리콘_한글", ex.pattern.two_theta_deg, ex.pattern.intensity, "counts", synthetic=True
    )
    res = run_analysis(pat, ex.recipe)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        write_pdf_report(res, tmp_path / "ko.pdf")
        write_html_report(res, tmp_path / "ko.html")
    missing = [str(x.message) for x in w if "missing from font" in str(x.message)]
    assert missing == []
    assert "시료_실리콘_한글" in (tmp_path / "ko.html").read_text(encoding="utf-8")


def test_figure_formats(tmp_path, nano_result):
    fig = result_figure(
        nano_result, PlotSettings(dark=True, log_y=True, show_components=True, show_net=True)
    )
    for ext in ("png", "svg", "pdf"):
        assert save_figure(fig, tmp_path / f"f.{ext}").stat().st_size > 1000
    with pytest.raises(ValueError):
        save_figure(fig, tmp_path / "f.jpg")
    assert PlotSettings.from_dict({"font_size": 14, "junk": 1}).font_size == 14


def test_cli_examples_analyze_and_errors(tmp_path, capsys):
    files = write_example_files(tmp_path / "ex")
    csvs = [f for f in files if f.suffix == ".csv"]
    assert len(csvs) == 3
    pat = read_pattern(csvs[0])
    assert pat.synthetic and pat.intensity_unit == "counts"
    recipe = next(f for f in files if f.name == "synthetic_nano_fcc_recipe.json")
    nano = next(f for f in csvs if "nano" in f.name)
    assert (
        main(
            [
                "analyze",
                str(nano),
                "--recipe",
                str(recipe),
                "--out",
                str(tmp_path / "o"),
                "--reference-cubic",
                "4.0786,F,fcc-like",
            ]
        )
        == 0
    )
    out = json.loads((tmp_path / "o" / "synthetic_nano_fcc_result.json").read_text("utf-8"))
    assert out["williamson_hall"]["status"] == "OK"
    assert out["references"][0]["confidence"] == "high"
    assert main(["formats"]) == 0
    assert "UNSUPPORTED" in capsys.readouterr().out
    headerless = tmp_path / "h.xy"
    headerless.write_text("\n".join(f"{20 + i * 0.01:.2f} {i}" for i in range(100)), "utf-8")
    assert (
        main(["analyze", str(headerless), "--wavelength", "cu_ka1", "--out", str(tmp_path / "o2")])
        == 2
    )
    assert (
        main(
            [
                "analyze",
                str(headerless),
                "--axis",
                "2theta-deg",
                "--out",
                str(tmp_path / "o3"),
                "--no-pdf",
            ]
        )
        == 3
    )  # wavelength missing
    assert (
        main(
            [
                "analyze",
                str(headerless),
                "--axis",
                "2theta-deg",
                "--wavelength",
                "1.5406",
                "--K",
                "0.9",
                "--inst-fwhm",
                "0.05",
                "--add-peak",
                "20.5",
                "--no-pdf",
                "--out",
                str(tmp_path / "o4"),
            ]
        )
        == 0
    )
    with pytest.raises(SystemExit):
        main(["analyze", str(headerless), "--wavelength", "copper", "--out", "x"])


def test_cli_batch_with_partial_failure(tmp_path):
    write_example_files(tmp_path / "ex")
    csvs = [str(tmp_path / "ex" / "synthetic_si_like.csv")]
    bad = tmp_path / "bad.csv"
    bad.write_text("2theta_deg,counts\n10,1\n10,2\n10,3\n10,4\n10,5\n", "utf-8")
    code = main(
        [
            "batch",
            *csvs,
            str(bad),
            "--wavelength",
            "cu_ka1",
            "--no-pdf",
            "--out",
            str(tmp_path / "b"),
        ]
    )
    assert code == 3
    summary = json.loads((tmp_path / "b" / "batch_summary.json").read_text("utf-8"))
    assert summary[0]["n_peaks"] == 6 and "error" in summary[1]


def test_selftest_command_passes(tmp_path):
    out = tmp_path / "selftest.json"
    assert main(["selftest", "--out", str(out)]) == 0
    rep = json.loads(out.read_text("utf-8"))
    assert rep["selftest"] == "PASS" and len(rep["checks"]) == 6
    assert rep["environment"]["frozen"] is False

import time

import numpy as np
import pytest

from xrd_analyzer.io import (
    AxisConfirmationRequired,
    ReadOptions,
    UnsupportedFormatError,
    XRDFormatError,
    list_formats,
    probe_file,
    read_pattern,
)
from xrd_analyzer.io.readers import classify_column
from xrd_analyzer.models.dataset import (
    SYNTHETIC_ONLY,
    UNSUPPORTED,
    XRDPattern,
    sha256_file,
)

ROWS = "\n".join(f"{20 + 0.02 * i:.2f},{100 + i}" for i in range(50))


def test_csv_with_explicit_header(write_text):
    p = write_text(
        "a.csv", "# operator: test\n# synthetic: yes\ntwo_theta_deg,intensity_counts\n" + ROWS
    )
    pat = read_pattern(p)
    assert pat.n_points == 50 and pat.intensity_unit == "counts"
    assert pat.two_theta_deg[0] == 20.0 and pat.format_status == SYNTHETIC_ONLY
    assert pat.metadata["operator"] == "test" and pat.synthetic
    assert pat.sha256 == sha256_file(str(p))


def test_tab_and_unicode_header(write_text):
    body = ROWS.replace(",", "\t")
    pat = read_pattern(write_text("b.txt", "2θ (°)\tIntensity (cps)\n" + body))
    assert pat.intensity_unit == "cps"


def test_cp949_korean_header(write_text):
    p = write_text("k.csv", "# 시료: 실리콘\n2theta_deg,counts\n" + ROWS, encoding="cp949")
    pat = read_pattern(p)
    assert pat.metadata["시료"] == "실리콘"


def test_headerless_requires_axis_confirmation(write_text):
    p = write_text("c.xy", ROWS.replace(",", " "))
    with pytest.raises(AxisConfirmationRequired) as exc:
        read_pattern(p)
    assert exc.value.probe is not None and exc.value.probe.n_columns == 2
    pat = read_pattern(p, ReadOptions(axis_quantity="2theta", axis_unit="deg"))
    assert pat.n_points == 50 and "intensity_unit_unconfirmed" in pat.quality_flags


def test_header_without_unit_requires_confirmation(write_text):
    p = write_text("d.csv", "2Theta,Intensity\n" + ROWS)
    with pytest.raises(AxisConfirmationRequired, match="unit"):
        read_pattern(p)
    assert read_pattern(p, ReadOptions(axis_unit="deg")).n_points == 50


def test_theta_header_is_never_silently_converted(write_text):
    p = write_text(
        "e.csv", "theta_deg,counts\n" + "\n".join(f"{10 + 0.01 * i:.2f},{i}" for i in range(50))
    )
    with pytest.raises(AxisConfirmationRequired, match="ambiguous"):
        read_pattern(p)
    as_2t = read_pattern(p, ReadOptions(axis_quantity="2theta"))
    assert as_2t.two_theta_deg[0] == pytest.approx(10.0)
    as_t = read_pattern(p, ReadOptions(axis_quantity="theta"))
    assert as_t.two_theta_deg[0] == pytest.approx(20.0)
    assert "axis_converted_from_theta" in as_t.quality_flags


def test_radian_axis_conversion(write_text):
    rows = "\n".join(f"{np.deg2rad(20 + 0.1 * i):.8f},{i + 1}" for i in range(50))
    pat = read_pattern(write_text("r.csv", "2theta_rad,counts\n" + rows))
    assert pat.two_theta_deg[0] == pytest.approx(20.0, abs=1e-5)
    assert "axis_converted_from_radians" in pat.quality_flags


def test_multi_column_selection(write_text):
    rows = "\n".join(f"{20 + 0.02 * i:.2f},{i},{100 + i}" for i in range(50))
    p = write_text("m.csv", "2theta_deg,esd,counts\n" + rows)
    pat = read_pattern(p)
    assert pat.intensity[0] == 100
    noh = write_text("m2.csv", rows)
    with pytest.raises(AxisConfirmationRequired, match="columns"):
        read_pattern(noh, ReadOptions(axis_quantity="2theta", axis_unit="deg"))
    pat2 = read_pattern(noh, ReadOptions(0, 2, "2theta", "deg"))
    assert pat2.intensity[0] == 100


@pytest.mark.parametrize(
    "text, match",
    [
        ("", "empty"),
        ("# only comments\n# nothing else\n", "no numeric"),
        ("2theta_deg,counts\n20,1\n20.1,2\nfoo,bar\n", "non-numeric"),
        ("2theta_deg,counts\n20,1\n20.1,2,3\n", "expected 2 columns"),
        ("2theta_deg;counts\n20,1;5\n20,2;6\n", "decimal comma"),
        ("2theta_deg\n20\n21\n", "one numeric column"),
        ("2theta_deg,counts\n" + "\n".join(f"{170 + i},{i}" for i in range(20)), "outside"),
        ("2theta_deg,counts\n20,1\n21,2\n", "only 2 points"),
    ],
)
def test_malformed_files_fail_clearly(write_text, text, match):
    p = write_text("bad.csv", text)
    with pytest.raises(XRDFormatError, match=match):
        read_pattern(p)


def test_nonfinite_rows_rejected_unless_explicitly_dropped(write_text):
    p = write_text("n.csv", "2theta_deg,counts\n" + ROWS + "\n21.5,nan\n21.6,inf\n")
    with pytest.raises(XRDFormatError, match="NaN/Inf"):
        read_pattern(p)
    pat = read_pattern(p, ReadOptions(drop_nonfinite=True))
    assert pat.n_points == 50 and "dropped_2_nonfinite_rows" in pat.quality_flags


def test_binary_content_rejected(tmp_path):
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\x00\x01\x02binary" * 100)
    with pytest.raises(XRDFormatError, match="binary"):
        read_pattern(p)


def test_quality_flags_for_odd_but_valid_data(write_text):
    rows = "\n".join(f"{40 - 0.02 * i:.2f},{-5 + i}" for i in range(50))  # descending, negatives
    pat = read_pattern(write_text("q.csv", "2theta_deg,counts\n" + rows))
    assert "descending_axis" in pat.quality_flags and "negative_intensity" in pat.quality_flags
    sat = "\n".join(f"{20 + 0.02 * i:.2f},{min(i, 30)}" for i in range(50))
    pat2 = read_pattern(write_text("s.csv", "2theta_deg,counts\n" + sat))
    assert "possible_saturation" in pat2.quality_flags
    dup = "2theta_deg,counts\n20,1\n20,2\n" + ROWS
    assert "duplicate_2theta" in read_pattern(write_text("dup.csv", dup)).quality_flags


@pytest.mark.parametrize("ext", [".xrdml", ".ras", ".rasx", ".raw", ".brml", ".udf"])
def test_vendor_formats_are_refused(write_text, ext):
    p = write_text("v" + ext, "<xml/>")
    with pytest.raises(UnsupportedFormatError, match="UNSUPPORTED"):
        read_pattern(p)
    assert probe_file(p).status == UNSUPPORTED


def test_unknown_extension(write_text):
    with pytest.raises(UnsupportedFormatError, match="unknown extension"):
        read_pattern(write_text("x.foo", ROWS))


def test_format_table_is_honest():
    table = {f["parser"]: f["status"] for f in list_formats()}
    assert table["delimited-text"] == SYNTHETIC_ONLY
    assert all(v == UNSUPPORTED for k, v in table.items() if k != "delimited-text")


def test_raw_data_is_immutable_and_file_untouched(si_csv):
    before = sha256_file(str(si_csv))
    pat = read_pattern(si_csv)
    with pytest.raises(ValueError):
        pat.intensity[0] = 0
    with pytest.raises(AttributeError):
        pat.name = "x"  # type: ignore[misc]
    assert sha256_file(str(si_csv)) == before


def test_pattern_validation():
    with pytest.raises(ValueError):
        XRDPattern("x", [1, 2, 3], [1, 2])
    with pytest.raises(ValueError):
        XRDPattern("x", [[1, 2]], [[1, 2]])


def test_large_file_performance(tmp_path):
    n = 200_000
    x = np.linspace(5, 120, n)
    p = tmp_path / "big.csv"
    np.savetxt(
        p,
        np.column_stack([x, np.ones(n) * 10]),
        delimiter=",",
        fmt="%.6f",
        header="2theta_deg,counts",
        comments="",
    )
    t = time.perf_counter()
    pat = read_pattern(p)
    assert pat.n_points == n
    assert time.perf_counter() - t < 10


def test_classify_column():
    assert classify_column("2θ (deg)") == ("2theta", "deg")
    assert classify_column("Two_Theta") == ("2theta", None)
    assert classify_column("Omega") == ("theta", None)
    assert classify_column("Intensity (a.u.)") == ("intensity", "a.u.")
    assert classify_column("CPS") == ("intensity", "cps")
    assert classify_column("") == (None, None)
    assert classify_column("Angle") == (None, None)

"""Synthetic header grammar and metadata/data-boundary regression cases."""

import numpy as np
import pytest

from xrd_analyzer.io import (
    AxisConfirmationRequired,
    ReadOptions,
    XRDFormatError,
    probe_file,
    read_pattern,
)
from xrd_analyzer.io.readers import classify_column

ROWS = "\n".join(f"{20 + i},{100 + i}" for i in range(6))


@pytest.mark.parametrize(
    "label",
    [
        "2thetaError_deg",
        "2theta_error_deg",
        "2thetaOffset_deg",
        "2thetaResidual_deg",
        "intensity_sigma",
        "intensitySigma",
        "intensity_counts_error",
        "intensity_offset",
        "intensityResidual",
    ],
)
def test_uncertainty_labels_are_excluded(label):
    assert classify_column(label) == ("uncertainty", None)


@pytest.mark.parametrize(
    "label",
    [
        "Errin",
        "Intensity unit",
        "Scan axis",
        "2thetaCalibration_deg",
        "2thetaerror_deg",
        "2theta_deg_rad",
        "2theta_deg/second",
        "2thickness_deg",
        "intensitysigma",
        "intensity_counts/minute",
        "intensity_counts/second_squared",
        "discounts",
        "cpsResidualValue",
        "integral",
    ],
)
def test_unsupported_labels_never_select_a_signal(label):
    assert classify_column(label)[0] not in {"2theta", "theta", "intensity"}


@pytest.mark.parametrize(
    "label",
    [
        "intensity_counts/second",
        "counts per second",
        "cps",
        "counts/s",
        "Intensity (counts / second)",
        "intensity_counts/sec",
        "counts/seconds",
        "cts/s",
        "c/s",
    ],
)
def test_rate_labels_are_not_raw_counts(write_text, label):
    assert classify_column(label) == ("intensity", "cps")
    pattern = read_pattern(write_text("rate.csv", f"2theta_deg,{label}\n{ROWS}"))
    assert pattern.intensity_unit == "cps"
    assert not pattern.counts_like
    np.testing.assert_array_equal(pattern.intensity, np.arange(100, 106))


@pytest.mark.parametrize(
    "header, missing",
    [
        ("2thetaError_deg,intensity_counts", "x_column"),
        ("intensity_counts,2thetaError_deg", "x_column"),
        ("2theta_deg,intensity_sigma", "y_column"),
        ("intensity_sigma,2theta_deg", "y_column"),
        ("2theta_deg,intensity_offset", "y_column"),
        ("2theta_deg,intensityResidual", "y_column"),
        ("2theta_deg,discounts", "y_column"),
        ("2theta_deg,intensity_counts/minute", "y_column"),
        ("2thetaerror_deg,intensity_counts", "x_column"),
    ],
)
def test_no_positional_fallback_for_unsafe_named_columns(write_text, header, missing):
    path = write_text("unsafe.csv", f"{header}\n{ROWS}")
    probe = probe_file(path)
    assert getattr(probe, missing) is None
    with pytest.raises(AxisConfirmationRequired):
        read_pattern(path)


def test_uncertainty_does_not_hide_valid_columns(write_text):
    rows = "\n".join(f"0.1,{20 + i},0.2,{100 + i}" for i in range(6))
    path = write_text(
        "extra.csv", "2thetaError_deg,2theta_deg,intensity_sigma,intensity_counts\n" + rows
    )
    probe = probe_file(path)
    assert (probe.x_column, probe.y_column) == (1, 3)
    pattern = read_pattern(path)
    np.testing.assert_array_equal(pattern.two_theta_deg, np.arange(20, 26))
    np.testing.assert_array_equal(pattern.intensity, np.arange(100, 106))


@pytest.mark.parametrize(
    "header, missing",
    [
        ("2theta_deg,theta_deg,intensity_counts", "x_column"),
        ("2theta_deg,counts,cps", "y_column"),
    ],
)
def test_multiple_signal_candidates_require_selection(write_text, header, missing):
    rows = "\n".join(f"{20 + i},{100 + i},{200 + i}" for i in range(6))
    path = write_text("ambiguous.csv", f"{header}\n{rows}")
    assert getattr(probe_file(path), missing) is None
    with pytest.raises(AxisConfirmationRequired):
        read_pattern(path)
    pattern = read_pattern(path, ReadOptions(x_column=0, y_column=2))
    np.testing.assert_array_equal(pattern.intensity, np.arange(200, 206))


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
@pytest.mark.parametrize(
    "preamble",
    [
        "Intensity unit,counts",
        "Scan axis,2theta",
        "Operator,Errin",
        "Intensity unit,counts\nScan axis,2theta\nOperator,Errin",
    ],
)
def test_metadata_tokens_do_not_start_data_section(write_text, delimiter, preamble):
    text = f"{preamble}\nWavelength,1.5406\n2theta_deg,intensity_counts\n{ROWS}"
    path = write_text("metadata.csv", text.replace(",", delimiter))
    probe = probe_file(path)
    assert probe.header == ["2theta_deg", "intensity_counts"]
    assert probe.n_rows == 6
    pattern = read_pattern(path)
    assert pattern.n_points == 6
    assert pattern.metadata["Wavelength"] == "1.5406"
    for line in preamble.splitlines():
        key, value = line.split(",")
        assert pattern.metadata[key] == value
    np.testing.assert_array_equal(pattern.two_theta_deg, np.arange(20, 26))
    np.testing.assert_array_equal(pattern.intensity, np.arange(100, 106))


@pytest.mark.parametrize("bad_row", ["10,BAD", "BAD,10"])
@pytest.mark.parametrize("delimiter", [",", ";", "\t", " "])
def test_malformed_first_data_row_retains_line_number(write_text, bad_row, delimiter):
    text = f"# Operator: Errin\n2theta_deg,intensity_counts\n{bad_row}\n{ROWS}"
    path = write_text("malformed.csv", text.replace(",", delimiter))
    with pytest.raises(XRDFormatError, match="line 3: malformed data row"):
        read_pattern(path)


def test_metadata_then_real_header_still_rejects_malformed_data(write_text):
    text = "Intensity unit,counts\nWavelength,1.5406\n2theta_deg,counts\n10,BAD\n" + ROWS
    with pytest.raises(XRDFormatError, match="line 4: malformed data row"):
        read_pattern(write_text("boundary.csv", text))


@pytest.mark.parametrize("header", ["2theta_deg,counts,esd", "2theta_deg,counts"])
def test_header_and_first_data_row_must_have_same_width(write_text, header):
    rows = ROWS if header.endswith("esd") else ROWS.replace("\n", ",1\n") + ",1"
    with pytest.raises(XRDFormatError, match=r"line 2: expected .* columns from the column header"):
        read_pattern(write_text("width.csv", f"{header}\n{rows}"))


@pytest.mark.parametrize("delimiter", [",", ";", "\t", " "])
@pytest.mark.parametrize("header", ["2theta_deg,I_net", "angle,counts"])
@pytest.mark.parametrize("bad_row", ["10,BAD", "BAD,10", "10,BAD,extra"])
def test_partial_header_cannot_hide_malformed_row(write_text, delimiter, header, bad_row):
    text = f"{header}\n{bad_row}\n{ROWS}".replace(",", delimiter)
    path = write_text("partial.csv", text)
    with pytest.raises(XRDFormatError, match="line 2: malformed data row"):
        read_pattern(path, ReadOptions(0, 1, "2theta", "deg"))


def test_fallback_header_must_be_all_text(write_text):
    path = write_text("preamble.csv", f"Sample,Si\nWavelength,1.5406\n{ROWS}")
    probe = probe_file(path)
    assert probe.header == []
    assert probe.metadata["Wavelength"] == "1.5406"
    with pytest.raises(AxisConfirmationRequired):
        read_pattern(path)
    pattern = read_pattern(path, ReadOptions(0, 1, "2theta", "deg"))
    assert pattern.n_points == 6


def test_metadata_before_partial_header_is_preserved(write_text):
    path = write_text(
        "partial.csv", f"Intensity unit,counts\nWavelength,1.5406\n2theta_deg,I_net\n{ROWS}"
    )
    assert probe_file(path).header == ["2theta_deg", "I_net"]
    with pytest.raises(AxisConfirmationRequired):
        read_pattern(path)
    pattern = read_pattern(path, ReadOptions(0, 1, "2theta", "deg"))
    assert pattern.n_points == 6
    assert pattern.metadata["Intensity unit"] == "counts"
    assert pattern.metadata["Wavelength"] == "1.5406"


@pytest.mark.parametrize("label", ["intensity_corrected", "intensity_uncorrected"])
def test_correction_labels_require_column_confirmation(write_text, label):
    assert classify_column(label) == (None, None)
    path = write_text("correction.csv", f"2theta_deg,{label}\n{ROWS}")
    with pytest.raises(AxisConfirmationRequired):
        read_pattern(path)
    assert read_pattern(path, ReadOptions(y_column=1)).n_points == 6


@pytest.mark.parametrize(
    "label, expected",
    [
        ("2θ / °", ("2theta", "deg")),
        ("two_theta / degrees", ("2theta", "deg")),
        ("2theta / rad", ("2theta", "rad")),
        ("theta / deg", ("theta", "deg")),
        ("Intensity / counts", ("intensity", "counts")),
        ("Intensity / counts/s", ("intensity", "cps")),
        ("Intensity / a.u.", ("intensity", "a.u.")),
        ("2theta_error / deg", ("uncertainty", None)),
        ("Intensity / counts/minute", (None, None)),
        ("2theta / counts", (None, None)),
        ("Intensity / deg", (None, None)),
        ("2theta / deg / rad", (None, None)),
        ("2th / etadeg", (None, None)),
        ("I / ntensitycounts", (None, None)),
    ],
)
def test_quantity_unit_separator(label, expected):
    assert classify_column(label) == expected


@pytest.mark.parametrize("unit, expected", [("counts", "counts"), ("counts/s", "cps")])
def test_quantity_unit_header_parses(write_text, unit, expected):
    path = write_text("quantity.csv", f"2θ / °,Intensity / {unit}\n{ROWS}")
    pattern = read_pattern(path)
    np.testing.assert_array_equal(pattern.two_theta_deg, np.arange(20, 26))
    np.testing.assert_array_equal(pattern.intensity, np.arange(100, 106))
    assert pattern.intensity_unit == expected
    assert pattern.counts_like == (expected == "counts")


@pytest.mark.parametrize("axis", ["theta / deg", "2theta / furlong", "2theta / deg / rad"])
def test_ambiguous_axis_requires_confirmation(write_text, axis):
    path = write_text("ambiguous.csv", f"{axis},Intensity / counts\n{ROWS}")
    with pytest.raises(AxisConfirmationRequired):
        read_pattern(path)

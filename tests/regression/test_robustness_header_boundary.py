"""Robustness regressions: malformed rows after a partially recognised column header must be
reported (never stored as metadata); ambiguous header boundaries are rejected."""

import pytest

from xrd_analyzer.io import ReadOptions, XRDFormatError, read_pattern

ROWS = "\n".join(f"{v},{v + 80}" for v in range(20, 26))
OPTS = ReadOptions(0, 1, "2theta", "deg")


@pytest.mark.parametrize(
    "pre, bad_line",
    [
        ("2theta_deg,I_net\n10,BAD\n11,BAD\n", 2),
        ("2theta_deg,I_net\n10,BAD\nBAD,BAD\n", 2),
        ("2theta_deg,I_net\nBAD,BAD\n10,BAD\n", 3),
        ("2theta_deg,I_net\n10,BAD,5\n", 2),
        ("Angle,counts\n10,BAD\n11,BAD\n", 2),
        ("2theta_deg;I_net\n10;BAD\n11;BAD\n", 2),
    ],
)
def test_first_malformed_row_reported(write_text, pre, bad_line):
    with pytest.raises(XRDFormatError, match=f"line {bad_line}: malformed data row"):
        read_pattern(
            write_text("malformed.csv", pre + ROWS.replace(",", ";" if ";" in pre else ",")), OPTS
        )


def test_ambiguous_boundary_is_rejected_not_guessed(write_text):
    """a later header-like line must not move the
    boundary past malformed observations; ambiguous preambles are rejected."""
    for later in ("BAD,counts", "deg,counts"):
        text = f"2theta_deg,I_net\n10,BAD\n{later}\n" + ROWS
        with pytest.raises(XRDFormatError, match="line 2: malformed data row"):
            read_pattern(write_text("amb.csv", text), OPTS)
    text = "Scan axis,2theta\nWavelength,1.5406\n2theta_deg,I_net\n" + ROWS
    with pytest.raises(XRDFormatError, match=r"line 2: .*move it above the header"):
        read_pattern(write_text("amb2.csv", text), OPTS)


def test_recognised_header_keeps_metadata_preamble(write_text):
    text = "Scan axis,2theta\nWavelength,1.5406\n2theta_deg,intensity_counts\n" + ROWS
    pat = read_pattern(write_text("ok.csv", text))
    assert pat.n_points == 6 and pat.metadata["Wavelength"] == "1.5406"


def test_names_row_preferred_over_units_row(write_text):
    pat = read_pattern(write_text("nu.csv", "2theta_deg,I_net\ndeg,counts\n" + ROWS), OPTS)
    assert pat.metadata["x_column"] == "2theta_deg" and pat.metadata["y_column"] == "I_net"


def test_all_text_units_row_after_declaration_is_not_data(write_text):
    pat = read_pattern(write_text("u.csv", "2theta_deg,I_net\nBAD,BAD\n" + ROWS), OPTS)
    assert pat.n_points == 6 and pat.metadata["x_column"] == "2theta_deg"

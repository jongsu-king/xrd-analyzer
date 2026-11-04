"""Bruker BRML reader. The archives are synthetic: same XML layout as real files, invented counts.

Real research data are not committed. Comparison with real files and their vendor text exports
lives in tests/integration/test_real_bruker_files.py, which needs XRD_REAL_DATA_DIR.
"""

import zipfile

import numpy as np
import pytest

from xrd_analyzer.core.analysis import run_analysis
from xrd_analyzer.io import (
    ReadOptions,
    UnsupportedFormatError,
    XRDFormatError,
    probe_file,
    read_pattern,
)
from xrd_analyzer.models.dataset import VERIFIED_NATIVE
from xrd_analyzer.models.recipe import AnalysisRecipe

XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
TWO_THETA = np.round(20.0 + 0.02 * np.arange(200), 6)
COUNTS = (100 + 900 * np.exp(-(((TWO_THETA - 22.0) / 0.1) ** 2))).astype(int)
VIEWS = {"time": 0, "absorption": 1, "two_theta": 2, "theta": 3, "counts": 4}


def _views(pos: dict[str, int]) -> str:
    return f"""<DataViews>
      <RawDataView xsi:type="FixedRawDataView" Start="{pos["time"]}" Length="1"
          LogicName="MeasuredTime" />
      <RawDataView xsi:type="FixedRawDataView" Start="{pos["absorption"]}" Length="1"
          LogicName="AbsorptionFactor" />
      <RawDataView xsi:type="VaryingRawDataView" Start="{pos["two_theta"]}" Length="2">
        <Varying xsi:type="VaryingStepData2D" LogicName="ScanAxes">
          <FieldDefinitions FieldName="TwoTheta"><Restriction Unit="°" /></FieldDefinitions>
          <FieldDefinitions FieldName="Theta"><Restriction Unit="°" /></FieldDefinitions>
        </Varying>
      </RawDataView>
      <RawDataView xsi:type="RecordedRawDataView" Start="{pos["counts"]}" Length="1"
          MeasuredDataIndex="0">
        <Recording LogicName="ScanCounter" Category="Count">
          <Unit Prefix="None" Base="Counts" />
          <BlankingValue>-9999</BlankingValue>
        </Recording>
      </RawDataView>
    </DataViews>"""


def make_brml(
    path,
    version="8.6.3.0",
    status="Measured",
    scan_name="LockedCoupled",
    scan_mode="PsdFastScan",
    multi="false",
    steps=None,
    measured=None,
    refs=("Experiment0/RawData0.xml",),
    absorption=1.0,
    times=None,
    counts=None,
    views=None,
    extra_members=(),
    prolog="",
    drop=(),
):
    n = len(TWO_THETA)
    counts = COUNTS if counts is None else counts
    times = np.full(n, 2.5) if times is None else times
    pos = VIEWS if views is None else views
    rows = []
    for i in range(n):
        row = [""] * 5
        row[pos["time"]] = f"{times[i]}"
        row[pos["absorption"]] = f"{absorption}"
        row[pos["two_theta"]] = f"{TWO_THETA[i]}"
        row[pos["theta"]] = f"{TWO_THETA[i] / 2}"
        row[pos["counts"]] = f"{counts[i]}"
        rows.append(f"<Datum>{','.join(row)}</Datum>")
    raw = f"""<?xml version="1.0"?>{prolog}
<RawData {XSI}>
  <MeasurementStatus>{status}</MeasurementStatus>
  <TimeStampStarted>2026-01-01T10:00:00</TimeStampStarted>
  <DataRoutes><DataRoute RouteFlag="Measured" ProcLevel="0">
    <ScanInformation IsMultiScan="{multi}" IsAdvScan="false" IsAdvMultiScan="false"
        ScanName="{scan_name}" VisibleName="Coupled TwoTheta/Theta">
      <MeasurementPoints>{n}</MeasurementPoints>
      <ScanMode>{scan_mode}</ScanMode>
    </ScanInformation>
    <SubScans><SubScanInfo Steps="{steps or n}" MeasuredSteps="{measured or n}" /></SubScans>
    {"".join(rows)}
    {_views(pos)}
  </DataRoute></DataRoutes>
  <FixedInformation>
    <InfoItem Name="SampleName" Value="synthetic" />
    <WaveLengthAlpha1 Unit="Å" Value="1.5406" />
    <WaveLengthAlpha2 Unit="Å" Value="1.54439" />
    <WaveLengthRatio Unit="" Value="0.5" />
  </FixedInformation>
</RawData>"""
    ref_xml = "".join(f"<string>{r}</string>" for r in refs)
    container = f"""<?xml version="1.0"?>
<DataContainer {XSI}>
  <CreatingVersion>{version}</CreatingVersion>
  <RawDataReferenceList>{ref_xml}</RawDataReferenceList>
  <ExperimentInfo><InstrumentDescription>
    <DeviceTypeDesc>Synthetic Diffractometer</DeviceTypeDesc>
  </InstrumentDescription></ExperimentInfo>
</DataContainer>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        members = {"Experiment0/DataContainer.xml": container, "Experiment0/RawData0.xml": raw}
        members.update(dict(extra_members))
        for name, text in members.items():
            if name not in drop:
                z.writestr(name, text)
    return path


@pytest.fixture
def brml(tmp_path):
    return make_brml(tmp_path / "scan.brml")


def test_reads_raw_counts_and_axis(brml):
    pat = read_pattern(brml)
    assert np.array_equal(pat.two_theta_deg, TWO_THETA)
    assert np.array_equal(pat.intensity, COUNTS)
    assert pat.intensity_unit == "counts" and pat.counts_like
    assert pat.parser == "bruker-brml" and pat.format_status == VERIFIED_NATIVE
    assert not pat.synthetic


def test_metadata_reports_what_the_file_records(brml):
    md = read_pattern(brml).metadata
    assert md["creating_version"] == "8.6.3.0" and md["scan_mode"] == "PsdFastScan"
    assert md["time_per_step_s"] == "2.5" and md["instrument"] == "Synthetic Diffractometer"
    assert md["file_wavelength_A"] == "1.5406" and md["file_wavelength2_A"] == "1.54439"
    assert md["file_ka2_ratio"] == "0.5"


def test_columns_follow_the_file_layout_not_fixed_positions(tmp_path):
    shuffled = {"counts": 0, "two_theta": 1, "theta": 2, "absorption": 3, "time": 4}
    pat = read_pattern(make_brml(tmp_path / "s.brml", views=shuffled))
    assert np.array_equal(pat.two_theta_deg, TWO_THETA)
    assert np.array_equal(pat.intensity, COUNTS)


def test_file_wavelength_is_never_applied_silently(brml):
    with pytest.raises(ValueError, match="wavelength is not set"):
        run_analysis(read_pattern(brml), AnalysisRecipe())
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = 0.7093
    res = run_analysis(read_pattern(brml), r)
    assert res.recipe["preprocess"]["wavelength_A"] == 0.7093
    assert any("differs from 1.5406" in w for w in res.warnings)


def test_matching_wavelength_gives_no_warning(brml):
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = 1.5405929  # the preset rounds to the value in the file
    res = run_analysis(read_pattern(brml), r)
    assert not any("differs from" in w for w in res.warnings)


def test_analysis_uses_poisson_weights(brml):
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = 1.5406
    res = run_analysis(read_pattern(brml), r)
    assert res.processed.uncertainty_model[0].startswith("Poisson weights")
    assert res.fit.peaks and res.fit.groups[0].weighted


def test_probe_needs_no_confirmation(brml):
    probe = probe_file(brml)
    assert probe.axis_confirmed and probe.intensity_unit == "counts" and probe.n_rows == 200
    assert probe.preview[0] == [TWO_THETA[0], COUNTS[0]]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"version": "9.0.0.0"}, "version 9.0.0.0"),
        ({"scan_name": "Omega"}, "only coupled"),
        ({"scan_mode": "StepScan"}, "only coupled"),
        ({"multi": "true"}, "multi-scan"),
        ({"refs": ("Experiment0/RawData0.xml", "Experiment0/RawData1.xml")}, "2 raw-data sets"),
        ({"absorption": 10.0}, "absorber"),
        ({"times": np.linspace(1.0, 3.0, 200)}, "counting time varies"),
        ({"extra_members": (("Experiment1/DataContainer.xml", "<x/>"),)}, "several experiments"),
    ],
)
def test_unverified_files_are_refused_explicitly(tmp_path, kwargs, match):
    with pytest.raises(UnsupportedFormatError, match=match):
        read_pattern(make_brml(tmp_path / "x.brml", **kwargs))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"status": "Aborted"}, "not a completed scan"),
        ({"steps": 300}, "scan incomplete"),
        ({"counts": np.where(np.arange(200) == 5, -9999, COUNTS)}, "blanked"),
        ({"counts": COUNTS + 0.5}, "not whole"),
        ({"counts": np.where(np.arange(200) == 5, -3, COUNTS)}, "not whole"),
        ({"prolog": "<!DOCTYPE x>"}, "DTD"),
        ({"refs": ("../../etc/passwd",)}, "unsafe"),
        ({"drop": ("Experiment0/RawData0.xml",)}, "no member"),
        ({"extra_members": (("Experiment0/RawData0.xml", "<RawData><oops>"),)}, "not valid XML"),
    ],
)
def test_damaged_files_are_refused(tmp_path, kwargs, match):
    with pytest.raises(XRDFormatError, match=match):
        read_pattern(make_brml(tmp_path / "x.brml", **kwargs))


def test_not_a_zip_is_refused(tmp_path):
    p = tmp_path / "x.brml"
    p.write_bytes(b"<xml/>")
    with pytest.raises(XRDFormatError, match="not a valid BRML"):
        read_pattern(p)


def test_truncated_archive_is_refused(tmp_path, brml):
    p = tmp_path / "cut.brml"
    p.write_bytes(brml.read_bytes()[:400])
    with pytest.raises(XRDFormatError):
        read_pattern(p)


@pytest.mark.parametrize(
    ("options", "match"),
    [
        (ReadOptions(axis_quantity="theta"), "declares its axis"),
        (ReadOptions(axis_unit="rad"), "declares its axis"),
        (ReadOptions(intensity_unit="cps"), "stores raw counts"),
        (ReadOptions(x_column=1), "declares its own columns"),
    ],
)
def test_options_cannot_contradict_the_file(brml, options, match):
    with pytest.raises(XRDFormatError, match=match):
        read_pattern(brml, options)


def test_consistent_options_are_accepted(brml):
    pat = read_pattern(
        brml, ReadOptions(axis_quantity="2theta", axis_unit="deg", intensity_unit="counts")
    )
    assert pat.n_points == 200

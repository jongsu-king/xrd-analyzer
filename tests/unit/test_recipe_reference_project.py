import json

import pytest

from xrd_analyzer.core.reference import (
    load_reference_csv,
    match_reference,
    reference_from_cubic,
)
from xrd_analyzer.io import read_pattern
from xrd_analyzer.models.recipe import AnalysisRecipe
from xrd_analyzer.models.units import CU_KA1_A, SI_LATTICE_A
from xrd_analyzer.project import (
    Project,
    ProjectError,
    entry_for,
    load_project,
    resolve_entry,
    save_project,
)


def test_recipe_json_round_trip(tmp_path):
    r = AnalysisRecipe()
    r.preprocess.wavelength_A = CU_KA1_A
    r.peaks.manual_add_deg = [33.3]
    r.size.scherrer_K = 0.94
    p = tmp_path / "r.json"
    r.save(p)
    back = AnalysisRecipe.load(p)
    assert back.to_dict() == r.to_dict()


def test_recipe_rejects_unknown_or_foreign(tmp_path):
    d = AnalysisRecipe().to_dict()
    d["peaks"]["bogus"] = 1
    with pytest.raises(ValueError, match="unknown keys"):
        AnalysisRecipe.from_dict(d)
    with pytest.raises(ValueError, match="not an XRD Analyzer"):
        AnalysisRecipe.from_dict({"format": "other"})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        AnalysisRecipe.load(bad)


def test_recipe_validation_messages():
    r = AnalysisRecipe()
    r.preprocess.kalpha2_mode = "strip"
    r.preprocess.smoothing_window = 10
    r.size.instrument_mode = "constant"
    r.size.scherrer_K = 5
    r.fit.profile = "lorentz"
    errs = " | ".join(r.validate())
    for frag in ("Kα2 wavelength", "odd integer", "FWHM ≥ 0", "shape factor", "profile"):
        assert frag in errs
    assert AnalysisRecipe().validate() == []


def test_reference_match_high_for_correct_phase(examples):
    ex = examples["si_like"]
    obs = [p["two_theta_deg"] + 0.003 for p in ex.truth["peaks"]]
    ref = reference_from_cubic("Si", SI_LATTICE_A, "diamond", CU_KA1_A)
    m = match_reference(obs, ref, CU_KA1_A, (20, 90))
    assert m.confidence == "high" and m.n_matched == m.n_expected == 6
    assert m.median_delta_deg == pytest.approx(0.003, abs=1e-9)
    assert any("not a proof" in c for c in m.caveats)


def test_reference_match_low_for_wrong_phase_or_wavelength(examples):
    obs = [p["two_theta_deg"] for p in examples["si_like"].truth["peaks"]]
    fcc = reference_from_cubic("fcc", 4.0786, "F", CU_KA1_A)
    assert match_reference(obs, fcc, CU_KA1_A, (20, 90)).confidence == "low"
    si = reference_from_cubic("Si", SI_LATTICE_A, "diamond", 1.7890)  # Co Kα used by mistake
    assert match_reference(obs, si, 1.7890, (20, 90)).confidence == "low"
    si_cu = reference_from_cubic("Si", SI_LATTICE_A, "diamond", CU_KA1_A)
    empty = match_reference(obs, si_cu, CU_KA1_A, (89.0, 94.0))
    assert empty.confidence.startswith("not_applicable")


def test_reference_csv_loading(write_text):
    d_ref = write_text(
        "ref_d.csv",
        "# name: test phase\n# source: own calc\n# license: CC0\n"
        "d_A,rel_intensity,hkl\n3.1356,100,111\n1.9202,55,220\n1.6375,2,311\n",
    )
    ref = load_reference_csv(d_ref)
    assert ref.name == "test phase" and len(ref.peaks) == 3
    m = match_reference([28.44, 47.30], ref, CU_KA1_A, (20, 90), min_rel_intensity=5)
    assert m.n_expected == 2
    tt_ref = write_text("ref_tt.csv", "# wavelength_A: 1.5405929\ntwo_theta_deg,hkl\n28.4419,111\n")
    r2 = load_reference_csv(tt_ref)
    assert r2.peaks[0].d_A == pytest.approx(3.1356, abs=1e-4)
    assert "[source/license not stated]" in r2.name
    for bad, msg in [
        ("two_theta_deg\n28.44\n", "wavelength_A"),
        ("foo\n1\n", "need a"),
        ("d_A\n-1\n", "positive"),
        ("d_A\nabc\n", "bad reference row"),
        ("", "no data"),
    ]:
        with pytest.raises(ValueError, match=msg):
            load_reference_csv(write_text("bad_ref.csv", bad))


def test_project_round_trip_example_and_file(tmp_path, examples, si_csv):
    ex = examples["nano_fcc"]
    pat = read_pattern(si_csv)
    proj = Project(
        [
            entry_for(ex.pattern, ex.recipe, None, example_key="nano_fcc"),
            entry_for(pat, AnalysisRecipe(), None),
            entry_for(ex.pattern, ex.recipe, None, embed=True),
        ],
        active=1,
        plot_settings={"font_size": 12},
    )
    path = save_project(proj, tmp_path / "p.xrdproj.json")
    back = load_project(path)
    assert back.active == 1 and back.plot_settings["font_size"] == 12
    for e in back.entries:
        p2, r2 = resolve_entry(e)
        assert p2.sha256 == e.sha256 and isinstance(r2, AnalysisRecipe)


def test_project_detects_changed_missing_and_corrupt(tmp_path, si_csv):
    pat = read_pattern(si_csv)
    entry = entry_for(pat, AnalysisRecipe(), None)
    si_csv.write_text(si_csv.read_text(encoding="utf-8") + "90.01,1\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="SHA-256 mismatch"):
        resolve_entry(entry)
    si_csv.unlink()
    with pytest.raises(ProjectError, match="not found"):
        resolve_entry(entry)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ProjectError, match="corrupted"):
        load_project(bad)
    bad.write_text(json.dumps({"format": "x"}), encoding="utf-8")
    with pytest.raises(ProjectError, match="not an XRD Analyzer"):
        load_project(bad)


def test_project_embedded_tamper_and_in_memory(examples):
    ex = examples["si_like"]
    e = entry_for(ex.pattern, ex.recipe, None, embed=True)
    assert e.embedded is not None
    e.embedded["intensity"][0] += 1
    with pytest.raises(ProjectError, match="hash mismatch"):
        resolve_entry(e)
    with pytest.raises(ProjectError, match="embed=True"):
        entry_for(ex.pattern, ex.recipe, None)

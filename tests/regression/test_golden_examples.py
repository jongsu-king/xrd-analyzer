"""Regression guard: synthetic examples must stay bit-stable and results must not drift.

Golden values were produced by this code (v0.1.0) and are NOT independent validation —
the independent check is tests/integration/test_known_answer_pipeline.py (ground truth).
Update the golden file only with a documented reason (CHANGELOG).
"""

import json
from pathlib import Path

import pytest

from xrd_analyzer.core.analysis import run_analysis

GOLDEN = Path(__file__).with_name("golden_v0_1_0.json")


def _snapshot(ex):
    res = run_analysis(ex.pattern, ex.recipe)
    wh = res.williamson_hall
    return {
        "sha256": ex.pattern.sha256,
        "centers": [round(p.fit["center_deg"], 5) for p in res.peaks],
        "fwhm": [round(p.fit["fwhm_deg"], 5) for p in res.peaks],
        "wh_size_nm": round(wh.size.value, 4) if wh.size and wh.size.value else None,
    }


@pytest.mark.parametrize("key", ["si_like", "si_like_ka2", "nano_fcc"])
def test_golden(examples, key):
    if not GOLDEN.exists():
        pytest.skip("golden file not generated")
    gold = json.loads(GOLDEN.read_text(encoding="utf-8"))[key]
    snap = _snapshot(examples[key])
    assert snap["sha256"] == gold["sha256"], "synthetic generator output changed"
    assert snap["centers"] == pytest.approx(gold["centers"], abs=2e-5)
    assert snap["fwhm"] == pytest.approx(gold["fwhm"], abs=2e-5)
    if gold["wh_size_nm"] is None:
        assert snap["wh_size_nm"] is None
    else:
        assert snap["wh_size_nm"] == pytest.approx(gold["wh_size_nm"], abs=1e-3)


if __name__ == "__main__":  # regenerate: python tests/regression/test_golden_examples.py
    from xrd_analyzer.core.synthetic import make_example

    data = {k: _snapshot(make_example(k)) for k in ("si_like", "si_like_ka2", "nano_fcc")}
    GOLDEN.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote {GOLDEN}")

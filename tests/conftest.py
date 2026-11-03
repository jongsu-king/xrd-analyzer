from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from xrd_analyzer.core.synthetic import SyntheticExample, make_example


@pytest.fixture(scope="session")
def examples() -> dict[str, SyntheticExample]:
    return {k: make_example(k) for k in ("si_like", "si_like_ka2", "nano_fcc")}


@pytest.fixture
def write_text(tmp_path: Path) -> Callable[..., Path]:
    def _write(name: str, text: str, encoding: str = "utf-8") -> Path:
        p = tmp_path / name
        p.write_bytes(text.encode(encoding))
        return p

    return _write


@pytest.fixture
def si_csv(tmp_path: Path, examples: dict[str, SyntheticExample]) -> Path:
    ex = examples["si_like"]
    p = tmp_path / "si.csv"
    lines = ["# synthetic: yes", "two_theta_deg,intensity_counts"]
    lines += [
        f"{x:.4f},{int(y)}"
        for x, y in zip(ex.pattern.two_theta_deg, ex.pattern.intensity, strict=True)
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p

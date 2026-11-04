"""Project files (.xrdproj.json): dataset references + per-dataset recipes + plot settings.

Raw files are referenced by path and SHA-256 and re-read on load; a changed or missing
file is reported per dataset instead of being silently replaced. Synthetic examples are
regenerated from their key and verified by hash. Optionally raw arrays can be embedded.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .core.synthetic import make_example
from .io import ReadOptions, XRDFormatError, read_pattern
from .models.dataset import XRDPattern, sha256_arrays
from .models.recipe import AnalysisRecipe

PROJECT_FORMAT = "xrd-analyzer-project"
PROJECT_VERSION = 1


class ProjectError(ValueError):
    pass


@dataclass
class ProjectEntry:
    name: str
    kind: str  # "file" | "example" | "embedded"
    sha256: str
    recipe: dict[str, Any]
    source_path: str | None = None
    read_options: dict[str, Any] | None = None
    example_key: str | None = None
    embedded: dict[str, Any] | None = None
    # measured background (embedded so the analysis can be replayed exactly)
    background: dict[str, Any] | None = None


@dataclass
class Project:
    entries: list[ProjectEntry] = field(default_factory=list)
    active: int | None = None
    plot_settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "entries": [asdict(e) for e in self.entries],
            "active": self.active,
            "plot_settings": self.plot_settings,
        }


def entry_for(
    pattern: XRDPattern,
    recipe: AnalysisRecipe,
    read_options: ReadOptions | None,
    example_key: str | None = None,
    embed: bool = False,
    background: XRDPattern | None = None,
) -> ProjectEntry:
    entry = _entry_for(pattern, recipe, read_options, example_key, embed)
    if background is not None:
        entry.background = {
            "name": background.name,
            "source_path": background.source_path,
            "sha256": background.sha256,
            "parser": background.parser,
            "intensity_unit": background.intensity_unit,
            "quality_flags": list(background.quality_flags),
            "metadata": dict(background.metadata),
            "two_theta_deg": background.two_theta_deg.tolist(),
            "intensity": background.intensity.tolist(),
            "array_sha256": sha256_arrays(background.two_theta_deg, background.intensity),
        }
    return entry


def resolve_background(entry: ProjectEntry) -> XRDPattern | None:
    """Re-create the embedded measured background and verify its array hash."""
    b = entry.background
    if b is None:
        return None
    try:
        pat = XRDPattern(
            b["name"],
            b["two_theta_deg"],
            b["intensity"],
            b["intensity_unit"],
            b.get("metadata", {}),
            b.get("source_path"),
            b.get("sha256"),
            b.get("parser", "embedded"),
            quality_flags=tuple(b.get("quality_flags", ())),
        )
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ProjectError(f"{entry.name}: embedded background malformed: {exc!r}") from exc
    if sha256_arrays(pat.two_theta_deg, pat.intensity) != b.get("array_sha256"):
        raise ProjectError(f"{entry.name}: embedded background hash mismatch (corrupted?)")
    return pat


def _entry_for(
    pattern: XRDPattern,
    recipe: AnalysisRecipe,
    read_options: ReadOptions | None,
    example_key: str | None,
    embed: bool,
) -> ProjectEntry:
    assert pattern.sha256 is not None
    rd = recipe.to_dict()
    if embed:
        return ProjectEntry(
            pattern.name,
            "embedded",
            pattern.sha256,
            rd,
            pattern.source_path,
            embedded={
                "two_theta_deg": pattern.two_theta_deg.tolist(),
                "intensity": pattern.intensity.tolist(),
                "intensity_unit": pattern.intensity_unit,
                "metadata": dict(pattern.metadata),
                "synthetic": pattern.synthetic,
                "array_sha256": sha256_arrays(pattern.two_theta_deg, pattern.intensity),
            },
        )
    if example_key is not None:
        return ProjectEntry(pattern.name, "example", pattern.sha256, rd, example_key=example_key)
    if pattern.source_path is None:
        raise ProjectError(f"{pattern.name}: in-memory data can only be saved with embed=True")
    return ProjectEntry(
        pattern.name,
        "file",
        pattern.sha256,
        rd,
        pattern.source_path,
        read_options.to_dict() if read_options else None,
    )


def save_project(project: Project, path: str | Path) -> Path:
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(project.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # atomic: a crash never leaves a half-written project
    return p


def load_project(path: str | Path) -> Project:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ProjectError(f"cannot read project: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectError(f"project file is corrupted (invalid JSON): {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != PROJECT_FORMAT:
        raise ProjectError("not an XRD Analyzer project file")
    if data.get("version") != PROJECT_VERSION:
        raise ProjectError(f"unsupported project version {data.get('version')!r}")
    try:
        entries = [ProjectEntry(**e) for e in data.get("entries", [])]
    except TypeError as exc:
        raise ProjectError(f"project entry malformed: {exc}") from exc
    active = data.get("active")
    settings = data.get("plot_settings")
    return Project(
        entries,
        active if type(active) is int else None,
        settings if isinstance(settings, dict) else {},
    )


def resolve_entry(entry: ProjectEntry) -> tuple[XRDPattern, AnalysisRecipe]:
    """Re-create the pattern for ``entry`` and verify its hash. Raises ProjectError."""
    try:
        return _resolve_entry(entry)
    except (ProjectError, XRDFormatError):
        raise
    except (TypeError, AttributeError, KeyError, ValueError) as exc:  # fields of the wrong kind
        raise ProjectError(f"{entry.name}: project entry malformed: {exc!r}") from exc


def _resolve_entry(entry: ProjectEntry) -> tuple[XRDPattern, AnalysisRecipe]:
    try:
        recipe = AnalysisRecipe.from_dict(entry.recipe)
    except (ValueError, TypeError) as exc:
        raise ProjectError(f"{entry.name}: invalid recipe: {exc}") from exc
    problems = recipe.validate()
    if problems:
        raise ProjectError(f"{entry.name}: invalid recipe: {'; '.join(problems)}")
    if entry.kind == "example":
        if entry.example_key is None:
            raise ProjectError(f"{entry.name}: example key missing")
        pat = make_example(entry.example_key).pattern
    elif entry.kind == "file":
        if not entry.source_path or not Path(entry.source_path).exists():
            raise ProjectError(f"{entry.name}: raw file not found: {entry.source_path}")
        opts = ReadOptions.from_dict(entry.read_options) if entry.read_options else None
        pat = read_pattern(entry.source_path, opts)
    elif entry.kind == "embedded":
        emb = entry.embedded or {}
        try:
            pat = XRDPattern(
                entry.name,
                emb["two_theta_deg"],
                emb["intensity"],
                emb.get("intensity_unit", "a.u."),
                emb.get("metadata", {}),
                entry.source_path,
                entry.sha256,
                "embedded",
                synthetic=bool(emb.get("synthetic")),
            )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ProjectError(f"{entry.name}: embedded data malformed: {exc!r}") from exc
        if sha256_arrays(pat.two_theta_deg, pat.intensity) != emb.get("array_sha256"):
            raise ProjectError(f"{entry.name}: embedded data hash mismatch (corrupted?)")
        return pat, recipe
    else:
        raise ProjectError(f"{entry.name}: unknown entry kind {entry.kind!r}")
    if pat.sha256 != entry.sha256:
        raise ProjectError(
            f"{entry.name}: SHA-256 mismatch — the raw data changed since the project was "
            f"saved (expected {str(entry.sha256)[:12]}…, found {str(pat.sha256)[:12]}…)"
        )
    return pat, recipe

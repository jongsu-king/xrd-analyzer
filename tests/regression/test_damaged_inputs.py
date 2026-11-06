"""Damaged files must end in a clear error, never an unexpected exception or a wrong result.

The seeded runs repeat a larger search that found these problems: corrupt deflate data, encrypted
members and project fields of the wrong kind all raised exceptions the GUI and CLI do not expect.
"""

import json
import random
import re
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest

from unit.test_bruker_brml import COUNTS, TWO_THETA, make_brml
from xrd_analyzer.core.analysis import AnalysisError, run_analysis
from xrd_analyzer.core.synthetic import make_example
from xrd_analyzer.io import ReadOptions, XRDFormatError, probe_file, read_pattern
from xrd_analyzer.models.recipe import AnalysisRecipe
from xrd_analyzer.project import (
    Project,
    ProjectEntry,
    ProjectError,
    entry_for,
    load_project,
    resolve_background,
    resolve_entry,
    save_project,
)

TOKENS = (b"nan", b"inf", b"-inf", b"1e400", b"-1", b"0", b"1_000", b"", b"\xff\xfe", b"\x00", b'"')
TEXT = "2theta_deg,intensity_counts\n" + "\n".join(
    f"{x:.4f},{int(y)}" for x, y in zip(TWO_THETA, COUNTS, strict=True)
)


def _damage(data: bytes, rng: random.Random) -> bytes:
    b = bytearray(data)
    for _ in range(rng.choice((1, 1, 2, 4, 16))):
        if not b:
            break
        i = rng.randrange(len(b))
        op = rng.randrange(4)
        if op == 0:
            b[i] ^= 1 << rng.randrange(8)
        elif op == 1:
            del b[i : i + rng.randrange(1, 200)]
        elif op == 2:
            b[i:i] = rng.choice(TOKENS)
        else:
            del b[i + 1 :]
    return bytes(b)


def _read_cleanly(path, options=None):
    """Only a format error or a file-system error may come out."""
    try:
        probe_file(path, options)
    except (XRDFormatError, OSError):
        pass
    try:
        return read_pattern(path, options)
    except (XRDFormatError, OSError):
        return None


def _analyse_cleanly(pattern):
    if pattern is None:
        return
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    try:
        run_analysis(pattern, recipe).peak_rows()
    except AnalysisError:
        pass


def test_damaged_text_files(tmp_path):
    rng = random.Random(1)
    path = tmp_path / "scan.csv"
    for _ in range(200):
        path.write_bytes(_damage(TEXT.encode(), rng))
        _analyse_cleanly(_read_cleanly(path))


def test_damaged_brml_archives(tmp_path):
    seed = tmp_path / "seed.brml"
    make_brml(seed)
    rng = random.Random(2)
    path = tmp_path / "scan.brml"
    for _ in range(200):
        path.write_bytes(_damage(seed.read_bytes(), rng))
        _analyse_cleanly(_read_cleanly(path))


def test_damaged_xml_inside_brml(tmp_path):
    seed = tmp_path / "seed.brml"
    make_brml(seed)
    with zipfile.ZipFile(seed) as z:
        members = {n: z.read(n) for n in z.namelist()}
    values = ("", "x", "nan", "inf", "-1", "0", "1e400", "1.5", "99999999", "true", "Measured")
    rng = random.Random(3)
    path = tmp_path / "scan.brml"
    for _ in range(300):
        name = rng.choice(sorted(members))
        root = ET.fromstring(members[name])
        elements = list(root.iter())
        for _ in range(rng.choice((1, 1, 2, 3))):
            el = rng.choice(elements)
            if el.attrib and rng.random() < 0.7:
                el.attrib[rng.choice(sorted(el.attrib))] = rng.choice(values)
            else:
                el.text = rng.choice(values)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for n, v in members.items():
                z.writestr(n, ET.tostring(root) if n == name else v)
        _analyse_cleanly(_read_cleanly(path))


def _patched_archive(seed, path, directory, local):
    """Change one byte in every central-directory and local header of a zip archive."""
    raw = bytearray(seed.read_bytes())
    for marker, (offset, change) in ((b"PK\x01\x02", directory), (b"PK\x03\x04", local)):
        for m in re.finditer(marker, bytes(raw)):
            raw[m.start() + offset] = change(raw[m.start() + offset])
    path.write_bytes(bytes(raw))


@pytest.mark.parametrize(
    ("directory", "local", "words"),
    [
        ((8, lambda v: v | 1), (6, lambda v: v | 1), "corrupted"),  # encrypted
        ((10, lambda _: 99), (8, lambda _: 99), "corrupted"),  # unknown compression method
    ],
)
def test_archives_python_cannot_open_are_refused(tmp_path, directory, local, words):
    seed = tmp_path / "seed.brml"
    make_brml(seed)
    path = tmp_path / "scan.brml"
    _patched_archive(seed, path, directory, local)
    with pytest.raises(XRDFormatError, match=words):
        read_pattern(path)


def test_corrupt_deflate_data_is_refused(tmp_path):
    path = tmp_path / "scan.brml"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Experiment0/DataContainer.xml", b"<a>" + b"x" * 5000 + b"</a>")
    raw = bytearray(path.read_bytes())
    data = raw.index(b"DataContainer.xml") + len(b"DataContainer.xml")
    raw[data : data + 8] = b"\xff" * 8
    path.write_bytes(bytes(raw))
    with pytest.raises(XRDFormatError, match="corrupted"):
        read_pattern(path)


def test_a_folder_named_like_a_file_is_a_clean_error(tmp_path):
    for name in ("x.brml", "x.csv"):
        (tmp_path / name).mkdir()
        with pytest.raises((OSError, XRDFormatError)):
            read_pattern(tmp_path / name)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be", "utf-32"])
def test_a_dtd_is_refused_in_any_encoding(tmp_path, encoding):
    head = f'<?xml version="1.0" encoding="{encoding}"?>'
    xml = (head + '<!DOCTYPE a [<!ENTITY b "c">]><a>&b;</a>').encode(encoding)
    path = tmp_path / "scan.brml"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Experiment0/DataContainer.xml", xml)
    with pytest.raises(XRDFormatError, match=r"DTD|not valid XML"):
        read_pattern(path)


def _edited_brml(tmp_path, old, new, member="Experiment0/RawData0.xml"):
    seed = tmp_path / "seed.brml"
    make_brml(seed)
    path = tmp_path / "scan.brml"
    with zipfile.ZipFile(seed) as a, zipfile.ZipFile(path, "w") as b:
        for n in a.namelist():
            v = a.read(n)
            if n == member:
                assert old.encode() in v
                v = v.replace(old.encode(), new.encode())
            b.writestr(n, v)
    return path


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('Start="0"', 'Start="x"'),
        ('Start="0"', 'Start="-3"'),
        ("<BlankingValue>-9999</BlankingValue>", "<BlankingValue>x</BlankingValue>"),
        ('MeasuredSteps="200"', 'MeasuredSteps="x"'),
    ],
)
def test_nonsense_in_the_xml_is_a_format_error(tmp_path, old, new):
    with pytest.raises(XRDFormatError):
        read_pattern(_edited_brml(tmp_path, old, new))


@pytest.mark.parametrize("value", ["nan", "inf", "-2.5", "0"])
def test_a_counting_time_that_is_not_one_is_refused(tmp_path, value):
    path = tmp_path / "scan.brml"
    make_brml(path, times=[value] * TWO_THETA.size)
    with pytest.raises(XRDFormatError, match="counting time"):
        read_pattern(path)


@pytest.mark.parametrize("value", ["nan", "inf"])
def test_counts_that_are_not_numbers_are_refused(tmp_path, value):
    path = tmp_path / "scan.brml"
    make_brml(path, counts=[value, *COUNTS[1:]])
    with pytest.raises(XRDFormatError, match="whole non-negative counts"):
        probe_file(path)


# ------------------------------------------------------------------ text encodings


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32", "cp949"])
def test_text_is_read_in_the_encodings_windows_programs_save(tmp_path, encoding):
    path = tmp_path / "scan.csv"
    path.write_bytes(TEXT.encode(encoding))
    pat = read_pattern(path)
    assert np.array_equal(pat.two_theta_deg, np.round(TWO_THETA, 4))
    assert np.array_equal(pat.intensity, COUNTS)


def test_utf16_with_a_korean_header_keeps_its_comment(tmp_path):
    path = tmp_path / "scan.csv"
    path.write_bytes(("# 시료: 실리콘\n" + TEXT).encode("utf-16"))
    assert read_pattern(path).metadata["시료"] == "실리콘"


def test_damaged_utf16_is_refused(tmp_path):
    path = tmp_path / "scan.csv"
    path.write_bytes("2theta_deg,counts\n20,1\n".encode("utf-16") + b"\x00")
    with pytest.raises(XRDFormatError, match="damaged utf-16"):
        read_pattern(path)


def test_text_without_a_byte_order_mark_is_still_called_binary(tmp_path):
    path = tmp_path / "scan.csv"
    path.write_bytes(TEXT.encode("utf-16-le"))
    with pytest.raises(XRDFormatError, match="binary"):
        read_pattern(path)


def test_a_named_encoding_reads_text_without_a_byte_order_mark(tmp_path):
    path = tmp_path / "scan.csv"
    path.write_bytes(TEXT.encode("utf-16-le"))
    assert (
        read_pattern(path, ReadOptions(encoding="utf-16-le")).two_theta_deg.size == TWO_THETA.size
    )


def test_an_underscore_does_not_make_a_number(tmp_path):
    path = tmp_path / "scan.csv"
    path.write_text("2theta_deg,intensity_counts\n20.0,1_000\n20.1,2\n", encoding="utf-8")
    with pytest.raises(XRDFormatError, match="malformed data row"):
        read_pattern(path)


def test_infinity_in_a_rate_column_gives_no_warning_and_no_suggestion(tmp_path, recwarn):
    path = tmp_path / "scan.xy"
    head = '\'Id: "x" Anode: "Cu" Wavelength: "1.54" Scantype: "c" TimePerStep: "28.5"\n'
    path.write_text(head + "20.0 inf\n20.1 1.5\n20.2 2.5\n20.3 3.5\n20.4 4.5\n", encoding="utf-8")
    probe = probe_file(path, ReadOptions(axis_quantity="2theta", axis_unit="deg"))
    assert probe.suggested_intensity_unit is None
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]


# ------------------------------------------------------------------ projects


@pytest.fixture(scope="module")
def saved_project(tmp_path_factory):
    folder = tmp_path_factory.mktemp("project")
    data = folder / "scan.csv"
    data.write_text(TEXT, encoding="utf-8")
    pat = read_pattern(data)
    recipe = AnalysisRecipe()
    recipe.preprocess.wavelength_A = 1.5406
    example = make_example("si_like").pattern
    entries = [
        entry_for(pat, recipe, ReadOptions()),
        entry_for(example, recipe, None, example_key="si_like"),
        entry_for(pat, recipe, None, embed=True, background=pat),
    ]
    return save_project(Project(entries), folder / "p.xrdproj.json").read_bytes()


def _junk(rng):
    return rng.choice((None, 0, -1, 1.5, "", "x", [], {}, [1], {"a": 1}, True, 10**30))


def _poke(node, rng, depth=0):
    if isinstance(node, dict) and node:
        key = rng.choice(sorted(node))
        if rng.random() < 0.3 or depth > 5:
            node[key] = _junk(rng)
        else:
            _poke(node[key], rng, depth + 1)
    elif isinstance(node, list) and node:
        i = rng.randrange(len(node))
        if rng.random() < 0.4 or depth > 5:
            node[i] = _junk(rng)
        else:
            _poke(node[i], rng, depth + 1)


def _load_cleanly(path):
    try:
        project = load_project(path)
    except ProjectError:
        return
    for entry in project.entries:
        try:
            pattern, recipe = resolve_entry(entry)
            resolve_background(entry)
            if not recipe.validate():
                run_analysis(pattern, recipe)
        except (ProjectError, XRDFormatError, OSError, AnalysisError):
            pass


def test_damaged_projects(saved_project, tmp_path):
    rng = random.Random(4)
    path = tmp_path / "p.xrdproj.json"
    for k in range(300):
        if k % 3 == 0:
            path.write_bytes(_damage(saved_project, rng))
        else:
            data = json.loads(saved_project)
            for _ in range(rng.choice((1, 1, 2, 3))):
                _poke(data, rng)
            path.write_text(json.dumps(data), encoding="utf-8")
        _load_cleanly(path)


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "embedded", "embedded": "not an object"},
        {"kind": "embedded", "embedded": {"two_theta_deg": 1, "intensity": 2}},
        {
            "kind": "embedded",
            "embedded": {"two_theta_deg": [1], "intensity": [1], "intensity_unit": {}},
        },
        {"kind": "example", "example_key": "no such example"},
        {"kind": "example", "example_key": ["x"]},
        {"kind": "file", "source_path": 5},
        {"kind": "file", "source_path": ["x"]},
        {"recipe": None},
        {"recipe": "x"},
    ],
)
def test_project_fields_of_the_wrong_kind_are_a_project_error(fields):
    recipe = AnalysisRecipe().to_dict()
    entry = ProjectEntry(
        **{"name": "n", "kind": "embedded", "sha256": "0", "recipe": recipe, **fields}
    )
    with pytest.raises(ProjectError):
        resolve_entry(entry)


@pytest.mark.parametrize("field", ["intensity_unit", "two_theta_deg", "intensity"])
def test_embedded_background_fields_of_the_wrong_kind_are_a_project_error(saved_project, field):
    entry = ProjectEntry(**json.loads(saved_project)["entries"][2])
    entry.background[field] = {"a": 1}
    with pytest.raises(ProjectError):
        resolve_background(entry)


def test_read_options_with_unknown_fields_are_a_project_error(saved_project, tmp_path):
    data = json.loads(saved_project)
    data["entries"][0]["read_options"] = {"no_such_option": 1}
    path = tmp_path / "p.xrdproj.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProjectError, match="malformed"):
        resolve_entry(load_project(path).entries[0])


def test_a_recipe_that_is_not_an_object_is_refused():
    for value in (None, "x", 5, []):
        with pytest.raises(ValueError, match="not an XRD Analyzer recipe"):
            AnalysisRecipe.from_dict(value)

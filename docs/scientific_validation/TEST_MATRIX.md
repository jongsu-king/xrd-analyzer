# Test matrix

| Area | Test file | Cases |
|---|---|---|
| Bragg, Scherrer, W–H, Caglioti, cubic extinction | tests/unit/test_crystallography.py | known answers, θ/2θ confusion, refusal conditions, warnings |
| Profiles, preprocessing, baselines, noise | tests/unit/test_profiles_preprocessing.py | FWHM/area, Kα2 strip, SG, SNIP/ALS/poly/masked, duplicates, crop, background |
| Fitting, detection, edits, uncertainty | tests/unit/test_fit_peaks.py | exact fits, Poisson coverage, Voigt, overlap, doublet, identifiability, cancel |
| Readers / validators | tests/unit/test_readers.py | headers, encodings (cp949), axis confirmation, malformed/empty/binary/NaN/out-of-range, vendor refusal, 200k-row file |
| Recipe, reference, project | tests/unit/test_recipe_reference_project.py | JSON round-trip, validation, licensed CSV refs, hash mismatch, corrupted project |
| Full pipeline known answers | tests/integration/test_known_answer_pipeline.py | 3 synthetic examples, Kα2 modes, masked baseline, batch + statistics, failures |
| CLI and exports | tests/integration/test_cli_exports.py | all export formats, CSV/JSON round trip, Korean PDF glyphs, CLI exit codes, batch partial failure |
| Regression | tests/regression/test_golden_examples.py | generator SHA-256 and result drift |
| Real-file findings | tests/regression/test_real_data_findings.py | count rates vs counts, Bruker text header, detection recall and false positives, baseline bias, poor-fit and bound flags, doublet labelling, new CLI options |
| Baseline width and sampling | tests/regression/test_baseline_width_and_sampling.py | known answers across peak widths and steps, flag and its false alarms, mixed steps, dense patterns, absurd intensities, scale factors |
| Damaged input | tests/regression/test_damaged_inputs.py | seeded damage to text, BRML archives, XML and projects; encrypted and corrupt members; DTDs in any encoding; text encodings |
| Bruker BRML | tests/unit/test_bruker_brml.py | synthetic archives: columns from DataViews, wavelengths not applied, refusals for unverified versions/scans/absorbers, damaged archives, unsafe paths |
| Origin folder | tests/integration/test_origin_bundle.py | CSV contents, ASCII and line ends, script commands, relocatable, unsafe names, several scans. On Windows: launcher with a stand-in program, Origin search, spaces and Korean in paths. Real Origin is not available |
| Real files (optional) | tests/integration/test_real_bruker_files.py | `XRD_REAL_DATA_DIR`: `.brml` vs vendor `.xy`, exact text read, both routes find the same strong peaks |
| GUI (offscreen) | tests/gui/test_gui_smoke.py | threaded run, export, undo/redo, errors, import confirmation, batch, pause/cancel, KO/EN, theme, project |

Commands: `pytest`, `pytest --cov=xrd_analyzer` (UI omitted from coverage),
`ruff check src tests`, `mypy` (UI excluded), GUI tests need `QT_QPA_PLATFORM=offscreen`.

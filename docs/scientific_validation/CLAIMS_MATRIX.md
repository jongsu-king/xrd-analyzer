# Claims matrix — every claim and its evidence

| Claim | Status | Evidence |
|---|---|---|
| Reads delimited text (CSV/TXT/XY/DAT/TSV) 2θ–intensity | SYNTHETIC_ONLY | tests/unit/test_readers.py |
| Reads Bruker `.xy` text exports | checked on 4 real files (not committed) | tests/integration/test_real_bruker_files.py, needs XRD_REAL_DATA_DIR; test_bruker_header_becomes_metadata |
| Reads Bruker `.brml`: coupled 2θ/θ PSD fast scan, DIFFRAC 8.6.3.0 | VERIFIED_NATIVE for that version and scan type: 4 real files match the vendor export (not committed) | tests/unit/test_bruker_brml.py, tests/integration/test_real_bruker_files.py |
| Fractional values cannot be read as counts; no unit defaults to counts | implemented | test_fractional_values_cannot_be_counts, test_import_dialog_never_defaults_to_counts |
| Peak finding judges a peak as a whole, independent of sampling | synthetic Monte Carlo and 4 real scans | test_weak_peak_with_no_significant_point_is_found, test_detection_does_not_depend_on_how_finely_the_scan_is_sampled, VALIDATION.md |
| Noise alone rarely gives a peak | synthetic Monte Carlo: false peaks in 2 of 2100 scans | test_noise_alone_gives_no_peaks, VALIDATION.md |
| Baselines follow the mean background | implemented, checked on real scans | test_baseline_follows_the_mean_of_the_noise, test_snip_keeps_no_plateau_near_the_scan_start |
| Polynomial and peak-masked baselines keep broad peaks; SNIP and ALS flag those they take in | synthetic known answers, FWHM up to 2° | test_broad_peaks_keep_their_area, test_a_peak_the_baseline_follows_is_flagged |
| Baseline results do not depend on the step, 0.005–0.0002° | synthetic known answers, within 2 % | test_results_do_not_depend_on_how_finely_the_scan_is_sampled |
| Damaged files give a clear error | seeded search over damaged text, BRML and project files | tests/regression/test_damaged_inputs.py |
| A .xrdorigin file starts only files this version wrote | implemented; Windows launcher tested on CI | test_a_changed_folder_is_not_started |
| Opens results in OriginLab Origin and saves an .opju | **not tested with real Origin**: data files and launcher are tested, the LabTalk script follows the documentation | tests/integration/test_origin_bundle.py |
| Reads Rigaku .ras/.rasx, PANalytical .xrdml, Bruker .raw, Philips .udf | UNSUPPORTED (explicit error) | test_vendor_formats_are_refused |
| Never guesses axis quantity/unit; θ header needs confirmation | implemented | test_headerless_requires_axis_confirmation, test_theta_header_is_never_silently_converted |
| Raw data immutable, SHA-256 recorded | implemented | test_raw_data_is_immutable_and_file_untouched, test_project_detects_changed_missing_and_corrupt |
| Peak positions / d / FWHM reproduce ground truth | synthetic only; on real scans positions agree with an independent refit and index to one cell (no certified standard measured) | test_positions_d_and_widths, VALIDATION.md |
| Scherrer only with K and β_i; NOT_CALCULABLE otherwise | implemented | test_scherrer_requires_instrument_and_K, test_si_resolution_and_honest_size_refusal |
| W–H only with ≥ 3 suitable peaks | implemented | test_williamson_hall_needs_three_peaks, test_nanocrystalline_size_and_strain |
| Amorphous hump separated into the baseline | synthetic only (masked baseline) | test_masked_baseline_separates_amorphous_hump |
| Kα2: strip and doublet fit | synthetic only | test_kalpha2_strip_mode, test_doublet_fit_reports_kalpha1_position |
| Phase candidates with confidence, no phase fractions | implemented | test_reference_match_high_for_correct_phase, ..._low_for_wrong_phase_or_wavelength |
| Fit covariance, residuals, χ²_red, identifiability flags | implemented | test_poisson_fit_errors_are_consistent, test_unidentifiable_parameters_are_flagged |
| Exports CSV/JSON/PNG/SVG/HTML/PDF; Korean text in PDF | implemented (macOS AppleGothic) | test_export_all_files_and_round_trip, test_korean_names_render_in_pdf_without_missing_glyphs |
| Project save/restore, recipe save/restore | implemented | test_project_round_trip_example_and_file, test_recipe_json_round_trip |
| Batch with partial failure and batch statistics | implemented | test_batch_continues_after_failure, test_batch_peak_statistics, test_cli_batch_with_partial_failure |
| GUI: threaded run, cancel/pause, undo/redo, KO/EN, themes | offscreen smoke test (macOS) | tests/gui/test_gui_smoke.py |
| Packaged builds verify themselves (frozen self-test + GUI smoke test) | macOS arm64 verified locally; Windows/Linux/macOS verified in the release workflow | `xrd-analyzer selftest`, `.github/workflows/release.yml` |
| Robustness regressions (header inference, malformed rows, non-finite input, convergence, provenance) | implemented | tests/regression/test_robustness_*.py, tests/unit/test_reader_header_rules.py |

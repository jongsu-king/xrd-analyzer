# Claims matrix — every claim and its evidence

| Claim | Status | Evidence |
|---|---|---|
| Reads delimited text (CSV/TXT/XY/DAT/TSV) 2θ–intensity | SYNTHETIC_ONLY | tests/unit/test_readers.py |
| Reads Rigaku .ras/.rasx, PANalytical .xrdml, Bruker .raw/.brml, Philips .udf | UNSUPPORTED (explicit error) | test_vendor_formats_are_refused |
| Never guesses axis quantity/unit; θ header needs confirmation | implemented | test_headerless_requires_axis_confirmation, test_theta_header_is_never_silently_converted |
| Raw data immutable, SHA-256 recorded | implemented | test_raw_data_is_immutable_and_file_untouched, test_project_detects_changed_missing_and_corrupt |
| Peak positions / d / FWHM reproduce ground truth | synthetic only | test_positions_d_and_widths |
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

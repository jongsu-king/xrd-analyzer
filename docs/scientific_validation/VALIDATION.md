# Scientific validation

The synthetic sections are from v0.1.0 (2026-11-03) unless noted. They show that the algorithms
are self-consistent. The section on real files, added afterwards, covers one instrument only.

## Acceptance tolerances (tests/integration/test_known_answer_pipeline.py)
| Quantity | Tolerance | Rationale |
|---|---|---|
| peak position | max(0.005°, 1 % of FWHM) | well below typical step (0.01–0.02°) |
| d-spacing | 2 × 10⁻⁴ Å | follows from the position tolerance |
| FWHM | 6 % relative | Poisson noise, pV/baseline coupling |
| W–H size D | 5 % and within 3σ | |
| W–H strain ε | 15 % | ε is the most baseline-sensitive quantity |

## Observed results (local run, macOS 27.0.1 arm64, Python 3.12.14, NumPy 2.5.4, SciPy 1.18.1)
| Example | Result |
|---|---|
| si_like | 6/6 reflections. |Δ2θ| ≤ 0.001°, |Δd| ≤ 1e-5 Å, FWHM within 4 %. Scherrer and W–H NOT_CALCULABLE (no instrumental broadening given), as required |
| si_like_ka2 (doublet fit) | 6/6 Kα1 positions within 0.001° |
| si_like_ka2 (Rachinger strip) | 6/6 positions within tolerance after post-fit noise rejection |
| si_like_ka2 (Kα2 ignored) | Kα2 lines show up as extra "peaks" (e.g. 88.30°) and the note "Kα2 not handled" is shown. This is the expected visible failure mode |
| nano_fcc (masked baseline) | W–H D = 14.99 ± 0.23 nm (truth 15), ε = 1.52 × 10⁻³ ± 0.08 × 10⁻³ (truth 1.5 × 10⁻³). Single-peak Scherrer 10–12 nm (< truth, because strain is ignored). Hump baseline error ≈ 5 counts (SNIP: ≈ 300 counts) |

## Analytic checks (unit tests)
Si(111) 2θ = 28.4419° for Cu Kα1; the θ/2θ mix-up gives a > 1 Å error and is tested; σ_d
matches a numerical derivative; Scherrer and W–H reproduce constructed D/ε to 1e-6;
pseudo-Voigt FWHM/area and the Voigt FWHM approximation are checked numerically; Rachinger
stripping restores single profiles to < 2 % of the peak height and has no edge step;
the covariance helper equals (JᵀJ)⁻¹; ±2σ position coverage is ≥ 16/20 in a Poisson
Monte-Carlo.

## Real instrument files (2026-11-04)
Four coupled 2θ/θ scans from one Bruker D2 PHASER (Cu, DIFFRAC 8.6.3.0, 5–50°, 4453 points,
step 0.0101°, 28.548 s/step; two are repeats of one sample). The data are research data and are
not redistributed. There is no certified standard among them, so absolute position accuracy
is **not** established.

| Check | Result |
|---|---|
| `.brml` vs the same scan's vendor `.xy` | 2θ within 1.83×10⁻⁶°; counts ÷ time within 5×10⁻⁴ of the exported rate (its rounding) |
| Poisson model | peak-free regions: variance/mean 1.00–1.15, lag-1 autocorrelation −0.04 to +0.05 |
| Peak positions vs an independent doublet refit of the raw counts | all 40 peaks within 0.0016° and 0.70σ |
| Kα2 model, best-resolved strong reflection, free ratio and separation | separation within 0.0021° of Bragg; ratio 0.46–0.47 against the assumed 0.5 |
| Physical consistency | 7 reflections index to one cubic cell with a free zero shift: rms 0.003–0.011°, zero shift 0.03–0.04° |
| Peak finding vs an independent matched filter (z ≥ 6 in at least 2 scans) | raw counts, default settings: 11 of 11 in all four scans, SNIP and masked baselines. The count-rate export: 11 of 11, except 10 of 11 once with the masked baseline. v0.1.1 found 7 of 11 from raw counts |
| Baseline bias in peak-free regions | SNIP from about +20 to −0.6…−0.9 counts, ALS from +14 to −0.3…−0.6, polynomial from +22 to −0.9…−1.4; masked stays within ±0.1. Peak areas changed by < 0.5 % |
| Repeat scans: rms of Δ/√(σ₁²+σ₂²), 11 peaks | position 1.11, area 1.33, height 1.96, FWHM 2.51 |

The last row is why σ values are described as statistical only: position uncertainties nearly
match the repeat scatter, while height and FWHM are about 2–2.5× too small. The strongest low-angle reflection is
asymmetric (χ²_red about 5–10); a split-width model lowers χ²_red to about 2–2.6 and moves its
centre by 0.008–0.010°, far more than its reported σ. It is flagged `poor_profile_fit`.

## Detection Monte Carlo (2026-11-04)
Poisson scans with known content, default settings (`tools/detection_monte_carlo.py`): noise only, or one injected
peak on 80 counts/step, 300 scans per condition (150 for the masked baseline).

| | v0.1.1 | this version |
|---|---|---|
| False peaks, noise only, 2100 scans (flat 10/80/1000 counts, a background shaped like the real scans, SNIP/masked, counts/a.u.) | 8 scans | 2 scans |
| Found: FWHM 0.1°, height 2.2σ per point | 0 % | 20 % |
| Found: FWHM 0.1°, height 3.4σ per point | 0 % | 93 % |
| Found: FWHM 0.1°, height 4.5σ per point | 0 % | 99 % |
| Found: FWHM 0.1°, height 6.7σ per point | 3 % | 100 % |
| Found: FWHM 0.1°, height 8.9σ per point | 81 % | 100 % |
| Found: FWHM 0.25°, height 1.7σ per point | 0 % | 25 % |
| Found: FWHM 0.25°, height 2.8σ per point | 0 % | 100 % |
| Found: FWHM 0.25°, height 4.5σ per point | 0 % | 100 % |

## Not validated
That Origin accepts the generated LabTalk script and project: no copy of Origin was available, so
the script follows OriginLab's documentation but has never run. Absolute peak-position accuracy on real data (no certified standard was measured); crystallite
sizes on real data (no instrumental broadening was measured); instruments and software other
than the one above; other vendor formats; interactive GUI use on a real display beyond the
offscreen smoke tests; Intel macOS builds.

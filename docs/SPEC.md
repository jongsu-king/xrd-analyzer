# Scientific specification — XRD Analyzer v0.1

Scope: 1D θ–2θ (Bragg–Brentano-type) powder and thin-film patterns exported as text.
Not in scope for v0.1: rocking curves, RSM, XRR, in-plane, 2D detector images, Rietveld,
quantitative phase analysis.

## 1. Input and data model
| Quantity | Symbol | Unit | Source |
|---|---|---|---|
| diffraction angle | 2θ | degree | file column. The user must confirm it unless the header says so unambiguously (`2theta_deg`, `2θ (°)`) |
| intensity | I | counts / cps / a.u. | file column; unit from header or user, else `a.u.` + flag |
| wavelength | λ (Kα1), λ2 (Kα2) | Å | **user** (presets: Cu Kα1 1.5405929 Å, Kα2 1.5444274 Å, ITC Vol. C Table 4.2.2.1); 0.05–5 Å enforced |
| Kα2/Kα1 ratio | R | – | user (default 0.5) |
| Scherrer shape factor | K | – | user (no default) |
| instrumental FWHM | β_i | degree 2θ | user: constant or Caglioti U,V,W (deg²) |

Raw arrays are immutable copies. Sorting, duplicate handling and cropping produce new
arrays. The SHA-256 of the source file is recorded.

**Stop conditions (explicit errors):** missing/ambiguous axis quantity or unit; header says
"theta" without confirmation; any 2θ outside (0°, 180°); NaN/Inf unless the user chooses
to drop them (recorded); duplicate 2θ unless a policy is chosen; < 5 points; binary or
vendor files; missing wavelength; inconsistent recipe.

## 2. Pre-processing (in order)
1. Stable sort by 2θ; duplicate policy `error | average | keep_first`.
2. Optional crop.
3. Optional measured background (empty holder/dark) subtraction, linearly interpolated, no
   extrapolation, scale factor s: I ← I − s·B(2θ). The Poisson variances are added.
4. Kα2: `none` (noted), `strip` (Rachinger; I₁(2θ) = I(2θ) − R·I₁(2θ₁),
   sin θ₁ = sin θ·λ1/λ2. Below the scan start a locally flat profile, I₁ = I/(1+R), is
   assumed), or `fit_doublet` (each reflection is fitted as a Kα1+Kα2 pair: same width
   and shape, Kα2 position from Bragg's law, height R).
5. Savitzky–Golay smoothing is used for **detection only**. Fits always use unsmoothed data,
   so the covariance stays valid. It is shown overlaid on the original.
6. Baseline (continuous + amorphous): SNIP (LLS), ALS, iterative modified polynomial, or
   **peak-masked Whittaker** (provisional peaks are masked ±k·FWHM and the smoother runs
   through the remaining points. This keeps broad amorphous humps in the baseline without
   absorbing Bragg-peak tails). Overshoot warning if > 5 % of points are 3σ below the baseline.
   SNIP, ALS and the polynomial follow the bottom of the noise (about 2σ under the mean), so
   the running median of data − baseline is smoothed and added back. SNIP also runs on an
   odd extension of the data, so points near the scan ends are clipped as often as interior
   ones; otherwise the noise floor changes there and looks like a broad peak.

## 3. Peaks
- Detection: `scipy.signal.find_peaks` with height **and** prominence ≥ k·σ. σ is the Poisson
  √counts for raw counts, otherwise a rolling MAD of first differences. FWHM ≥ 1.5 steps
  is required, plus optional min/max width (max width rejects amorphous humps).
- The same test runs on Gaussian-smoothed copies of the unsmoothed net signal (FWHM 4, 8, 16
  and 32 steps), against the σ of the smoothed signal: σ_s² = Σ g²σ². Otherwise a peak spread
  over many points is missed however significant it is, and finer sampling would make peaks
  harder to find. A peak above 2× the single-point threshold keeps its single-point position
  and width; any other feature takes the scale where it is most significant, which gives
  the fit window a width that noise cannot set. Features wider than 3× the kernel, and hits
  whose kernel reaches past the scan end, are ignored.
- In doublet mode, auto-detected Kα2 partners of stronger peaks are merged.
- Manual edits (add at 2θ / remove near 2θ) are stored in the recipe and replayed exactly.
- Post-fit noise rejection: an auto-detected peak is dropped and the rest refitted if its
  fitted FWHM is < 1.5 steps, its height is < 3σ_height, or its significance is below k.
  Significance is √Δχ² = √Σ[(r + c)² − r²]/σ² over the fit window, with r the residuals and c
  the peak's modelled signal, including the Kα2 component. It is exported as
  `significance_sigma`. Manual peaks are kept but flagged.

## 4. Profile fitting
- pseudo-Voigt: H·[η·L + (1−η)·G], both with FWHM w (exactly the FWHM).
  Area = H·w·[η·π/2 + (1−η)·√(π/(4 ln2))].
- Voigt: area-normalised `scipy.special.voigt_profile(σ, γ)`. FWHM from Olivero &
  Longbothum (≈0.02 %).
- Local background: none / constant / linear. Overlapping windows (±k·FWHM) are fitted jointly.
- `scipy.optimize.least_squares` (TRF) with bounds. Weights are 1/√counts for raw counts.
- Covariance (JᵀJ)⁻¹ by SVD, scaled by max(1, χ²_red) (weighted) or χ²_red (unweighted).
  Derived uncertainties (area, Voigt FWHM/height) use the delta method.
- Reported per group: N points, parameters, dof, χ², χ²_red, R², condition number,
  convergence. Flags: parameter at bound, including η or γ (`shape_at_bound`), ill-conditioned
  (cond > 1e10), singular covariance (`parameters_not_identifiable`), undersampled
  (FWHM < 3 steps), low significance (H < 3σ), window truncated at the scan edge.
- Fit quality: χ²_red > 3 flags the group, and χ²_red < 0.5 with ≥ 20 degrees of freedom flags σ
  as too large (count rates labelled as counts look like this). A peak gets `poor_profile_fit`
  from its own points, where its model exceeds 2 % of its maximum, so a wide window cannot dilute
  it: reduced χ² above 3 with Poisson σ; for unweighted fits, whose noise under a strong peak is
  unknown, lag-1 autocorrelation of the residuals more than 3 standard deviations above zero.
- After confirmation, a seed width more than 1.5× away from the fitted width is replaced by the
  fitted width and the fit and confirmation repeated once, so noise in a weak peak's seed cannot
  merge its window with a neighbour's.
- With `fit_doublet`, height and area describe the Kα1 component. The whole reflection's area is
  (1 + R) × area.
- All σ values are statistical. Zero shift, sample displacement, profile-shape error and
  scan-to-scan reproducibility are not included.

## 5. Crystallographic quantities
- Bragg: d = nλ/(2 sin θ), θ = (2θ)/2, n = 1; σ_d = d·cot θ·σ_θ with σ_θ = rad(σ_2θ)/2.
  The wavelength uncertainty is not included.
- Scherrer: D = Kλ/(β cos θ), β = sample FWHM in 2θ **converted to radians**. The sample
  width is β_s = √(β_m² − β_i²) (Gaussian) or β_m − β_i (Lorentzian). **NOT_CALCULABLE**
  when K or β_i is missing, or when β_m ≤ β_i. WARNING when β_i = 0 (lower bound), β_m < 1.2·β_i,
  or D > 150 nm.
- Williamson–Hall (UDM): β_s cos θ = Kλ/D + 4ε sin θ. Weighted linear regression over
  ≥ 3 usable peaks spanning Δ(4 sin θ) ≥ 0.2. Intercept ≤ 0 → size NOT_CALCULABLE;
  intercept within 2σ of 0 → WARNING; negative slope → WARNING. It assumes one phase and
  isotropic broadening.
- Phase comparison is **position-only** against user-licensed reference CSVs or cubic
  reflections computed from a lattice parameter (P/I/F/diamond extinction rules).
  Confidence is high/medium/low from matched fraction and mean |Δ2θ|. No phase fractions
  are ever derived from intensities (texture, multiphase).

## 6. Known-answer data (synthetic, generated by `core/synthetic.py`)
| key | content | ground truth used in tests |
|---|---|---|
| `si_like` | diamond-cubic a = 5.431020511 Å, Cu Kα1, pV η = 0.5, Caglioti widths, Poisson noise | 6 positions, d, FWHM; Scherrer must be NOT_CALCULABLE (no β_i) |
| `si_like_ka2` | same + Kα2 (R = 0.5) | Kα1 positions via doublet fit and via stripping |
| `nano_fcc` | fcc-like a = 4.0786 Å, D = 15 nm, ε = 1.5e-3, K = 0.9, β_i = 0.08°, amorphous hump at 24° | positions, FWHM, W–H D and ε, hump in baseline |

Acceptance tolerances: see `docs/scientific_validation/VALIDATION.md`.

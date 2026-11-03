# Contributing

Thank you for helping. Please keep the project's guiding rule: **never report a number
the data cannot support.**

1. Every numerical claim needs a test with a known answer (analytic or synthetic ground
   truth). Put robustness counter-examples in `tests/regression/`.
2. Vendor formats need real sample files you are allowed to publish plus golden tests
   (see `docs/adapters.md`). Until then they stay `UNSUPPORTED`.
3. Never guess units or the axis quantity (2θ vs θ); ambiguous input must ask the user.
4. Run before a pull request:
   ```bash
   ruff check src tests && ruff format --check src tests && mypy
   QT_QPA_PLATFORM=offscreen pytest
   ```
5. Do not commit private measurements, credentials or absolute paths containing user names.
6. If you change analysis results on purpose, regenerate the golden file
   (`python tests/regression/test_golden_examples.py`) and explain why in `CHANGELOG.md`.
7. Packaging changes: build locally (`docs/BUILDING.md`) and run the frozen self-test
   (`xrd-analyzer-cli selftest`) before opening the pull request.

## XRD Analyzer 0.1.3

XRD Analyzer 0.1.3 fixes the automatic updates of the installed **macOS** builds (and of
Linux builds on distributions whose certificate store is not where the bundled
OpenSSL looks): the updater could not verify GitHub's HTTPS certificate and so never
found a new version. It now falls back to the system's CA bundle. Nothing else changed.

**If you installed 0.1.2 on a Mac or Linux, install 0.1.3 once by hand** - 0.1.2 cannot
reach GitHub to update itself. Windows copies of 0.1.2 update normally. From 0.1.3 on,
updates arrive automatically on every platform.

The analysis is identical to 0.1.1/0.1.2. `build-verification-reports.zip` holds the self-test and GUI
smoke-test reports of every build. Full guide: [docs/INSTALL.md](https://github.com/jongsu-king/xrd-analyzer/blob/main/docs/INSTALL.md).

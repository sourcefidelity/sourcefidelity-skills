# Current state

**Last updated:** November 20, 2026
**Authority:** This file owns current truth: what is implemented, what was verified, and what remains limited or risky. It is a snapshot, not a session log. Work order is `docs/PLAN.md`; design is `docs/ARCHITECTURE.md`; release history is `CHANGELOG.md`.

## 1. Current status

| Item | Value |
|---|---|
| Branch | `fix/review-pipeline-hardening` |
| Source version | `1.0.0` (`VERSION`) |
| Latest published release | `v1.0.0`; `latest.json` still carries `1.0.0` with an empty `sha256` |
| `latest.json` availability | Present on the feature branch only; `main` does not have it, so the default update check reports “Could not check” until it is merged |
| Installed copy | `~/.dsh/skills`, version `1.0.0`, commit `1dc5304`, channel `dev`, shared venv intact |
| Test suites | 304 pipeline tests; style and PDF suites report no failures |
| Docs check | `scripts/check_docs.py`, added with this documentation set |

The package now contains the review, batch and reappraisal skills, the dossier-first workflow, the source pack, the open-access and host-state fetch helpers, and the weekly update check with the release installer.

## 2. Verification record

| What | How it was verified | Result |
|---|---|---|
| Pipeline regressions | `test_pipeline.py` | 304 passed |
| Style and PDF suites | `test_style_signals.py`, `test_pdf_quality.py` | No failures |
| Update detection | `update_check.py` against a file `latest.json`; fresh and stale state; persistent notice; minimum-version warning | Notices correct; no fetch while state is fresh; notice persists |
| Installer update path | Installed `1.0.0` into a temp target, then `install.sh --update` from a `1.1.0` archive with SHA-256 | Verified download, checksum, extraction, reinstall; installed record `1.1.0` |
| Installer check states | `--check` with current and newer `latest.json`, and with an unreachable URL | “up to date”, update notice, and “Could not check” respectively |
| Source pack | Television run's supplied folder (`wilkins1991.pdf`) | Mapped, extracted in ~1 s, printed range 195–211; phrase search returned p. 201 |
| Dual-client fetch | Middleton landing page (curl 403, urllib 200) and a remembered blocked host | Saved through urllib; blocked host refused without retry |
| Installed stamp | `update_check.py --stamp` in `~/.dsh/skills` | `sourcefidelity-review v1.0.0 (commit 1dc5304)` |
| Deploy | `install.sh --target ~/.dsh/skills --no-venv --channel dev` | New scripts and `installed.json` written; venv preserved |
| Docs check | `python scripts/check_docs.py` | ok; the only warning is the known empty `latest.json` checksum |

## 3. Known limitations and risks

| Item | Impact | Mitigation |
|---|---|---|
| `latest.json` is not on `main` and has no checksum | Default `--check` cannot reach it; `--update` refuses an unverified release | Merge the branch, tag the release, fill `sha256`, then verify the default URL |
| PowerShell installer paths unexecuted here | `install.ps1 -Check/-Update` reviewed but not runtime-tested (no PowerShell on this machine) | Run on Windows before announcing the release |
| `openfetch.py` is best-effort | CORE's title search did not return the O'Connell article; a missed work is recorded as `no_candidate`, not absence | Treat it as a first pass; use the dossier and manual retrieval for gaps |
| `sourcepack.py` maps by filename heuristic | Opaque names (`8.pdf`, `Archive.pdf`) do not map | Use `--map R3=PATH`; adjudicate the mapping |
| Review artifacts are snapshots | Earlier reviews (for example the Television review) are not retroactively corrected | Correct only with an explicit logged edit or a reappraisal |
| Review method is long | `REVIEW_PROTOCOL.md` is ~10.7k words and is read on every review | Keep it canonical; do not duplicate it in `ARCHITECTURE.md` |
| Docs check is mechanical | It catches links, budgets and version drift, not stale truth | Review `STATE.md` and `PLAN.md` at release and after structural changes |
| No benchmark of the new speed helpers | The source pack and dossier were measured in isolation, not on a full review | Measure one full review before claiming a speed improvement |

## 4. Maintenance

- Update this file after a verified change in status, test counts, release state or limitations. Replace the affected row; do not append a dated entry.
- Keep the file under 2,000 words. If a row grows into a narrative, move the detail to `ARCHITECTURE.md` or `docs/PLAN.md`.
- The version here must match `VERSION` and `latest.json`; `scripts/check_docs.py` enforces that.
- Last verified commands: the three suites plus `python scripts/check_docs.py` from the repository root.

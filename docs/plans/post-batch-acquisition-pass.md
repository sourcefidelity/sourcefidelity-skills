# Post-batch acquisition and reappraisal pass

**When:** after the batch run has finished (117/117 rendered, `batch_report.md` written) and the VPN can
be turned on. The batch root is `source-use-reviews/batch-20261124-1122`.
**Why:** the run was completed with the VPN off for stability, so VPN-only sources (OpenLibrary,
Google Books, blocked publisher sites) were recorded as blocked or unresolved. This pass fixes source
access without re-running the batch, then reappraises only the affected papers.

**Rule:** VPN on for network steps (phases 1–3, 5); VPN off for model steps (phase 4). The 24 Nov
transport failure happened when model requests ran over the VPN; keeping the phases apart avoids it.

## 0. Freeze a baseline (no VPN)

```sh
ROOT="$HOME/Documents/deepseek-harness/default-workspace/source-use-reviews/batch-20261124-1122"
B="$HOME/.dsh/skills/sourcefidelity-batch/scripts"
R="$HOME/.dsh/skills/sourcefidelity-review/scripts"
PY="$HOME/.dsh/skills/sourcefidelity-batch/.venv/bin/python"
cp -R "$ROOT" "${ROOT}-before-acquisition"
cd "$ROOT"
cp batch_report.md batch_report.before.md
cp acquisition_list.md acquisition_list.before.md
"$PY" -I "$B/validate_batch.py" --batch "$ROOT" --strict
```

Keep `batch_report.before.md`; it is the comparison for phase 6.

## 1. VPN on: preflight the routes

```sh
curl -sS -m 20 -o /dev/null -w "openlibrary %{http_code}\n" "https://openlibrary.org/isbn/9780199947010.json"
curl -sS -m 20 -o /dev/null -w "googlebooks %{http_code}\n" "https://www.googleapis.com/books/v1/volumes?q=isbn:9780199947010"
curl -sS -m 20 -o /dev/null -w "crossref %{http_code}\n" "https://api.crossref.org/works/10.1017/S0143814X07009713"
```

All three must return 200 before continuing. The batch probe on 24 Nov saw OpenLibrary fail with
`curl 35` even with the VPN on; if that repeats, stop and fix the route rather than pushing the pass.

## 2. VPN on: identity resolution and open-access fetch

These are scripts only — no model calls.

```sh
cd "$ROOT"
"$PY" -I "$B/resolve_unsupplied.py" --batch "$ROOT" --refresh
"$PY" -I "$B/enrich_registry.py" --batch "$ROOT" --only unresolved --refresh
"$PY" -I "$B/fetch_open_sources.py" --batch "$ROOT"
"$PY" -I "$B/acquisition_list.py" --batch "$ROOT" --top 20
```

`resolve_unsupplied` queries Crossref/DataCite/DOI handle; `fetch_open_sources` queries OpenAlex,
Internet Archive, Gallica and Lantern. Neither is content evidence: a match is identity, and only a
complete fetched copy becomes a window. Re-read `acquisition_list.md` afterwards; it is the worklist.

## 3. VPN on: download the listed works

For each row in `acquisition_list.md`:

- download the full copy to `"$ROOT/_acquired_inbox/<suggested filename>"` (the suggested filename is in
  the table, for example `croteau-2006-pine-forge-press-t85b3bf0b.pdf`);
- use the reference URL or the identifier shown; a landing page, preview, lending record or abstract is
  **not** a complete copy and must not be supplied as one;
- if only a preview exists, record the work as unavailable in the pass notes rather than inventing a
  copy.

The folder is the input to the reappraisal matcher; filenames do not establish identity, the matcher
does.

## 4. VPN off: reappraise the affected papers

Prepare tasks (matching is local; run it with the VPN off too):

```sh
"$PY" -I "$B/reappraise_prepare.py" --batch "$ROOT" \
  --sources "$ROOT/_acquired_inbox" \
  --out-dir "$ROOT/_reappraise/acq-20261124"
```

This writes one `<paper>.task.json` per affected paper with the scoped C IDs, prior judgments and up to
two passage windows. For each task, run one worker that reads
`sourcefidelity-reappraise/WORKER_CONTRACT_REAPPRAISE.md` and the task, and returns one judgments JSON.
Then apply:

```sh
"$PY" -I "$B/reappraise_apply.py" --batch "$ROOT" \
  --task "$ROOT/_reappraise/acq-20261124/<paper>.task.json" \
  --judgments "<worker>.judgments.json"
```

`apply` keeps `review.pre-reappraisal.md`, rewrites only the scoped entries, appends the reappraisal log,
updates the ledger and the summary, and refuses IDs outside the task. A task that changes nothing is a
valid outcome; say so explicitly.

If a paper's source set changed so much that reappraisal is the wrong tool (a new work alters the
inventory, not one occurrence), re-run that paper's review worker and `_work/finish_paper.sh` instead,
then note it.

## 5. Optional: link re-check (VPN on, no model)

Only for papers whose blocked/unreachable links materially change the report's HL counts:

```sh
"$PY" -I "$R/hyperlink_audit.py" --paper "$ROOT/<PID>/paper-masked.docx" \
  --out "$ROOT/_work/<PID>/links-vpn" --jobs 4 --cache-dir "$ROOT/_work/_link_cache"
```

This writes local link observations; it does **not** rewrite the findings or summary. To apply them,
re-run that paper's worker with the new audit, or reconcile the reference `link_status` fields
manually. Treat this as a separate link-refresh pass, not a reappraisal: a link-state change is not a
C judgment. If the report's HL numbers are acceptable, skip this phase and record it as a limitation.

## 6. Regenerate and verify (no VPN)

```sh
cd "$ROOT"
"$PY" -I "$B/acquisition_list.py" --batch "$ROOT"
"$PY" -I "$B/batch_report.py" --batch "$ROOT"
"$PY" -I "$B/validate_batch.py" --batch "$ROOT" --strict
diff -u batch_report.before.md batch_report.md | head -100
```

Report: works matched, occurrences changed, papers reappraised, files unmatched, link states still
limited, and whether the report's completeness/flag counts moved. Update `docs/STATE.md` with the pass
result and leave the pre-pass copy in place until the result is accepted.

## Guardrails

- Do not run model workers with the VPN on; the 24 Nov batch failed at turn 415 with `TRANSPORT` on that
  route and resumed only after the VPN was disabled.
- Do not retry a `Content Exists Risk` 400 with the VPN toggled; that failure is provider content
  moderation and needs a resumed/pruned transcript, not a different path.
- Do not treat a matched identity, an abstract or a preview as evidence; only a complete copy becomes a
  window.
- Do not re-run the whole batch; the pass is scoped to the works on `acquisition_list.md` and the
  appearances that cite them.

#!/usr/bin/env python3
"""Turn the review skill's link audit into the summary `hyperlinks` block.

The review skill owns the audit (`hyperlink_audit.py`). This module is the
batch side: it runs the audit for one paper, derives the classified counts, and
writes `_work/<paper>/hyperlinks.json`. `render_review.py` reads that file when
the findings do not supply the block, so the pipeline gets the new scope line
without a worker change.

    python -I link_states.py --paper PAPER.docx --out ROOT/_work/PID [--fetch]

`refresh()` rewrites `summary.json` and the review's scope line after a
reappraisal, when new sources change a link's state.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                              "sourcefidelity-review", "scripts")
if REVIEW_SCRIPTS not in sys.path:
    sys.path.insert(0, REVIEW_SCRIPTS)
import hyperlink_audit as ha  # noqa: E402

STATES = ("absent", "non_linkable", "non_permanent", "record_only", "full_text",
          "unreachable", "blocked", "dead", "verified")
ENVIRONMENT = ("unreachable", "tls", "timeout")


def block_from_report(report):
    """Classified counts for one audit report."""
    counts = {key: 0 for key in STATES}
    for row in report.get("rows", []):
        state = row.get("link_class") or ""
        fetch = (row.get("fetch") or {}).get("state") or ""
        if state == "not-linkable":
            counts["non_linkable"] += 1
        elif state == "absent":
            counts["absent"] += 1
        elif state == "non-permanent":
            counts["non_permanent"] += 1
        elif state == "record-only":
            counts["record_only"] += 1
        elif state in ("full-text", "doi", "permanent"):
            if fetch in ENVIRONMENT:
                counts["unreachable"] += 1
            elif fetch in ("blocked", "login-page"):
                counts["blocked"] += 1
            elif fetch == "dead":
                counts["dead"] += 1
            elif fetch == "ok":
                counts["verified"] += 1
            else:
                counts["full_text"] += 1
        else:
            counts["absent"] += 1
    counts["hl_total"] = counts["absent"] + counts["non_permanent"]
    return counts


def audit_paper(paper_docx, fetch=False, timeout=10):
    """(report, block) for one paper; no network unless `fetch` is true."""
    report = ha.audit(paper_docx, fetch=fetch, timeout=timeout)
    report["paper_path"] = os.path.abspath(paper_docx)
    return report, block_from_report(report)


def reaudit_from_cache(root, paper_id):
    """Re-run the audit for a paper from the document path in its report."""
    path = os.path.join(root, "_work", paper_id, "hyperlinks.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    paper_docx = data.get("paper") or ""
    if not paper_docx or not os.path.exists(paper_docx):
        return None
    report, block = audit_paper(paper_docx, fetch=False)
    write(os.path.join(root, "_work", paper_id), report, block)
    return block


def write(work_dir, report, block):
    """Write the cached link report that the renderer reads."""
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, "hyperlinks.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"hyperlinks": block, "checked": report.get("checked", ""),
                   "paper": report.get("paper_path", ""),
                   "rows": report.get("rows", [])}, handle,
                  indent=1, ensure_ascii=False)
        handle.write("\n")
    return path


def load_cached(root, paper_id):
    """The block from a paper's cached link report, or None."""
    path = os.path.join(root, "_work", paper_id, "hyperlinks.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data.get("hyperlinks") or None


def refresh(folder, block):
    """Set the block in summary.json and rewrite the review's scope line."""
    changed = []
    summary_path = os.path.join(folder, "summary.json")
    try:
        with open(summary_path, encoding="utf-8") as handle:
            summary = json.load(handle)
        summary["hyperlinks"] = block
        with open(summary_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(summary, handle, indent=1, ensure_ascii=False)
            handle.write("\n")
        changed.append(summary_path)
    except (OSError, ValueError):
        pass
    review_path = os.path.join(folder, "review.md")
    try:
        import render_review
        lines = render_review.hyperlink_scope_lines({"hyperlinks": block})
    except Exception:  # noqa: BLE001
        lines = []
    if lines:
        text = open(review_path, encoding="utf-8").read()
        pattern = re.compile(r"(?m)^Hyperlinks(?: missing)?: .*$")
        if pattern.search(text):
            text = pattern.sub(lambda _match: lines[0], text, count=1)
        else:
            anchor = "## Summary of academic-integrity findings"
            if anchor in text:
                text = text.replace(anchor, lines[0] + "\n\n" + anchor, 1)
        with open(review_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        changed.append(review_path)
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paper", required=True, help="masked paper DOCX")
    parser.add_argument("--out", required=True, help="work directory for one paper")
    parser.add_argument("--fetch", action="store_true", help="request each link")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args(argv)
    report, block = audit_paper(args.paper, fetch=args.fetch, timeout=args.timeout)
    path = write(args.out, report, block)
    print(json.dumps({"path": path, "hyperlinks": block}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

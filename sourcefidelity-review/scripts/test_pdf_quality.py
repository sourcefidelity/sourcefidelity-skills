#!/usr/bin/env python3
"""Offline tests for pdf_quality.classify and the inspect/OCR plumbing.

    python3 -I test_pdf_quality.py                # unit tests only
    REAL_PDF_DIR=/path/to/pdfs python3 -I test_pdf_quality.py   # + integration

The integration checks take their sample PDFs from environment
variables (REAL_PDF_TEXT, REAL_PDF_TWO_COLUMN,
REAL_PDF_SECOND_TWO_COLUMN, REAL_PDF_FOLIO), so no corpus file
names live in the repository.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pdf_quality", os.path.join(HERE, "pdf_quality.py"))
pq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pq)

FAILURES = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + ("" if condition else f" — {detail}"))
    if not condition:
        FAILURES.append(name)


CLEAN = (
    "The film industry has always depended on distribution agreements.\n"
    "Studio accounting treats prints and advertising as a separate cost line.\n"
    "Analysts disagree about how much of the budget is recovered at the box office."
)

GARBAGE = "ÃƒÂ©Ã¢â‚¬â„¢ Ã¯Â¿Â½Ã¢â‚¬â€œ tÃƒÂ©Ã‚Â®m Ã¯Â¿Â½Ã¯Â¿Â½Ã¯Â¿Â½ " * 20

NO_SPACES = "Thefilmindustryhasalwaysdependedondistributionagreementsandstudios" * 8

DUPLICATED = "Chapter One\n" + "\n".join(["The same line repeated for the text layer."] * 40)

CJK = (
    "电影产业一直依赖发行协议。制片厂的会计制度把拷贝和广告费列为独立成本。"
    "分析人士对票房能收回多少预算存在分歧。" * 3
)

EMPTY = ""


def unit_tests():
    state, m, _ = pq.classify(CLEAN)
    check("clean prose is usable_native", state == "usable_native", state)

    state, m, _ = pq.classify(GARBAGE)
    check("mojibake is bad_native", state == "bad_native",
          f"{state} alpha_share={m['alpha_share']} repl={m['replacement_share']}")

    state, m, _ = pq.classify(NO_SPACES)
    check("missing spaces is bad_native", state == "bad_native",
          f"{state} long_token_share={m['long_token_share']}")

    state, m, _ = pq.classify(DUPLICATED)
    check("duplicated text layer is bad_native", state == "bad_native",
          f"{state} dup={m['duplicate_line_share']}")

    state, m, flags = pq.classify(CJK)
    check("CJK text is usable_native", state == "usable_native",
          f"{state} cjk_share={m['cjk_share']} alpha={m['alpha_share']}")
    check("CJK page notes skipped Latin tests", any("cjk" in f for f in flags), flags)

    state, m, _ = pq.classify(EMPTY)
    check("empty page is empty_page", state == "empty_page", state)

    # OCR text from the real image-only scan, which Vision read accurately.
    scan_sample = (
        "Preface\nAristotle deliberated about what we should do with our leisure time. "
        "The Declaration of Independence refers to the \"pursuit of happiness.\" Mass "
        "media entertainment stretches back to the 15th century in Europe."
    )
    state, _, _ = pq.classify(scan_sample)
    check("OCR-quality prose is usable_native", state == "usable_native", state)


def _sample(env_name):
    """A sample PDF path supplied by the operator, or an empty list."""
    path = os.environ.get(env_name)
    return [path] if path and os.path.exists(path) else []


def integration_tests(root):
    import glob
    image_only = glob.glob(os.path.join(root, "Global Entertainment Media- Content*"))
    text_pdf = _sample("REAL_PDF_TEXT")
    if not image_only or not text_pdf:
        print("SKIP integration: expected test PDFs not found")
        return

    result = pq.inspect_pdf(text_pdf[0], only_pages=[1, 5])
    states = [p["state"] for p in result["pages"]]
    check("digital-born article has usable native text",
          states and all(s == "usable_native" for s in states), states)

    labelled = glob.glob(os.path.join(root, "Global streamers*"))
    if labelled:
        result = pq.inspect_pdf(labelled[0], only_pages=[1])
        label = result["pages"][0].get("page_label")
        check("declared page label preserved", label == "223", label)

    result = pq.inspect_pdf(image_only[0], only_pages=[5, 10], ocr_pages=[5, 10],
                            render=True)
    pages = {p["pdf_page_1based"]: p for p in result["pages"]}
    check("image-only page 5 has no native text", pages[5]["state"] == "empty_page",
          pages[5]["state"])
    check("image-only page 5 OCR'd",
          (pages[5].get("ocr") or {}).get("state") == "done",
          pages[5].get("ocr"))
    check("OCR text recognised",
          "Aristotle" in ((pages[5].get("ocr") or {}).get("text") or ""),
          ((pages[5].get("ocr") or {}).get("text") or "")[:120])
    check("page 5 renders to PNG", bool(pages[5].get("png")) and os.path.exists(pages[5]["png"]),
          pages[5].get("png"))
    check("route records that OCR supplied the usable text",
          pages[5].get("route") == "ocr", pages[5].get("route"))
    check("native state is not overwritten by OCR",
          pages[5].get("state") == "empty_page", pages[5].get("state"))

    # Layout checks against pages verified by eye.
    result = pq.inspect_pdf(text_pdf[0], only_pages=[1])
    print("   native engine:", result.get("native_engine"))
    if str(result.get("native_engine", "")).startswith("pymupdf"):
        single = glob.glob(os.path.join(root, "Global streamers*"))
        if single:
            page = pq.inspect_pdf(single[0], only_pages=[3])["pages"][0]
            check("single-column page is not reordered",
                  page["layout"]["mode"] == "single-flow", page["layout"])
            check("single-column page has no column-major derivative",
                  "text_column_major" not in page)

        page = pq.inspect_pdf(text_pdf[0], only_pages=[10])["pages"][0]
        check("table page is not treated as columns",
              page["layout"]["mode"] != "columns", page["layout"])
        check("table page keeps native order and is flagged",
              "text_column_major" not in page
              and any("order" in w for w in page["warnings"]),
              page["warnings"])

        two_col = _sample("REAL_PDF_TWO_COLUMN")
        if two_col:
            page = pq.inspect_pdf(two_col[0], only_pages=[2])["pages"][0]
            check("two-column page detected", page["layout"]["columns"] == 2, page["layout"])
            check("two-column reading order reconstructed",
                  page.get("text_column_major") and "since 2004" in page["text_column_major"][:400],
                  (page.get("text_column_major") or "")[:200])
            check("native order of the two-column page really was interleaved",
                  "since 2004" not in page["text"][:400], page["text"][:200])

        second = _sample("REAL_PDF_SECOND_TWO_COLUMN")
        if second:
            page = pq.inspect_pdf(second[0], only_pages=[2])["pages"][0]
            check("second two-column article also detected",
                  page["layout"]["columns"] == 2, page["layout"])

        folio = _sample("REAL_PDF_FOLIO")
        if folio:
            page = pq.inspect_pdf(folio[0], only_pages=[3])["pages"][0]
            check("printed folio candidate found in a journal header",
                  (page.get("folio_candidate") or {}).get("text") == "41",
                  page.get("folio_candidate"))
    else:
        print("SKIP layout checks: PyMuPDF not available in this interpreter")
    print("   page 10 OCR confidence:", (pages[10].get("ocr") or {}).get("mean_confidence"),
          "lines:", (pages[10].get("ocr") or {}).get("line_count"))


def main():
    unit_tests()
    root = os.environ.get("REAL_PDF_DIR")
    if root:
        integration_tests(root)
    print("\nFAILURES:", FAILURES if FAILURES else "none")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

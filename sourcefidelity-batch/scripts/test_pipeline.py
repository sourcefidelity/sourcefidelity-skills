#!/usr/bin/env python3
"""Offline regression tests for the batch pipeline.

    python3 -I test_pipeline.py

No network, no PDFs, no OCR. Uses unittest so pytest can also collect it.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import pathlib
import sys
import subprocess
import tempfile
import time
import unittest
import urllib.error
import zipfile
from contextlib import redirect_stdout
from unittest import mock

# Policy tests assert the strict SSRF path; production callers default to auto.
os.environ.setdefault("SOURCEFIDELITY_FETCH_MODE", "strict")

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                      "sourcefidelity-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW)

import acquisition_list as al  # noqa: E402
import audit_tasks as at  # noqa: E402
import authorship_signals as ats  # noqa: E402
import batch_paths as bp  # noqa: E402
import batch_report as br  # noqa: E402
import detect_structure as ds  # noqa: E402
import docx_text as dt  # noqa: E402
import source_scope as scope  # noqa: E402
import kind_coverage as kc  # noqa: E402
import enrich_registry as er  # noqa: E402
import evidence_select as ev  # noqa: E402
import evidence_window as ew  # noqa: E402
import fetch_web_sources as fw  # noqa: E402
import file_digest as fd  # noqa: E402
import finding_counts as fc  # noqa: E402
import extract_candidates as ec  # noqa: E402
import academic_checks as ac  # noqa: E402
import benchmark_retrieval as bm  # noqa: E402
import make_task as mt  # noqa: E402
import prepare_html as ph  # noqa: E402
import prepare_source as ps  # noqa: E402
import prepare_text as pt  # noqa: E402
import page_map  # noqa: E402
import pdf_quality as pq  # noqa: E402
import paper_index as pi  # noqa: E402
import reappraise_apply as ra  # noqa: E402
import redact_identity as ri  # noqa: E402
import reappraise_prepare as rp  # noqa: E402
import record_metrics as rec  # noqa: E402
import render_review as rr  # noqa: E402
import resolve_unsupplied as ru  # noqa: E402
import review_metrics as rm  # noqa: E402
import link_states as ls  # noqa: E402
import refresh_pricing as pricing_refresh  # noqa: E402
import safe_fetch as sf  # noqa: E402
import source_registry as sr  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sourcefidelity-shared"))
from sourcefidelity_core import acquired as core_acquired  # noqa: E402
from sourcefidelity_core import assessment as core_assessment  # noqa: E402
from sourcefidelity_core import understanding as core_understanding  # noqa: E402
import stage_receipt as stg  # noqa: E402
import validate_inventory as vi  # noqa: E402
import web_cache  # noqa: E402
import fetch_open_sources as fos  # noqa: E402
from retrieval import pacing  # noqa: E402
from retrieval import providers  # noqa: E402
import which_python as wp  # noqa: E402
import word_count as wc  # noqa: E402
import resolve_dois as rd  # noqa: E402
import hyperlink_audit as ha  # noqa: E402
import http_cache  # noqa: E402
import dossier  # noqa: E402
import sourcepack as sp  # noqa: E402
import one_paper as op  # noqa: E402
import open_text_pages as otp  # noqa: E402
import openfetch as of  # noqa: E402
import fetch as fetch_helper  # noqa: E402
import update_check as uc  # noqa: E402
import user_settings  # noqa: E402


def page(pdf, printed, text):
    return {"pdf_page": pdf, "printed": printed, "text": text}


def marks_for(pages):
    text = ""
    marks = []
    printed = {}
    for p in pages:
        marks.append((len(text), p["pdf_page"], p["printed"]))
        text += (f"=== PDF p{p['pdf_page']} | printed p{p['printed'] or '—'} "
                 f"| native | conf 1.0 ===\n{p['text']}\n")
        printed[p["pdf_page"]] = p["printed"]
    return text, marks, printed


class ParseSourceTests(unittest.TestCase):
    def test_edited_collection_chapter(self):
        parsed = sr.parse_source({"source": (
            "Dray, T. (2011). Media as creative industries. In D. Wexler & "
            "D. Y. Ren (Eds.), The political economy of screen media (pp. 84-100). "
            "Halcyon Academic.")})
        self.assertEqual(parsed["work_kind"], "chapter_edited_collection")
        self.assertEqual(parsed["pages"], [84, 100])
        self.assertEqual(parsed["editors"], "D. Wexler & D. Y. Ren")

    def test_authored_book_chapter(self):
        parsed = sr.parse_source({"source": (
            "Ashby, D., & Merrick, W. (2006). The new media conglomerates. In The "
            "business of media industries (pp. 75-116). Kestrel Press.")})
        self.assertEqual(parsed["work_kind"], "chapter_authored_book")
        self.assertEqual(parsed["pages"], [75, 116])

    def test_press_release_is_web_not_monograph(self):
        parsed = sr.parse_source({"source": (
            "Kestrel Games. (2024, February 2). Reimagined genre-defining RPG Starfall "
            "is available now [Press release].")})
        self.assertEqual(parsed["work_kind"], "web")

    def test_authored_book_chapter_without_in(self):
        parsed = sr.parse_source({"source": (
            "Hollis, H. (2006). Buying into the talent show. Convergent media: "
            "Where old and new platforms collide (pp. 59-92). Meridian University Press.")})
        self.assertEqual(parsed["work_kind"], "chapter_authored_book")
        self.assertEqual(parsed["container_title"],
                         "Convergent media: Where old and new platforms collide")
        self.assertEqual(parsed["pages"], [59, 92])

    def test_journal_with_standalone_volume(self):
        parsed = sr.parse_source({"source": (
            "Denby, R. (2011). Fan subtitling and the unlicensed text. "
            "Screen Culture Quarterly, 6, 447-464.")})
        self.assertEqual(parsed["work_kind"], "article")

    def test_organizational_author_is_not_a_publisher(self):
        parsed = sr.parse_source({"source": (
            "National Film Office. (n.d.). Certification and tax "
            "relief FAQ.")})
        self.assertNotEqual(parsed["work_kind"], "monograph")

    def test_publisher_imprint_without_press(self):
        parsed = sr.parse_source({"source": (
            "Harlow, J. (2010). Cross-media promotion. Halcyon Publisher.")})
        self.assertEqual(parsed["work_kind"], "monograph")

    def test_article_with_pp_is_not_a_chapter(self):
        parsed = sr.parse_source({"source": (
            "Smith, J. (2020). An article. Journal of Testing, 53(3), "
            "pp. 325-336.")})
        self.assertEqual(parsed["work_kind"], "article")

    def test_article(self):
        parsed = sr.parse_source({"source": (
            "Eldridge, P. (2011). Explaining the rise of trade "
            "protectionism. Journal of Global Trade Studies, 53(3), "
            "325-336.")})
        self.assertEqual(parsed["work_kind"], "article")
        self.assertEqual(parsed["year"], "2011")

    def test_web_url_is_extracted_from_entry(self):
        parsed = sr.parse_source({"source": (
            "Mercer, H. (2019, February 7). How a superhero film almost "
            "destroyed a studio. Screen Trade Daily. "
            "https://www.example.com/movies/a26232993/superhero-film-studio/ ")})
        self.assertEqual(parsed["work_kind"], "web")
        self.assertEqual(parsed["url"],
                         "https://www.example.com/movies/a26232993/"
                         "superhero-film-studio/")

    def test_web_url_drops_sentence_punctuation(self):
        parsed = sr.parse_source({"source": (
            "Example. (2024). A web page. https://example.org/page).")})
        self.assertEqual(parsed["url"], "https://example.org/page")


class CandidateTests(unittest.TestCase):
    def test_nd_citation_is_found(self):
        text = ("Founded in 1986 ... distribution rights "
                "(Northlight Animation Studios, n.d.-b).")
        found = [m.group(0) for m in ec.ND_RE.finditer(text)]
        self.assertEqual(found, ["(Northlight Animation Studios, n.d.-b)"])


class SourceObjectTests(unittest.TestCase):
    def test_short_container_substring_does_not_link_another_book(self):
        registry = {
            "corbin": {"work_kind": "chapter_edited_collection",
                       "container_title": "A companion to fan cultures",
                       "form_issue": "chapter cited instead of the authored book",
                       "identity": {"author": "Corbin, E", "year": "2014",
                                    "title": "Toy collecting and adult lives"}},
            "hale": {"work_kind": "monograph",
                      "identity": {"author": "Hale, M", "year": "2002",
                                   "title": "Fan Worlds"}},
        }
        sr.resolve_chapters(registry)
        self.assertNotEqual(registry["corbin"].get("source_object_key"), "hale")
        self.assertEqual(registry["corbin"].get("form_issue"), "")

    def test_full_container_title_still_links(self):
        registry = {
            "dray": {"work_kind": "chapter_edited_collection",
                     "container_title": "The political economy of screen media",
                     "identity": {"author": "Dray, T", "year": "2011",
                                  "title": "Media as creative industries"}},
            "book": {"work_kind": "monograph",
                     "identity": {"author": "Wexler, D", "year": "2011",
                                  "title": "The political economy of screen media: "
                                           "The transformation of the global media industries"}},
        }
        sr.resolve_chapters(registry)
        self.assertEqual(registry["dray"].get("source_object_key"), "book")


class ScopeTests(unittest.TestCase):
    def rows(self, pages):
        return [{"printed_folio": str(p), "words": "100"} for p in pages]

    def test_adequate_scope(self):
        result = sr.assess_scope([75, 116], self.rows(range(75, 117)), 0.9)
        self.assertEqual(result["status"], "adequate")

    def test_partial_scope_is_unresolved(self):
        result = sr.assess_scope([75, 116], self.rows(range(75, 81)), 0.9)
        self.assertEqual(result["status"], "unresolved")

    def test_pages_outside_the_span_do_not_establish_absence(self):
        result = sr.assess_scope([41, 65], self.rows(range(117, 140)), 0.9)
        self.assertEqual(result["status"], "unresolved")
        self.assertIn("missing", result["reason"])

    def test_batch_attaches_copy_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "_sources", "R1")
            os.makedirs(source)
            with open(os.path.join(source, "pages.csv"), "w", encoding="utf-8") as handle:
                handle.write("pdf_page,printed_folio,source,confidence,chars,words\n")
                for page in range(57, 83):
                    handle.write(f"{page},{page},native,1.0,10,2\n")
            text = os.path.join(source, "text.txt")
            with open(text, "w", encoding="utf-8") as handle:
                handle.write("text\n")
            book = os.path.join(tmp, "book.pdf")
            with open(book, "wb") as handle:
                handle.write(b"%PDF-1.4")
            registry = {"R1": {"page_range": [57, 82], "extraction": text,
                               "supplied_file": book,
                               "work_kind": "chapter_edited_collection",
                               "identity_status": "supplied_confirmed",
                               "verified_by": "supplied copy: first-page title tokens",
                               "match": {"author": True, "year": True,
                                         "title_coverage": 1.0, "chapter_coverage": None,
                                         "later_edition": False}}}
            sr.apply_scope(registry, tmp)
        self.assertEqual(registry["R1"]["scope"]["status"], "adequate")
        self.assertTrue(registry["R1"]["copy_observation"]["sha256"])
        self.assertIn("chapter membership not checked",
                      registry["R1"]["copy_observation"]["unknowns"])

    def test_copy_observation_records_unknowns_and_scope(self):
        record = {"supplied_file": "/tmp/book.pdf", "work_kind": "chapter_edited_collection",
                  "verified_by": "supplied copy: first-page title tokens",
                  "identity_status": "supplied_confirmed",
                  "match": {"author": True, "year": False, "title_coverage": 1.0,
                            "chapter_coverage": None, "later_edition": False},
                  "scope": {"status": "unresolved", "reason": "no printed page evidence"}}
        observation = sr.copy_observation(record, "abc")
        self.assertEqual(observation["sha256"], "abc")
        self.assertIn("copy year not corroborated", observation["unknowns"])
        self.assertIn("chapter membership not checked", observation["unknowns"])
        self.assertEqual(observation["scope"]["status"], "unresolved")

    def test_unreliable_map_without_pages_is_unresolved(self):
        result = sr.assess_scope([41, 65], [], 0.125)
        self.assertEqual(result["status"], "unresolved")

    def test_no_range_is_not_required(self):
        self.assertEqual(sr.assess_scope([], [], None)["status"], "not_required")


class StructureTests(unittest.TestCase):
    def test_edited_collection_front_matter(self):
        pages = [page(i, "", "Prelims") for i in range(1, 5)]
        pages.append(page(5, "", "Copyright in the collection and in any "
                               "introductory materials (c) Wexler and Ren 2011. "
                               "Copyright in the individual chapters (c) the Contributors."))
        result = ds.front_matter(pages)
        self.assertTrue(result["edited_collection"])
        self.assertIn("copyright in the collection", result["marker"])

    def test_acknowledgments_edited_by_is_not_a_collection(self):
        pages = [page(i, "", "The author thanks the series editors.") for i in range(1, 8)]
        pages.append(page(8, "", '"Will the Real Wolverine Please Stand Up?" '
                                'from Film and Comics, edited by Jordan Marlow.'))
        self.assertFalse(ds.front_matter(pages)["edited_collection"])

    def test_repeated_same_editor_line_is_one_collection(self):
        pages = [page(1, "", "The Companion to Globalization"),
                 page(2, "", "Edited by Peter Voss"),
                 page(3, "", "The Companion to Globalization\n"
                             "Edited by Peter Voss\n© 2007 by Halcyon Publishing")]
        for i in range(4, 7):
            pages.append(page(i, "", "Contents and preface"))
        self.assertTrue(ds.front_matter(pages)["edited_collection"])

    def test_series_page_with_many_editor_lines_is_not_a_collection(self):
        pages = [page(i, "", "Critical Media Studies") for i in range(1, 4)]
        pages.append(page(4, "", "\n".join([
            "Edited by John Rowe and Peter Sand",
            "Edited by Miles Garner and Greg Ellery",
            "Edited by Arun P. Kavon and Todd Frayne"])))
        for i in range(5, 7):
            pages.append(page(i, "", "The Film Studio\nBen Gable and Tom O'Rourke"))
        self.assertFalse(ds.front_matter(pages)["edited_collection"])

    def test_chapter_bounds_from_outline(self):
        structure = {
            "outline": [
                {"title": "3 Media as Creative Industries", "pdf_page": 107, "level": 1},
                {"title": "Introduction", "pdf_page": 107, "level": 2},
                {"title": "4 The Structure of Communications Business", "pdf_page": 124, "level": 1},
            ],
            "printed": {"107": "84", "123": "100", "124": "101"},
            "page_count": 200,
            "toc": [],
        }
        bounds = ds.chapter_bounds(structure, "Media as Creative Industries")
        self.assertEqual(bounds["start"], "84")
        self.assertEqual(bounds["end"], "100")
        self.assertEqual(bounds["source"], "outline")

    def test_chapter_bounds_body_heading(self):
        pages = [
            page(1, "", "Contents\n3. The New Media Giants 75\n4. Strategies 117"),
            page(2, "75", "3\nThe New\nMedia Giants\n" +
                 "This chapter examines ownership and control. " * 8),
            page(3, "76", "The New Media Giants\n" +
                 "More text about the industry and its structure. " * 8),
            page(4, "116", "The New Media Giants\n" +
                 "The chapter closes with a summary. " * 8),
            page(5, "117", "4\nStrategies of the\nNew Media Giants\n" +
                 "The next chapter begins here. " * 8),
        ]
        structure = {"outline": [], "printed": {p["pdf_page"]: p["printed"] for p in pages},
                     "page_count": 4, "toc": [
                         {"number": 3, "title": "The New Media Giants", "pdf_page": 1},
                         {"number": 4, "title": "Strategies of the New Media Giants", "pdf_page": 1}]}
        bounds = ds.chapter_bounds(structure, "The New Media Giants", pages=pages)
        self.assertEqual((bounds["start"], bounds["end"]), ("75", "116"))
        self.assertEqual(bounds["source"], "body_heading")


class PageMapTests(unittest.TestCase):
    def test_running_head_trailing_folio(self):
        self.assertEqual(page_map.folio_from_lines(
            ["1 Introduction: Hallyu as an Alternative Aestheticized… 25"]),
            ("25", "arabic", 25))

    def test_chapter_opening_folio_before_copyright_symbol(self):
        lines = ["37© The Author(s), under exclusive license … 2021",
                 "V. Calder, S. Osei, The Sociology of Transnational Pop Culture,",
                 "https://doi.org/10.1007/978-3-030-84296-3_2", "2",
                 "Is Entertainment Capitalism the Ultimate Stage…"]
        self.assertEqual(page_map.folio_from_lines(lines), ("37", "arabic", 37))

    def test_chapter_opening_folio_below_copyright_line(self):
        lines = ["1© The Author(s), under exclusive license … 2021",
                 "V. Calder, S. Osei, The Sociology of Transnational Pop Culture,",
                 "https://doi.org/10.1007/978-3-030-84296-3_1", "1",
                 "Introduction: Hallyu as an Alternative Aestheticized…"]
        self.assertEqual(page_map.folio_from_lines(lines), ("1", "arabic", 1))

    def test_even_page_head_leading_folio(self):
        self.assertEqual(page_map.folio_from_lines(
            ["24 V. Calder and S. Osei"]),
            ("24", "arabic", 24))


class MatchRefsTests(unittest.TestCase):
    def test_close_surname_still_links_reference(self):
        refs = [{"id": "R1", "verbatim": (
            "Ashby, D., & Merrick, W. (2006). The business of media industries. "
            "Kestrel Press.")}]
        citation = {"marker": "(Ashby and Merric, 2006)",
                    "sentence": "The market model treats audiences as consumers.",
                    "year": "2006"}
        self.assertIn("R1", mt.match_refs(citation, refs))


class WindowTests(unittest.TestCase):
    def test_page_range_is_enforced(self):
        pages = [
            page(1, "1", "Creative industries appear in the introduction."),
            page(2, "84", "This chapter argues that creative industries and "
                          "conglomeration are accumulation strategies."),
            page(3, "101", "A later chapter mentions creative industries again."),
        ]
        text, marks, printed = marks_for(pages)
        hits = mt.windows(text, marks, printed, ["creative industries conglomeration"],
                          80, [84, 100])
        self.assertTrue(hits)
        self.assertTrue(all(h["printed_page"] == "84" for h in hits))

    def test_title_terms_are_distinctive(self):
        phrases = mt.title_phrases("Media as Creative Industries: Conglomeration")
        self.assertIn("creative industries conglomeration", phrases)
        self.assertTrue(all(len(p.split()) >= 2 for p in phrases))

    def test_single_distinctive_title_token(self):
        self.assertEqual(mt.title_phrases("Neoliberalism and the media"),
                         ["neoliberalism"])

    def test_no_window_is_empty_not_an_exception(self):
        text, marks, printed = marks_for([page(1, "84", "Nothing relevant here.")])
        self.assertEqual(mt.windows(text, marks, printed, ["unfindable phrase"],
                                    80, [84, 100]), [])


class MatchPlausibilityTests(unittest.TestCase):
    def result(self, **provider):
        base = {"type": "journal-article", "title": "A paper", "year": "2024",
                "editors": [], "authors": ["Smith"], "doi": ""}
        base.update(provider)
        return {"query": "bibliographic", "provider": base, "score": 0.9,
                "detail": {"title_coverage": 0.95, "author": True}}

    def record(self, kind="article", year="2024"):
        return {"work_kind": kind,
                "identity": {"author": "Smith, J", "year": year,
                             "title": "A paper about media"}}

    def test_accepts_consistent_article(self):
        ok, _ = ru.plausible(self.record(), self.result())
        self.assertTrue(ok)

    def test_rejects_press_release_book(self):
        result = self.result(type="monograph", title="My summer in a garden",
                             year="1895", authors=["Warner"])
        result["score"] = 0.55
        ok, why = ru.plausible(self.record("monograph", "2026"), result)
        self.assertFalse(ok)
        self.assertIn("score", " ".join(why["reasons"]))

    def test_rejects_wrong_work_type(self):
        result = self.result(type="journal-article", title="Mass communications",
                             year="1970", authors=["Maz"])
        ok, why = ru.plausible(self.record("monograph", "1992"), result)
        self.assertFalse(ok)
        self.assertIn("type", " ".join(why["reasons"]))

    def test_rejects_later_edition(self):
        result = self.result(type="book-chapter", title="Convergent Media",
                             year="2017", authors=["Hollis"])
        ok, why = ru.plausible(self.record("monograph", "2008"), result)
        self.assertFalse(ok)

    def test_doi_query_is_not_downgraded_here(self):
        result = self.result()
        result["query"] = "doi"
        ok, _ = ru.plausible(self.record(), result)
        self.assertTrue(ok)


class BatchSearchOutcomeTests(unittest.TestCase):
    def record(self, title="Independent film archives", doi=""):
        return {"identity_status": "unresolved", "work_kind": "article",
                "identity": {"title": title, "author": "Smith, J.",
                             "year": "2024", "doi": doi}}

    def test_legacy_failure_and_new_failure_do_not_prevent_retry(self):
        record = self.record()
        cache = {"independent film archives|smith": {
            "reason": "rate_limited", "_timestamp": time.time()}}
        with mock.patch.object(er, "fetch", side_effect=[
                RuntimeError("HTTP 429: provider busy"), {"message": {"items": []}}]) as fetch:
            failed = ru.lookup(record, "", cache)
            completed = ru.lookup(record, "", cache)
            replay = ru.lookup(record, "", cache)
        self.assertEqual(failed["outcome"], "incomplete")
        self.assertEqual(failed["attempts"], [{
            "provider": "crossref", "query": "bibliographic",
            "outcome": "provider_failure", "reason": "RuntimeError", "http_status": 429}])
        self.assertEqual(completed["outcome"], "no_results")
        self.assertTrue(replay["_cached"])
        self.assertEqual(fetch.call_count, 2)

    def test_malformed_refresh_invalidates_previous_completed_result(self):
        record, cache = self.record(), {}
        with mock.patch.object(er, "fetch", side_effect=[
                {"message": {"items": []}}, {"message": {}}, {"message": {"items": []}}]) as fetch:
            ru.lookup(record, "", cache)
            failed = ru.lookup(record, "", cache, refresh=True)
            retried = ru.lookup(record, "", cache)
        self.assertEqual(failed["outcome"], "incomplete")
        self.assertFalse(retried["_cached"])
        self.assertEqual(fetch.call_count, 3)

    def test_rejected_candidates_are_distinct_from_an_empty_response(self):
        record, cache = self.record(), {}
        data = {"message": {"items": [{
            "title": ["Research about bees"], "type": "journal-article",
            "author": [{"family": "Other"}], "DOI": "10.1000/bees"}]}}
        with mock.patch.object(er, "fetch", return_value=data):
            result = ru.lookup(record, "", cache)
        self.assertEqual(result["outcome"], "candidates_rejected")
        self.assertEqual(result["attempts"][0]["candidate_count"], 1)
        self.assertIsNone(result["provider"])

    def test_handle_failure_is_not_an_unregistered_doi(self):
        record = self.record(doi="10.1000/example")
        missing = RuntimeError("HTTP 404: absent")
        cache = {}
        with mock.patch.object(er, "fetch", side_effect=[
                missing, missing, RuntimeError("HTTP 503: failed"),
                missing, missing, {"responseCode": 100}]) as fetch:
            failed = ru.lookup(record, "", cache)
            absent = ru.lookup(record, "", cache)
            replay = ru.lookup(record, "", cache)
        self.assertEqual([(a["provider"], a["outcome"]) for a in failed["attempts"]],
                         [("crossref", "no_results"), ("datacite", "no_results"),
                          ("doi_handle", "provider_failure")])
        self.assertEqual(absent["outcome"], "doi_not_found")
        self.assertEqual(replay["attempts"][-1]["outcome"], "no_results")
        self.assertTrue(replay["_cached"])
        self.assertEqual(fetch.call_count, 6)

    def test_doi_conflict_is_recorded_without_installing_the_provider(self):
        bad = self.record(doi="10.1000/same")
        good = self.record("Tropical ornamental horticulture", doi="10.1000/same")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source_registry.json"
            path.write_text(json.dumps({"works": {"bad": bad, "good": good}}), encoding="utf-8")
            data = {"message": {"title": ["Tropical ornamental horticulture"],
                                "DOI": "10.1000/same", "type": "journal-article",
                                "author": [{"family": "Other"}]}}
            with mock.patch.object(sys, "argv", ["resolve_unsupplied.py", "--batch", tmp]), \
                    mock.patch.object(er, "fetch", return_value=data) as fetch, \
                    redirect_stdout(io.StringIO()):
                ru.main()
            works = json.loads(path.read_text(encoding="utf-8"))["works"]
        self.assertNotIn("provider", works["bad"])
        self.assertEqual(works["bad"]["search"]["outcome"], "doi_mismatch")
        self.assertEqual(works["good"]["search"]["outcome"], "matched")
        self.assertEqual(fetch.call_count, 2)

    def test_query_limit_records_skips_and_does_not_charge_cache_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source_registry.json"
            works = {str(i): self.record(f"Archives collection {i}") for i in range(3)}
            path.write_text(json.dumps({"works": works}), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["resolve_unsupplied.py", "--batch", tmp,
                                                 "--limit", "1"]), \
                    mock.patch.object(er, "fetch", return_value={"message": {"items": []}}) as fetch, \
                    redirect_stdout(io.StringIO()):
                ru.main()
                first = json.loads(path.read_text(encoding="utf-8"))["works"]
                ru.main()
            second = json.loads(path.read_text(encoding="utf-8"))["works"]
        self.assertEqual(sum(r["search"]["outcome"] == "skipped" for r in first.values()), 2)
        self.assertEqual(sum(r["search"]["outcome"] == "no_results" for r in second.values()), 2)
        self.assertEqual(sum(r["search"]["cached"] for r in second.values()), 1)
        self.assertEqual(fetch.call_count, 2)


class DataCiteTests(unittest.TestCase):
    def test_text_with_journal_citeproc_is_article(self):
        fields = ru.datacite_fields({
            "doi": "10.6092/example",
            "titles": [{"title": "Franchising Jurassic Park: Introduction"}],
            "creators": [{"familyName": "Marlowe"}],
            "publicationYear": 2023,
            "publisher": "Screen Culture Review",
            "types": {"resourceTypeGeneral": "Text", "citeproc": "article-journal"},
            "container": {"title": "Screen Culture Review"},
            "page": None,
        })
        self.assertEqual(fields["type"], "journal-article")
        self.assertEqual(fields["year"], "2023")
        self.assertEqual(fields["authors"], ["Marlowe"])


class FileMatchTests(unittest.TestCase):
    def test_doi_in_file_confirms(self):
        record = {"identity": {"doi": "10.5555/jib.2011.042", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Eldridge, P. (2011). Explaining the rise of trade "
            "protectionism. Journal of Global Trade Studies, 53(3), 325-336.")})
        score, detail = sr.score_file(
            "Explaining the rise of trade protectionism\n"
            "doi:10.5555/jib.2011.042\n(c) 2011 Meridian", record, parsed)
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(detail["doi"])

    def test_later_edition_is_rejected(self):
        record = {"identity": {"doi": "", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Sutter, M. (2003). Globalization: A short introduction. "
            "Meridian Press.")})
        score, detail = sr.score_file(
            "Globalization: A short introduction\n"
            "© Marta B. Sutter 2013\nFirst Edition published in 2003", record, parsed)
        self.assertEqual(score, 0.0)
        self.assertTrue(detail["later_edition"])


    def test_chapter_author_matches_container_front_matter(self):
        parsed = sr.parse_source({"source": (
            "Gable, B., & O'Rourke, T. (2005). The policy environment of the "
            "contemporary film studio. In G. Ellery & M. Garner (Eds.), "
            "Contracting out the studio system (pp. 41-65). Rowan & Field.")})
        match = dict(parsed)
        match["title"] = parsed["container_title"]
        match["author"] = parsed["editors"]
        record = {"work_kind": "chapter_edited_collection",
                  "identity": {"doi": "", "isbn": "", "author": parsed["author"]}}
        score, detail = sr.score_file(
            "Contracting out the studio system (2005)\nGable and O'Rourke\n"
            "The policy environment of the contemporary film studio",
            record, match)
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(detail["chapter_author"])

    def test_other_chapter_offprint_is_rejected(self):
        parsed = sr.parse_source({"source": (
            "Tarrant, D. K. (2007). Mapping global media flow. In D. K. Tarrant "
            "(Ed.), Media in motion (pp. 11-20). Alder House.")})
        match = dict(parsed)
        match["title"] = parsed["container_title"]
        match["author"] = parsed["editors"]
        match["chapter_title"] = parsed["title"]
        record = {"work_kind": "chapter_edited_collection",
                  "identity": {"doi": "", "isbn": "", "author": parsed["author"]}}
        score, detail = sr.score_file(
            "Media in Motion\nRyu, Y. The rising East Asian wave\n"
            "Korean media go global", record, match)
        self.assertEqual(score, 0.0)
        self.assertLess(detail["chapter_coverage"], 0.5)

    def test_short_surname_does_not_match_inside_a_word(self):
        record = {"identity": {"doi": "", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Ng, J. (2018). Northlight Animation shutdown. Screen Trade Weekly.")})
        score, detail = sr.score_file(
            "Northlight Animation shutdown", record, parsed,
            path="/tmp/Sample - understanding animation.pdf")
        self.assertFalse(detail["filename_author"])

    def test_filename_author_and_year_corroborate_title(self):
        record = {"identity": {"doi": "", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Sutter, M. B. (2003). Globalization: A short introduction. "
            "Meridian Press.")})
        score, detail = sr.score_file(
            "Globalization: A short introduction\nSHORT INTRODUCTIONS",
            record, parsed,
            path="/tmp/Globalization -- Marta B Sutter, 1961- -- 2003.pdf")
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(detail["filename_author"])
        self.assertTrue(detail["filename_year"])

    def test_later_edition_in_filename_is_rejected(self):
        record = {"identity": {"doi": "", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Ashby, D., & Merrick, W. (2001). The business of media industries. "
            "Kestrel Press.")})
        score, detail = sr.score_file(
            "The business of media industries: Corporate media and the public interest\n"
            "Ashby, David\nMerrick, William", record, parsed,
            path="/tmp/The business of media industries -- 2nd ed_ -- 2006.pdf")
        self.assertEqual(score, 0.0)
        self.assertTrue(detail["later_edition"])
        self.assertGreaterEqual(detail["base_score"], 0.65)
        self.assertEqual(detail["filename_years"], [2006])


class RobustnessTests(unittest.TestCase):
    def test_summary_files_accepts_both_layouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("P1", "paper-P2"):
                os.makedirs(os.path.join(tmp, name))
                json.dump({"paper_id": name}, open(
                    os.path.join(tmp, name, "summary.json"), "w"))
            found = br.summary_files(pathlib.Path(tmp))
            self.assertEqual([p.parent.name for p in found], ["P1", "paper-P2"])

    def test_audit_tasks_ids_before_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "_work", "P1")
            os.makedirs(work)
            json.dump({"citations": []}, open(
                os.path.join(work, "candidates.json"), "w"))
            self.assertEqual(at.paper_ids(tmp), ["P1"])

    def test_as_records_accepts_strings_and_partial_dicts(self):
        records = rr.as_records(
            ["R1: no text", {"source_key": "R2", "reason": "no text"}],
            "unverifiable")
        self.assertEqual(records[0]["source"], "R1")
        self.assertEqual(records[0]["search"], "no text")
        self.assertEqual(records[1]["source"], "R2")
        self.assertEqual(records[1]["search"], "no text")


class ReappraiseWindowTests(unittest.TestCase):
    def test_title_terms_are_used_when_sentence_does_not_match(self):
        text = "Neoliberalism shapes the structure of media markets."
        cache = (text, [(0, 1, "10")], {1: "10"})
        hits = rp.window_hits(
            cache, {"sentence": "Berry's account explains this structure",
                    "marker": "(2019)"},
            {"title": "Neoliberalism and the media"}, "", None)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["fallback"], "title_terms")


    def test_render_md_inserts_before_summary(self):
        paper = {"paper_id": "P1", "path": "/tmp/a.docx",
                 "word_counts": {"total": 1, "text": 1, "references": 0}}
        findings = {"title": "T", "date": "2026-11-17", "status": "complete",
                    "summary": {}, "references": [], "works": [], "uncited": []}
        metrics = {"runs": [{
            "phase": "review", "model": "deepseek-v4-flash",
            "duration_ms": 1000,
            "usage": {"input_tokens": 1000, "cache_read_tokens": 900,
                      "output_tokens": 100, "total_tokens": 1100},
            "started_at": "2026-11-17T02:00:00Z",
            "completed_at": "2026-11-17T02:00:01Z",
            "cost_status": "estimated", "estimated_cost_usd": 0.0001554}]}
        text = rr.render_md(paper, findings, rr.counts(findings), {}, metrics)
        self.assertIn("Review time: 1s", text)
        self.assertLess(text.index("Review time:"),
                        text.index("## Summary of academic-integrity findings"))

    def test_render_summary_includes_metrics(self):
        paper = {"paper_id": "P1", "path": "/tmp/a.docx",
                 "word_counts": {"total": 1, "text": 1, "references": 0}}
        findings = {"references": [], "occurrences": [], "works": [],
                    "uncited": [], "summary": {}}
        metrics = {"runs": [{"phase": "review"}]}
        summary = rr.render_summary(paper, findings, rr.counts(findings), {},
                                    {}, "/tmp", metrics)
        self.assertEqual(summary["review_metrics"], metrics)


    def test_metrics_block_does_not_parse_as_a_citation(self):
        text = ("# Review\n\n<!-- review-metrics:start -->\n"
                "**Review time:** 1s\n<!-- review-metrics:end -->\n\n"
                "### C1 — ¶1\n**Overall judgment: Supports.**\n")
        self.assertEqual(set(rp.parse_review(text)), {"C1"})


    def test_failed_metadata_keeps_duration_and_marks_tokens_missing(self):
        metadata = {"status": "failed",
                    "createdAt": "2026-11-15T15:01:08Z",
                    "completedAt": "2026-11-15T15:16:06Z"}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "metadata.json")
            json.dump(metadata, open(path, "w", encoding="utf-8"))
            run = rec.from_metadata(path, "deepseek-v4-flash")
        self.assertIsNone(run["usage"])
        self.assertAlmostEqual(run["duration_ms"], 898000, delta=5)


class RenderValidationTests(unittest.TestCase):
    def _occ(self, oid, **extra):
        item = {"id": oid, "judgment": "Not Judged", "paragraph_id": "p1",
                "marker": "(Doe, 2020)", "statement": "Claim"}
        item.update(extra)
        return item

    def test_validate_rejects_duplicate_ids(self):
        findings = {"occurrences": [self._occ("C1"), self._occ("C1")],
                    "references": []}
        with self.assertRaises(SystemExit):
            rr.validate(findings)

    def test_validate_rejects_an_unresolved_cited_reference(self):
        findings = {"occurrences": [self._occ("C1", cited_r_ids=["R9"])],
                    "references": []}
        with self.assertRaises(SystemExit):
            rr.validate(findings)

    def test_validate_rejects_empty_findings_without_explicit_state(self):
        with self.assertRaises(SystemExit):
            rr.validate({})
        rr.validate({"inventory_empty": True, "status": "complete"})

    def test_rejects_contributions_for_unknown_or_uncited_references(self):
        refs = [{"id": "R1", "source_key": "one", "verbatim": "First"},
                {"id": "R2", "source_key": "two", "verbatim": "Second"}]
        for rid, key, message in [("R9", "unknown", "not in the findings"),
                                  ("R2", "two", "is not cited here"),
                                  ("R1", "two", "source key differs")]:
            with self.subTest(rid=rid, key=key):
                occ = self._occ("C1", cited_r_ids=["R1"], sources=[
                    {"r_id": rid, "source_key": key}])
                with self.assertRaisesRegex(SystemExit, message):
                    rr.validate({"references": refs, "occurrences": [occ]})

    def test_window_validation_binds_reference_occurrence_and_excerpt(self):
        task = {"candidates": {"references": [{"id": "R2", "verbatim": "Second"}],
                               "citations": [{"id": "X1", "paragraph_id": "P1",
                                              "marker": "(Doe, 2020)"}]},
                "windows": {"X1": [{"r_id": "R2", "source_key": "two",
                                    "window_id": "X1:R2:1", "text": "Observed text."}]}}
        # Final inventory IDs can change during adjudication.
        refs = [{"id": "R3", "source_key": "two", "verbatim": "Second"}]
        inventory = {"occurrences": [{"id": "C1", "paragraph_id": "P1",
                                      "marker": "(Doe, 2020)", "cited_r_ids": ["R3"]}]}
        src = {"r_id": "R3", "source_key": "two", "window_id": "X1:R2:1",
               "evidence": "Observed text.", "locator": "printed p. 1"}
        occ = self._occ("C1", judgment="Supports", cited_r_ids=["R3"], sources=[src])
        findings = {"references": refs, "occurrences": [occ]}
        rr.validate(findings, inventory, task)
        src["evidence"] = "Unrelated text."
        with self.assertRaisesRegex(SystemExit, "evidence is not in window"):
            rr.validate(findings, inventory, task)
        src["evidence"] = "Observed text."
        refs[0]["verbatim"] = "Different work"
        with self.assertRaisesRegex(SystemExit, "does not match"):
            rr.validate(findings, inventory, task)
        occ["cited_r_ids"] = []
        with self.assertRaisesRegex(SystemExit, "differ from the validated inventory"):
            rr.validate(findings, inventory, task)

    def test_reconcile_rejects_a_dropped_inventory_item(self):
        inventory = {"occurrences": [{"id": "C1"}, {"id": "C2"}], "references": []}
        findings = {"occurrences": [{"id": "C1"}], "references": []}
        with self.assertRaises(SystemExit):
            rr.reconcile(findings, inventory)

    def test_reconcile_rejects_an_invented_id(self):
        inventory = {"occurrences": [], "references": []}
        findings = {"occurrences": [{"id": "C7"}], "references": []}
        with self.assertRaises(SystemExit):
            rr.reconcile(findings, inventory)

    def test_reconcile_accepts_matching_id_sets(self):
        inventory = {"occurrences": [{"id": "C1"}], "references": [{"id": "R1"}]}
        findings = {"occurrences": [{"id": "C1"}], "references": [{"id": "R1"}]}
        rr.reconcile(findings, inventory)


class FormattingRemovalTests(unittest.TestCase):
    def _paper(self):
        return {"paper_id": "P1", "path": "/tmp/a.docx",
                "word_counts": {"total": 5, "text": 5, "references": 0}}

    def test_formatting_notes_no_longer_render_or_export(self):
        findings = {"title": "T", "date": "2026-11-18", "status": "complete",
                    "summary": {"formatting_notes": ["hyphen instead of en dash"]},
                    "occurrences": [], "references": [], "works": [], "uncited": [],
                    "inventory_empty": True}
        text = rr.render_md(self._paper(), findings, rr.counts(findings), {})
        summary = rr.render_summary(self._paper(), findings, rr.counts(findings),
                                    {}, {}, "/tmp")
        self.assertNotIn("Formatting notes", text)
        self.assertNotIn("formatting_notes", summary)


class ReportModeTests(unittest.TestCase):
    def _paper(self):
        return {"paper_id": "P1", "path": "/tmp/a.docx",
                "word_counts": {"total": 5, "text": 5, "references": 0}}

    def _findings(self):
        return {"title": "T", "date": "2026-11-18", "status": "complete",
                "summary": {}, "occurrences": [], "references": [], "works": [],
                "uncited": [{"id": "U1", "locator": "\u00b63",
                             "statement": "Uncited claim about media.",
                             "source_status": "no source", "search": "searched",
                             "type": "uncited claim"}],
                "audit": {"checks": "Structural audit detail."},
                "limitations": "Access limited to one chapter."}

    def test_short_report_keeps_a_scope_block_and_omits_the_audit(self):
        text = rr.render_md(self._paper(), self._findings(),
                            rr.counts(self._findings()), {})
        self.assertIn("## Review scope and counts", text)
        self.assertIn("Word counts: total 5", text)
        self.assertIn("\nUncited substantive passages:", text)
        self.assertNotIn("The paper was missing", text)
        for absent in ("## 1. Findings", "## 5. Unresolved",
                       "Structural audit detail."):
            self.assertNotIn(absent, text)

    def test_short_report_keeps_uncited_statements_and_limitations(self):
        text = rr.render_md(self._paper(), self._findings(),
                            rr.counts(self._findings()), {})
        self.assertIn("Uncited claim about media.", text)
        self.assertIn("Access limited to one chapter.", text)

    def test_hyperlink_count_names_the_reasons(self):
        findings = self._findings()
        findings["summary"] = {"hyperlinks_missing": [
            "Smith 2020: no link", {"work": "Jones 2021", "reason": "broken"},
            "Lee 2019: preview only"]}
        text = rr.render_md(self._paper(), findings, rr.counts(findings), {})
        self.assertIn("Hyperlinks missing: 3 (no link 1; broken 1; preview only 1).", text)

    def test_scope_block_uses_classified_link_states(self):
        findings = self._findings()
        findings["summary"] = {"hyperlinks": {
            "absent": 0, "non_linkable": 4, "non_permanent": 10, "record_only": 0,
            "full_text": 11, "unreachable": 11, "blocked": 0, "dead": 0,
            "verified": 0, "hl_total": 10}}
        text = rr.render_md(self._paper(), findings, rr.counts(findings), {})
        self.assertIn("4 excluded as non-linkable", text)
        self.assertIn("11 full-text", text)
        self.assertIn("HL total: 10 (HL = distinct works", text)
        self.assertNotIn("Hyperlinks missing:", text)

    def test_measured_word_counts_override_the_paper_record(self):
        findings = self._findings()
        findings["word_counts"] = {"total": 10, "text": 8, "references": 2}
        text = rr.render_md(self._paper(), findings, rr.counts(findings), {})
        summary = rr.render_summary(self._paper(), findings, rr.counts(findings),
                                    {}, {}, "/tmp")
        self.assertIn("Word counts: total 10; text 8; references 2.", text)
        self.assertEqual(summary["word_counts"]["references"], 2)

    def test_summary_ends_with_the_counts_line(self):
        findings = self._findings()
        findings["summary"] = {"category_a": ["one", "two"], "category_b": ["three"]}
        text = rr.render_md(self._paper(), findings, rr.counts(findings), {})
        self.assertIn("Counts: Category A 2; Category B 1; uncited substantive passages 1.", text)
        self.assertLess(text.index("Category A academic integrity"),
                        text.index("Counts: Category A 2"))

    def test_scope_block_precedes_the_summary_and_reports_cost(self):
        metrics = {"runs": [{"duration_ms": 65000,
                             "usage": {"input_tokens": 1200, "output_tokens": 300}}]}
        text = rr.render_md(self._paper(), self._findings(),
                            rr.counts(self._findings()), {}, metrics)
        self.assertLess(text.index("## Review scope and counts"),
                        text.index("## Summary of academic-integrity findings"))
        self.assertIn("Review time: 1m 05s; tokens: 1,200 prompt (0 cached + 1,200 uncached) / 300 output; cost: not recorded.", text)

    def test_scope_block_says_not_recorded_without_metrics(self):
        text = rr.render_md(self._paper(), self._findings(),
                            rr.counts(self._findings()), {})
        self.assertIn("Review time: not recorded; tokens: not recorded.", text)

    def _paper_with_refs(self, tmp, body):
        path = os.path.join(tmp, "paper.txt")
        open(path, "w", encoding="utf-8").write(
            body + "\nReferences\nSmith, J. (2020). A work. Journal.\n"
            "Jones, K. (2021). Another work. Journal.\n")
        return path

    def test_narrative_citation_matched_to_a_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = ec.build(self._paper_with_refs(tmp, "Smith (2020) argues a point.\n"), "P1")
        citing = [c for c in data["citations"] if "Smith (2020)" in c["marker"]]
        self.assertTrue(citing, data["citations"])
        self.assertEqual(citing[0]["kind"], "narrative")
        self.assertEqual(citing[0].get("reference_match"), "R1")

    def test_et_al_narrative_matches_on_surname(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = ec.build(self._paper_with_refs(tmp, "Smith et al. (2020) report a result.\n"), "P1")
        citing = [c for c in data["citations"] if "Smith et al." in c["marker"]]
        self.assertTrue(citing, data["citations"])
        self.assertEqual(citing[0].get("reference_match"), "R1")

    def test_unmatched_single_name_is_flagged_not_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = ec.build(self._paper_with_refs(tmp, "Brown (2019) makes a claim.\n"), "P1")
        citing = [c for c in data["citations"] if "Brown (2019)" in c["marker"]]
        self.assertTrue(citing, data["citations"])
        self.assertEqual(citing[0]["confidence"], "low")
        self.assertIn("no_matching_reference", citing[0]["flags"])

    def test_title_like_year_is_a_year_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = ec.build(self._paper_with_refs(
                tmp, "Welcome to Raccoon City (2021) is a film.\n"), "P1")
        markers = [c["marker"] for c in data["citations"]]
        self.assertFalse(any("2021" in m for m in markers), markers)
        self.assertTrue(any("2021" in m["marker"] for m in data["year_mentions"]))

    def test_a_dashless_year_range_is_a_year_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.txt")
            open(path, "w", encoding="utf-8").write("The films (20022016) were released.\n")
            data = ec.build(path, "P1")
        self.assertEqual(data["citations"], [])
        self.assertTrue(any("20022016" in m["marker"] for m in data["year_mentions"]))

    def test_bare_year_parentheticals_are_not_citations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.txt")
            open(path, "w", encoding="utf-8").write(
                "Resident Evil: Welcome to Raccoon City (2021) is a film.\n"
                "Smith (2020) argues a point. Jones supports it (Jones, 2021).\n")
            data = ec.build(path, "P1")
        markers = [c["marker"] for c in data["citations"]]
        self.assertNotIn("(2021)", markers)
        self.assertNotIn("(2020)", markers)
        self.assertIn("(Jones, 2021)", markers)
        mentions = [m["marker"] for m in data["year_mentions"]]
        self.assertTrue(any("2021" in m for m in mentions), mentions)

    def test_full_report_keeps_counts_and_audit(self):
        text = rr.render_md(self._paper(), self._findings(),
                            rr.counts(self._findings()), {})
        self.assertEqual(text.count("Word counts:"), 1)
        self.assertNotIn("## 1. Findings", text)
        self.assertNotIn("## 5. Unresolved", text)


@unittest.skipUnless(os.path.exists(os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                               "install.sh" if os.name != "nt" else "install.ps1")),
                     "installer not shipped with the installed skills")
class InstallerTests(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                          "install.ps1" if os.name == "nt" else "install.sh")
    SKILLS = ("sourcefidelity-review", "sourcefidelity-batch", "sourcefidelity-reappraise")

    def _run(self, *args, env=None):
        environment = {**os.environ, **(env or {})}
        if os.name == "nt":
            powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                      "System32", "WindowsPowerShell", "v1.0",
                                      "powershell.exe")
            command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", self.SCRIPT]
        else:
            command = ["/bin/bash", self.SCRIPT]
        return subprocess.run([*command, *args], capture_output=True,
                              text=True, env=environment)

    def _flags(self, target=None, dry_run=False):
        """The installed platform's spellings for --dry-run and --target."""
        args = []
        if os.name == "nt":
            if dry_run:
                args.append("-DryRun")
            if target:
                args.extend(["-Target", target])
        else:
            if dry_run:
                args.append("--dry-run")
            if target:
                args.extend(["--target", target])
        return args

    def test_dry_run_lists_every_skill_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "skills")
            proc = self._run(*self._flags(target, dry_run=True))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for skill in self.SKILLS:
                self.assertIn(f"would install: {skill}", proc.stdout)
            self.assertFalse(os.path.exists(target))

    def test_a_missing_target_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(env={"HOME": tmp, "USERPROFILE": tmp})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("No harness skills directory found", proc.stderr)

    def test_absent_python_reports_prompt_only_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "skills")
            shim = os.path.join(tmp, "shim-bin")
            os.makedirs(shim)
            if os.name == "nt":
                # An empty PATH hides py.exe and python.exe from the installer.
                environment = {"PATH": shim}
            else:
                for name in ("python3", "python3.12"):
                    path = os.path.join(shim, name)
                    open(path, "w").write("#!/bin/sh\nexit 1\n")
                    os.chmod(path, 0o755)
                environment = {"PATH": shim + os.pathsep + "/usr/bin:/bin"}
            proc = self._run(*self._flags(target, dry_run=True), env=environment)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("prompt-only", proc.stdout)


class WebFetchTests(unittest.TestCase):
    def test_cache_roundtrip_keeps_blocked_pages_as_limitations(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_cache.store(tmp, "https://example.com/a",
                            {"text": "page text", "final_url": "https://example.com/a",
                             "status": 200})
            web_cache.store(tmp, "https://example.com/b",
                            {"text": "", "limitation": "private_host"})
            text, meta = web_cache.text_for(tmp, "https://example.com/a")
            blocked_text, blocked_meta = web_cache.text_for(tmp, "https://example.com/b")
        self.assertEqual(text, "page text")
        self.assertEqual(meta["status"], 200)
        self.assertEqual(blocked_text, "")
        self.assertEqual(blocked_meta["limitation"], "private_host")

    def test_truncated_text_is_kept_out_of_evidence_windows(self):
        url = "https://example.com/article"
        body = "the cited phrase appears in this article " * 80
        with tempfile.TemporaryDirectory() as tmp:
            web_cache.store(tmp, url, {"text": body, "truncated": True})
            hits = mt.web_windows(tmp, [{"r_id": "R1", "url": url}],
                                  ["cited phrase appears"], 120)
            _, meta = web_cache.text_for(tmp, url)
        self.assertEqual(hits, [])
        self.assertEqual(meta["limitation"], "response_limit")
        with mock.patch.object(fos.transport.pacing, "wait"), \
                mock.patch.object(fos.transport.pacing, "observe"), \
                mock.patch.object(sf, "fetch", return_value={"text": body, "truncated": True}):
            attempt = fos.try_text("example", url)
        self.assertEqual(attempt["limitation"], "response_limit")
        self.assertNotIn("text", attempt)

    def test_page_classification_separates_blocks_from_evidence(self):
        body = "Hollywood stars and their images. " * 120
        self.assertEqual(web_cache.classify_text("Subscribe to continue reading. " * 20),
                         "paywalled")
        self.assertEqual(web_cache.classify_text("Borrow this book. " + body),
                         "lending_only")
        self.assertEqual(web_cache.classify_text("Abstract " + "star image " * 60),
                         "abstract_only")
        self.assertEqual(web_cache.classify_text(body), "full_text")
        self.assertEqual(web_cache.classify_text(""), "unreadable")

    def test_a_classified_block_is_not_used_as_a_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "https://example.com/paywalled"
            web_cache.store(tmp, url, {"text": "Subscribe to continue reading. " * 20,
                                       "final_url": url, "status": 200})
            hits = mt.web_windows(tmp, [{"r_id": "R1", "url": url}],
                                  ["subscribe continue reading"], 120)
            _, meta = web_cache.text_for(tmp, url)
        self.assertEqual(hits, [])
        self.assertEqual(meta["classification"], "paywalled")

    def test_fetch_stage_uses_cached_web_text_for_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "https://example.com/article"
            web_cache.store(tmp, url, {"text": "the cited phrase appears in this article " * 80,
                                       "final_url": url, "status": 200})
            hits = mt.web_windows(tmp, [{"r_id": "R1", "url": url}],
                                  ["cited phrase appears"], 120)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["locator"], f"web: {url}")

    def test_fetch_stage_skips_urls_already_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "https://example.com/x"
            web_cache.store(tmp, url, {"text": "cached text", "final_url": url})
            calls = []
            with mock.patch.object(fw.safe_fetch, "fetch",
                                   side_effect=lambda u, **k: calls.append(u)):
                stats = fw.fetch_urls(tmp, {url: "R1"})
        self.assertEqual(calls, [])
        self.assertEqual(stats["cached"], 1)

    def test_fetch_stage_caches_a_blocked_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "https://example.com/blocked"
            with mock.patch.object(fw.safe_fetch, "fetch",
                                   return_value={"text": "", "limitation": "content_type"}):
                stats = fw.fetch_urls(tmp, {url: "R1"})
            _, meta = web_cache.text_for(tmp, url)
        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(meta["limitation"], "content_type")


class CanonicalFieldTests(unittest.TestCase):
    def test_worker_vocabulary_is_normalised_before_validation(self):
        self.assertEqual(rr.canonical_outcome("unverified_redirect"), "unlocated_after_search")
        self.assertEqual(rr.canonical_outcome("confirmed_at_issue_level"), "confirmed")
        self.assertEqual(rr.canonical_outcome("weird_outcome"), "search_incomplete")
        self.assertEqual(rr.canonical_retrieval("retrieved_shared_window"), "retrieved")
        self.assertEqual(rr.canonical_retrieval("weird"), "not_retrieved")


class TaskMatchingTests(unittest.TestCase):
    REFS = [
        {"id": "R2", "verbatim": "Harlow, J. (2010). Dynamics and varieties of cross-media promotion. In Cross-media promotion (pp. 3-32). Halcyon Publisher."},
        {"id": "R4", "verbatim": "Vale, A.E. (2010). From chick flicks to millennial blockbusters. The Journal of Screen Culture, 43(1), 3-25."},
    ]

    def test_a_shared_year_does_not_bind_another_work(self):
        citation = {"marker": "Harlow’s (2010)", "sentence": "Drawing on Harlow’s (2010) idea.", "year": "2010"}
        self.assertEqual(mt.match_refs(citation, self.REFS), ["R2"])

    def test_a_surname_still_binds(self):
        citation = {"marker": "(Vale, 2010)", "sentence": "Franchising (Vale, 2010).", "year": "2010"}
        self.assertEqual(mt.match_refs(citation, self.REFS), ["R4"])


    def test_group_matches_each_year_and_keeps_ambiguous_members_open(self):
        refs = [{"id": rid, "verbatim": f"{author}, A. ({date}). {rid}. Press."}
                for rid, author, date in [("R1", "Hart", "2020a"), ("R2", "Hart", "2020b"),
                                            ("R3", "Rivera", "2021"), ("R4", "Kim", "2022"),
                                            ("R5", "Kim", "2022")]]
        marker = "(Hart, 2020b; Rivera, 2021; Kim, 2022; Nobody, 2023)"
        citation = {"marker": marker, "year": "2020", "sentence": "Hart mentioned Rivera."}
        self.assertEqual(mt.match_refs(citation, refs), ["R2", "R3"])
        members = mt.reference_members(citation, refs)
        self.assertEqual(members[2]["candidate_r_ids"], ["R4", "R5"])
        self.assertEqual(members[3]["candidate_r_ids"], [])
        self.assertNotIn("r_id", members[2])

    def test_task_keeps_same_page_windows_and_web_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "_work" / "P1"
            work.mkdir(parents=True)
            phrase = "Independent evidence documents this effect."
            refs = [
                {"id": "R1", "verbatim": "Hart, A. (2020). First article. Journal."},
                {"id": "R2", "verbatim": "Lee, C. (2021). Second article. Journal."},
                {"id": "R3", "verbatim": "Rivera, D. (2022). Web article. https://example.test/item"},
            ]
            registry = {}
            for index, ref in enumerate(refs, 1):
                key = f"source-{index}"
                registry[key] = {"identity": {"verbatim": ref["verbatim"]}}
                if index < 3:
                    path = root / f"{key}.txt"
                    path.write_text(f"=== PDF p1 | printed p1 | native\nCopy {index}. {phrase}",
                                    encoding="utf-8")
                    registry[key]["extraction"] = str(path)
            citation = {"id": "X1", "marker": "(Hart, 2020; Lee, 2021; Rivera, 2022)",
                        "sentence": phrase, "paragraph_id": "P1"}
            (work / "candidates.json").write_text(json.dumps({
                "citations": [citation], "references": refs}), encoding="utf-8")
            (root / "source_registry.json").write_text(json.dumps({"works": registry}),
                                                      encoding="utf-8")
            args = ["make_task.py", "--batch", tmp, "--paper", "P1", "--max-hits", "1"]
            with mock.patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
                mt.main()
            task = json.loads((work / "task.json").read_text(encoding="utf-8"))
            hits = task["windows"]["X1"]
            self.assertEqual([(h["r_id"], h["printed_page"]) for h in hits],
                             [("R1", "1"), ("R2", "1")])
            self.assertNotEqual(hits[0]["text_sha256"], hits[1]["text_sha256"])
            for hit in hits:
                original = Path(registry[hit["source_key"]]["extraction"]).read_text(encoding="utf-8")
                self.assertEqual(" ".join(original[hit["start"]:hit["end"]].split()), hit["text"])
            selection = task["candidates"]["citations"][0]["evidence_selection"]
            self.assertIn(selection["status"], {"fallback", "selected", "empty"})
            if selection["sentences"]:
                self.assertTrue(selection["sentences"][0]["text"])
            self.assertEqual(task["window_status"]["X1"]["by_reference"]["R3"]["status"],
                             "web_url")
            self.assertEqual(fw.task_urls(tmp), {"https://example.test/item": "R3"})
            web_cache.store(tmp, "https://example.test/item", {
                "text": phrase + " Site text.",
                "final_url": "https://example.test/final",
                "status": 200,
                "classification": "full_text",
            })
            with mock.patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
                mt.main()
            task = json.loads((work / "task.json").read_text(encoding="utf-8"))
            self.assertEqual([h["r_id"] for h in task["windows"]["X1"]], ["R1", "R2", "R3"])
            self.assertEqual(task["windows"]["X1"][2]["source_url"], "https://example.test/final")
            self.assertEqual(fw.task_urls(tmp), {})


class ScriptBootstrapTests(unittest.TestCase):
    SCRIPTS = ("source_registry.py", "batch_report.py", "acquisition_list.py",
               "validate_batch.py", "audit_tasks.py", "align_report.py")

    def test_batch_scripts_start_in_isolated_mode(self):
        for script in self.SCRIPTS:
            proc = subprocess.run(
                [sys.executable, "-I", os.path.join(HERE, script), "--help"],
                capture_output=True, text=True, cwd=HERE)
            self.assertEqual(proc.returncode, 0, f"{script}: {proc.stderr[-300:]}")


class InterpreterResolutionTests(unittest.TestCase):
    def _skill(self, tmp, venv=True):
        skill = os.path.join(tmp, "sourcefidelity-review")
        os.makedirs(os.path.join(skill, "scripts"))
        if venv:
            venv_python = wp.venv_candidates(skill)[0]
            os.makedirs(os.path.dirname(venv_python), exist_ok=True)
            open(venv_python, "w").close()
        return skill

    def test_the_skill_venv_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._skill(tmp)
            expected = wp.venv_candidates(skill)[0]
            result = wp.resolve(skill, check=lambda candidate: True)
        self.assertEqual(result["source"], "venv")
        self.assertEqual(result["python"], expected)
        self.assertEqual(result["limitation"], "")

    def test_python312_on_path_is_the_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._skill(tmp, venv=False)
            with mock.patch.object(wp, "version_ok", return_value=True), \
                    mock.patch.object(wp.shutil, "which",
                                      side_effect=lambda name, path=None:
                                      "/usr/local/bin/python3.12" if name == "python3.12" else None):
                result = wp.resolve(skill)
        self.assertEqual(result["source"], "path")
        self.assertEqual(result["python"], "/usr/local/bin/python3.12")

    def test_no_interpreter_reports_prompt_only_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._skill(tmp, venv=False)
            with mock.patch.object(wp.shutil, "which", return_value=None):
                result = wp.resolve(skill)
        self.assertEqual(result["python"], "")
        self.assertIn("prompt-only", result["limitation"])

    def test_windows_candidate_is_scripts_python_exe(self):
        candidate = wp.venv_candidates("C:/skills/sourcefidelity-review", "nt")[0]
        self.assertTrue(candidate.replace("\\", "/").endswith(".venv/Scripts/python.exe"))

    def test_the_review_venv_serves_a_sibling_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._skill(tmp)
            batch = os.path.join(tmp, "sourcefidelity-batch")
            os.makedirs(os.path.join(batch, "scripts"))
            expected = wp.venv_candidates(batch)[1]
            result = wp.resolve(batch, check=lambda candidate: True)
        self.assertEqual(result["source"], "venv")
        self.assertEqual(result["python"], expected)


class OcrBackendTests(unittest.TestCase):
    def _pdf(self, tmp):
        import pymupdf
        path = os.path.join(tmp, "paper.pdf")
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 72), "Image-only page text")
        document.save(path)
        document.close()
        return path

    def test_missing_tesseract_is_a_stated_limitation(self):
        with mock.patch.object(pq.shutil, "which", return_value=None):
            result = pq._run_helper("paper.pdf", pages=[1], ocr=True)
        self.assertIn("Tesseract", result["error"])

    def test_cache_root_respects_the_environment_override(self):
        with mock.patch.dict(os.environ, {"SOURCEFIDELITY_CACHE": "/tmp/sf-cache"}):
            self.assertEqual(pq.default_cache_root(), "/tmp/sf-cache")

    def test_rendering_uses_pymupdf_without_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._pdf(tmp)
            with mock.patch.object(pq, "CACHE_ROOT", os.path.join(tmp, "cache")):
                result = pq._run_helper(path, pages=[1], ocr=False, render=True)
            self.assertNotIn("error", result)
            page = result["pages"][0]
            self.assertEqual(page["pdf_page_1based"], 1)
            self.assertTrue(os.path.exists(page["png"]))
            self.assertGreater(page["width"], 0)

    def test_tesseract_reads_a_rendered_page(self):
        if not pq.shutil.which("tesseract"):
            self.skipTest("Tesseract is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._pdf(tmp)
            with mock.patch.object(pq, "CACHE_ROOT", os.path.join(tmp, "cache")):
                result = pq.ocr_text(path, [1])
        self.assertNotIn("error", result)
        self.assertEqual(result["route"], "tesseract-ocr")
        self.assertIn("Image", result["pages"][0]["text"])

    def test_ocr_text_reports_the_missing_backend(self):
        with mock.patch.object(pq, "helper_status",
                               return_value=(None, "Tesseract OCR not found.")):
            result = pq.ocr_text("paper.pdf", [1])
        self.assertIn("Tesseract", result["error"])

    def test_a_missing_language_is_reported_and_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._pdf(tmp)
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, b"Recognised text\n", b"")

            with mock.patch.object(pq, "CACHE_ROOT", os.path.join(tmp, "cache")), \
                    mock.patch.object(pq, "helper_status",
                                      return_value=("tesseract", None)), \
                    mock.patch.object(pq, "available_languages", return_value={"eng"}), \
                    mock.patch.object(pq.subprocess, "run", side_effect=fake_run):
                result = pq._run_helper(path, pages=[1], ocr=True,
                                        langs=("en-US", "zh-Hans"))
        self.assertEqual(result["languages_missing"], ["chi_sim"])
        self.assertEqual(result["pages"][0]["languages_missing"], ["chi_sim"])
        self.assertEqual(calls[0][calls[0].index("-l") + 1], "eng")

    def test_no_installed_language_is_a_stated_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._pdf(tmp)
            with mock.patch.object(pq, "helper_status",
                                   return_value=("tesseract", None)), \
                    mock.patch.object(pq, "available_languages",
                                      return_value={"osd"}), \
                    mock.patch.object(pq.subprocess, "run"):
                result = pq._run_helper(path, pages=[1], ocr=True)
        self.assertIn("no requested OCR language is installed", result["error"])
        self.assertIn("--install", result["error"])
        self.assertEqual(result["languages_missing"], ["eng", "chi_sim"])

    def test_unknown_language_list_keeps_the_requested_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._pdf(tmp)
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, b"text\n", b"")

            with mock.patch.object(pq, "CACHE_ROOT", os.path.join(tmp, "cache")), \
                    mock.patch.object(pq, "helper_status",
                                      return_value=("tesseract", None)), \
                    mock.patch.object(pq, "available_languages", return_value=None), \
                    mock.patch.object(pq.subprocess, "run", side_effect=fake_run):
                result = pq._run_helper(path, pages=[1], ocr=True)
        self.assertEqual(calls[0][calls[0].index("-l") + 1], "eng+chi_sim")
        self.assertEqual(result["languages_missing"], [])

    def test_install_language_writes_the_pack(self):
        payload = b"x" * (200 * 1024)

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return payload

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"TESSDATA_PREFIX": tmp}), \
                    mock.patch.object(pq.urllib.request, "urlopen",
                                      return_value=FakeResponse()):
                destination, detail = pq.install_language("chi_sim", binary=None)
            self.assertIsNotNone(destination, detail)
            self.assertTrue(os.path.exists(destination))
            self.assertEqual(os.path.getsize(destination), len(payload))

    def test_languages_command_reports_missing_packs(self):
        buffer = io.StringIO()
        with mock.patch.object(pq.shutil, "which", return_value="/usr/bin/tesseract"), \
                mock.patch.object(pq, "available_languages",
                                  return_value={"eng", "osd"}), \
                redirect_stdout(buffer):
            code = pq._languages_main([])
        report = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["installed"], ["eng", "osd"])
        self.assertEqual(report["missing"], ["chi_sim"])


class RedactionTests(unittest.TestCase):
    CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="xml" ContentType="application/xml"/></Types>')
    RELS = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')

    def _docx(self, tmp, paragraphs, creator="Jordan Ellis",
              modified_by="Administrator", header=None):
        path = os.path.join(tmp, "paper.docx")
        body = "".join(
            "<w:p>" + "".join(f'<w:r><w:t xml:space="preserve">{chunk}</w:t></w:r>'
                              for chunk in para) + "</w:p>"
            for para in paragraphs)
        document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
                    '2006/main"><w:body>' + body + "</w:body></w:document>")
        core = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
                'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:dcterms="http://purl.org/dc/terms/">'
                f"<dc:creator>{creator}</dc:creator>"
                f"<cp:lastModifiedBy>{modified_by}</cp:lastModifiedBy>"
                "<dcterms:created>2026-01-01T00:00:00Z</dcterms:created>"
                "</cp:coreProperties>")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", self.CONTENT_TYPES)
            archive.writestr("_rels/.rels", self.RELS)
            archive.writestr("word/document.xml", document)
            archive.writestr("docProps/core.xml", core)
            if header:
                archive.writestr(
                    "word/header1.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
                    '2006/main"><w:p><w:r><w:t>' + header + "</w:t></w:r></w:p></w:hdr>")
        return path

    def _read(self, path, member):
        with zipfile.ZipFile(path) as archive:
            return archive.read(member).decode("utf-8")

    def test_tier_one_strips_metadata_and_leaves_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Name: Jordan Ellis"], ["Body sentence stays."]])
            out = os.path.join(tmp, "copy.docx")
            report = ri.redact_file(source, out, tier=1)
            core = self._read(out, "docProps/core.xml")
            document = self._read(out, "word/document.xml")
            original = self._read(source, "docProps/core.xml")
        self.assertNotIn("Jordan Ellis", core)
        self.assertNotIn("Administrator", core)
        self.assertIn("Body sentence stays.", document)
        self.assertIn("Jordan Ellis", original)
        self.assertEqual(report["tier"], 1)

    def test_tier_two_masks_a_labelled_identity_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Name: Jordan Ellis"], ["Body stays."]])
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            document = self._read(out, "word/document.xml")
        self.assertNotIn("Jordan", document)
        self.assertIn("Name: " + "*" * len("Jordan Ellis"), document)
        self.assertIn("Body stays.", document)

    def test_tier_two_masks_a_name_split_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Name: Jordan ", "Ellis"]])
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            document = self._read(out, "word/document.xml")
        self.assertNotIn("Jordan", document)
        self.assertNotIn(">Ellis<", document)

    def test_tier_two_masks_email_and_long_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Email: je@example.com"],
                                      ["Student 2023123456 in the cohort."]])
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            document = self._read(out, "word/document.xml")
        self.assertNotIn("je@example.com", document)
        self.assertNotIn("2023123456", document)
        self.assertIn("in the cohort.", document)

    def test_a_dashless_year_range_is_not_masked_as_an_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["The films (20022016) were released."]])
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            document = self._read(out, "word/document.xml")
        self.assertIn("20022016", document)
        self.assertNotIn("********", document)

    def test_a_student_number_is_still_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Student 2023123456 in the cohort."]])
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            document = self._read(out, "word/document.xml")
        self.assertNotIn("2023123456", document)

    def test_tier_two_masks_a_header_identity_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Body only."]],
                                header="Student ID: 2023123456")
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            header = self._read(out, "word/header1.xml")
        self.assertNotIn("2023123456", header)
        self.assertIn("Student ID: " + "*" * 10, header)

    def test_tier_two_warns_when_no_label_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["An essay with no title block."]])
            out = os.path.join(tmp, "copy.docx")
            report = ri.redact_file(source, out, tier=2)
        self.assertTrue(any("label" in w.lower() for w in report["warnings"]))

    def test_pdf_masking_preserves_the_page_count(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "paper.pdf")
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Name: Jordan Ellis")
            page.insert_text((72, 100), "The argument is clear.")
            doc.save(source)
            doc.close()
            out = os.path.join(tmp, "copy.pdf")
            report = ri.redact_file(source, out, tier=2)
            masked = pymupdf.open(out)
            text = "\n".join(page.get_text() for page in masked)
            pages = masked.page_count
            masked.close()
        self.assertNotIn("Jordan", text)
        self.assertIn("argument", text)
        self.assertEqual(pages, 1)
        self.assertFalse(any("unavailable" in item for item in report["limitations"]))


class ExtractionReuseTests(unittest.TestCase):
    def test_one_extraction_subprocess_per_file(self):
        fake = mock.Mock(returncode=0,
                         stdout=b'{"text": "first line; second line", "engine": "fake"}')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.pdf")
            open(path, "wb").write(b"%PDF-1.4 first")
            with mock.patch.object(wc, "_extraction_layer",
                                   return_value=("python", "quality")), \
                    mock.patch.object(wc.subprocess, "run", return_value=fake) as run:
                wc.count(path)
                ec.build(path, "P1")
                self.assertEqual(run.call_count, 1)
                open(path, "ab").write(b" more bytes")
                wc.count(path)
                self.assertEqual(run.call_count, 2)

    def test_render_reuses_a_stored_profile_for_the_same_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paper_path = os.path.join(tmp, "a.docx")
            open(paper_path, "wb").write(b"student text")
            os.makedirs(os.path.join(tmp, "_work", "P1"))
            json.dump({"sha256": fd.sha256_file(paper_path),
                       "profile": {"style": {"words": 3}}},
                      open(os.path.join(tmp, "_work", "P1", "profile.json"), "w"))
            with mock.patch.object(ats, "paper_profile",
                                   side_effect=AssertionError("should not re-profile")):
                profile = rr.paper_profile_for(tmp, {"paper_id": "P1",
                                                     "path": paper_path})
        self.assertEqual(profile, {"style": {"words": 3}})

    def test_render_reprofiles_when_the_file_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paper_path = os.path.join(tmp, "a.docx")
            open(paper_path, "wb").write(b"new text")
            os.makedirs(os.path.join(tmp, "_work", "P1"))
            json.dump({"sha256": "stale", "profile": {"style": {"words": 1}}},
                      open(os.path.join(tmp, "_work", "P1", "profile.json"), "w"))
            with mock.patch.object(ats, "paper_profile",
                                   return_value={"style": {"words": 2}}) as profile:
                out = rr.paper_profile_for(tmp, {"paper_id": "P1", "path": paper_path})
        self.assertEqual(out, {"style": {"words": 2}})
        self.assertEqual(profile.call_count, 1)


class ExtractionLayerTests(unittest.TestCase):
    def test_a_skill_without_pdf_quality_uses_the_review_sibling(self):
        with mock.patch.object(wc.os.path, "exists",
                               side_effect=lambda path: "sourcefidelity-review" in path):
            python, quality = wc._extraction_layer()
        self.assertIn("sourcefidelity-review", python)
        self.assertTrue(quality.endswith("pdf_quality.py"))

    def test_no_layer_when_nothing_is_installed(self):
        with mock.patch.object(wc.os.path, "exists", return_value=False):
            self.assertEqual(wc._extraction_layer(), ("", ""))


class AutoFetchTests(unittest.TestCase):
    class Response:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __init__(self, body=b"<html>ok</html>", final="https://example.org/"):
            self.body = body
            self.final = final

        def read(self, limit=-1):
            return self.body[:limit] if limit >= 0 else self.body

        def geturl(self):
            return self.final

        def close(self):
            pass

    def test_policy_defaults_to_auto(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOURCEFIDELITY_FETCH_MODE", None)
            self.assertEqual(ha.public_fetch.policy_mode(), "auto")

    def test_auto_path_does_not_resolve_proxy_mapped_hosts(self):
        with mock.patch.object(ha.public_fetch, "resolve",
                               side_effect=AssertionError("resolved")), \
                mock.patch.object(ha.public_fetch, "auto_transport",
                                  return_value=self.Response()) as transport:
            result = ha.public_fetch.fetch("https://example.org/x", mode="auto")
        self.assertEqual(result["limitation"], "")
        self.assertEqual(result["status"], 200)
        transport.assert_called_once()

    def test_auto_path_still_refuses_literal_private_hosts(self):
        result = ha.public_fetch.fetch("http://127.0.0.1/", mode="auto",
                                       network=lambda *_: self.Response())
        self.assertEqual(result["limitation"], "private_host")

    def test_auto_link_audit_records_a_verified_state(self):
        fake = {"status": 200, "final_url": "https://example.org/x",
                "redirects": [], "truncated": False, "limitation": "",
                "content_type": "text/html", "bytes": 10, "body": b"<html>ok</html>"}
        with mock.patch.dict(os.environ, {"SOURCEFIDELITY_FETCH_MODE": "auto"}), \
                mock.patch.object(ha, "http_cache") as cache, \
                mock.patch.object(ha.public_fetch, "fetch", return_value=fake):
            cache.cache_get.return_value = None
            state = ha.fetch_state("https://example.org/x")
        self.assertEqual(state["state"], "ok")
        self.assertFalse(state["environment"])


class FetchPolicyTests(unittest.TestCase):
    class Response:
        def __init__(self, status, headers, body, url):
            self.status = status
            self.headers = headers
            self._body = body
            self._url = url

        def read(self, amount):
            return self._body[:amount]

        def geturl(self):
            return self._url

    def _transport(self, routes, calls=None):
        def transport(url, timeout):
            if calls is not None:
                calls.append(url)
            status, headers, body = routes[url]
            return self.Response(status, headers, body, url)
        return transport

    def test_default_urllib_opener_does_not_follow_unchecked_redirect(self):
        peer = mock.Mock()
        peer.getpeername.return_value = ("93.184.216.34", 80)
        peer.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/unavailable\r\n"
            b"Content-Length: 0\r\n\r\n")
        with mock.patch.object(ha.public_fetch.socket, "create_connection",
                               return_value=peer) as connect, \
                mock.patch.dict(os.environ, {"http_proxy": "http://proxy.test:9090"}):
            result = sf.fetch("http://example.test/start",
                              resolver=lambda _: {"93.184.216.34"})
        connect.assert_called_once()
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 80))
        self.assertIn(b"Host: example.test", peer.sendall.call_args.args[0])
        self.assertEqual(result["limitation"], "private_host")
        self.assertEqual(result["redirects"], [{"status": 302, "url": "http://127.0.0.1/unavailable"}])

    def test_changed_peer_on_a_redirect_is_closed_before_http_bytes(self):
        first = mock.Mock()
        first.getpeername.return_value = ("93.184.216.34", 80)
        first.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 302 Found\r\nLocation: http://second.test/\r\n"
            b"Content-Length: 0\r\n\r\n")
        second = mock.Mock()
        second.getpeername.return_value = ("10.0.0.9", 80)
        resolver = mock.Mock(side_effect=[{"93.184.216.34"}, {"1.1.1.1"}])
        with mock.patch.object(ha.public_fetch.socket, "create_connection",
                               side_effect=[first, second]) as connect:
            result = sf.fetch("http://first.test/", resolver=resolver)
        self.assertEqual(result["limitation"], "peer_refused")
        self.assertEqual(connect.call_args.args[0], ("1.1.1.1", 80))
        self.assertEqual(resolver.call_args_list, [mock.call("first.test"), mock.call("second.test")])
        second.close.assert_called_once()
        second.sendall.assert_not_called()

    def test_https_peer_is_verified_before_tls_and_retains_hostname(self):
        import ssl

        peer = mock.Mock()
        peer.getpeername.return_value = ("1.1.1.1", 443)
        context = mock.Mock()
        with mock.patch.object(ha.public_fetch.socket, "create_connection", return_value=peer), \
                mock.patch.object(ssl, "_create_default_https_context", return_value=context):
            rejected = sf.fetch("https://example.test/", resolver=lambda _: {"8.8.8.8"})
            self.assertEqual(rejected["limitation"], "peer_refused")
            context.wrap_socket.assert_not_called()
            context.wrap_socket.return_value = peer
            peer.makefile.return_value = io.BytesIO(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok")
            accepted = sf.fetch("https://example.test/", resolver=lambda _: {"1.1.1.1"})
        context.wrap_socket.assert_called_once_with(peer, server_hostname="example.test")
        self.assertEqual(accepted["text"], "ok")

    def test_redirect_hops_share_timeout_and_close_each_response(self):
        first = self.Response(302, {"Location": "/next"}, b"", "https://example.test/start")
        second = self.Response(200, {"Content-Type": "text/plain"}, b"abcdef",
                               "https://example.test/next")
        first.close = mock.Mock()
        second.close = mock.Mock()
        transport = mock.Mock(side_effect=[first, second])
        with mock.patch.object(ha.public_fetch.time, "monotonic",
                               side_effect=[10.0, 10.0, 12.0, 12.0]):
            result = sf.fetch("https://example.test/start", network=transport,
                              resolver=lambda _: {"93.184.216.34"}, timeout=5, max_bytes=3)
        self.assertEqual(transport.call_args_list, [
            mock.call("https://example.test/start", 5.0),
            mock.call("https://example.test/next", 3.0)])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["text"], "abc")
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_refuses_a_non_http_scheme(self):
        calls = []
        out = sf.fetch("file:///etc/passwd",
                       network=self._transport({}, calls), resolver=lambda h: {"93.184.216.34"})
        self.assertEqual(out["limitation"], "unsupported_scheme")
        self.assertEqual(calls, [])

    def test_refuses_embedded_credentials(self):
        out = sf.fetch("https://user:pass@example.com/x",
                       network=self._transport({}), resolver=lambda h: {"93.184.216.34"})
        self.assertEqual(out["limitation"], "credentials_in_url")

    def test_refuses_private_and_loopback_hosts(self):
        for url in ("http://127.0.0.1/x", "http://localhost/x", "http://169.254.1.1/x"):
            out = sf.fetch(url, network=self._transport({}),
                           resolver=lambda h: {"93.184.216.34"})
            self.assertEqual(out["limitation"], "private_host", url)
        out = sf.fetch("http://evil.example/x", network=self._transport({}),
                       resolver=lambda h: {"10.0.0.5"})
        self.assertEqual(out["limitation"], "private_host")

    def test_a_proxy_fake_ip_is_refused(self):
        out = sf.fetch("https://example.com/x", network=self._transport({}, []),
                       resolver=lambda h: {"198.18.0.7"})
        self.assertEqual(out["limitation"], "private_host")

    def test_a_real_private_address_is_still_refused(self):
        out = sf.fetch("https://example.com/x", network=self._transport({}),
                       resolver=lambda h: {"192.168.1.5"})
        self.assertEqual(out["limitation"], "private_host")

    def test_rechecks_each_redirect(self):
        calls = []
        routes = {"https://example.com/a": (302, {"Location": "http://127.0.0.1/b"}, b"")}
        out = sf.fetch("https://example.com/a", network=self._transport(routes, calls),
                       resolver=lambda h: {"93.184.216.34"})
        self.assertEqual(out["limitation"], "private_host")
        self.assertEqual(calls, ["https://example.com/a"])

    def test_follows_an_allowed_redirect(self):
        routes = {
            "https://example.com/a": (302, {"Location": "https://example.com/b"}, b""),
            "https://example.com/b": (200, {"Content-Type": "text/html; charset=utf-8"},
                                      b"<html><body>cited text</body></html>"),
        }
        out = sf.fetch("https://example.com/a", network=self._transport(routes),
                       resolver=lambda h: {"93.184.216.34"})
        self.assertEqual(out["limitation"], "")
        self.assertEqual(out["final_url"], "https://example.com/b")
        self.assertIn("cited text", out["text"])

    def test_enforces_a_byte_cap(self):
        routes = {"https://example.com/x": (200, {"Content-Type": "text/plain"}, b"a" * 100)}
        out = sf.fetch("https://example.com/x", max_bytes=10,
                       network=self._transport(routes),
                       resolver=lambda h: {"93.184.216.34"})
        self.assertTrue(out["truncated"])
        self.assertEqual(out["bytes"], 10)

    def test_rejects_a_disallowed_content_type(self):
        routes = {"https://example.com/x": (200, {"Content-Type": "application/zip"}, b"PK")}
        out = sf.fetch("https://example.com/x", network=self._transport(routes),
                       resolver=lambda h: {"93.184.216.34"})
        self.assertEqual(out["limitation"], "content_type")


    def test_accepts_json_for_api_adapters(self):
        routes = {"https://example.com/x": (200, {"Content-Type": "application/json"},
                                           b'{"ok": true}')}
        out = sf.fetch("https://example.com/x", network=self._transport(routes),
                       resolver=lambda h: {"93.184.216.34"})
        self.assertEqual(out["limitation"], "")
        self.assertIn('"ok"', out["text"])

    def test_rate_limit_is_classified(self):
        def network(url, timeout):
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        out = sf.fetch("https://api.openalex.org/works", network=network,
                       resolver=lambda h: {"93.184.216.34"})
        self.assertEqual(out["status"], 429)
        self.assertEqual(out["limitation"], "rate_limited")


class CohortOverlapTests(unittest.TestCase):
    def _summary(self):
        return {"paper_id": "P1", "references": {"total": 10},
                "sources": [{"source_key": "k", "search_status": "searched"}],
                "nonexistent_sources": [], "unverifiable_sources": [],
                "flags": {}}

    def test_cohort_reports_overlap_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "P1"))
            json.dump(self._summary(), open(os.path.join(tmp, "P1", "summary.json"),
                                            "w", encoding="utf-8"))
            data = ats.cohort(pathlib.Path(tmp), pathlib.Path(tmp))
        self.assertEqual(set(data["papers"]["P1"]), {"S3"})
        for absent in ("duplicates", "venue_patterns", "venue_observations", "profiles"):
            self.assertNotIn(absent, data)
        self.assertIn("Cohort text overlap", ats.markdown(data))


class PageMapOffsetTests(unittest.TestCase):
    def _page(self, pdf, folio, kind="arabic"):
        return {"pdf_page": pdf, "folio": "" if folio is None else str(folio),
                "folio_kind": kind if folio is not None else "none",
                "folio_value": folio}

    def test_one_folio_is_not_corroboration(self):
        offset = page_map.infer_offset([self._page(1, 5)])
        self.assertFalse(offset["corroborated"])

    def test_a_stable_offset_is_corroborated(self):
        pages = [self._page(n, n + 4) for n in range(1, 6)]
        offset = page_map.infer_offset(pages)
        self.assertTrue(offset["corroborated"])
        self.assertEqual(offset["coverage"], 1.0)

    def test_a_pagination_reset_is_not_corroborated(self):
        pages = [self._page(1, 10), self._page(2, 11), self._page(3, 12),
                 self._page(4, 1), self._page(5, 2)]
        offset = page_map.infer_offset(pages)
        self.assertFalse(offset["corroborated"])

    def test_observed_folios_survive_and_inferred_pages_are_labelled(self):
        pages = [self._page(1, 5), self._page(2, None), self._page(3, 7)]
        out = page_map.assign_printed(pages, {"arabic_offset": -4,
                                              "corroborated": True})
        self.assertEqual(out[0]["printed"], "5")
        self.assertEqual(out[0]["printed_source"], "observed")
        self.assertEqual(out[1]["printed"], "6")
        self.assertEqual(out[1]["printed_source"], "inferred")
        self.assertEqual(out[2]["printed"], "7")

    def test_an_uncorroborated_offset_leaves_unmapped_pages_empty(self):
        pages = [self._page(1, 5), self._page(2, None)]
        out = page_map.assign_printed(pages, {"arabic_offset": -4,
                                              "corroborated": False})
        self.assertEqual(out[0]["printed"], "5")
        self.assertEqual(out[1]["printed"], "")
        self.assertEqual(out[1]["printed_source"], "unmapped")


class WordCountProvenanceTests(unittest.TestCase):
    def test_an_unavailable_extraction_layer_is_reported(self):
        with mock.patch.object(wc, "pdf_blocks", return_value=([], None)):
            result = wc.count("paper.pdf")
        self.assertIs(result["counts_available"], False)
        self.assertEqual(result["extraction"]["engine"], "pypdf fallback")
        self.assertTrue(any("OCR unavailable" in w for w in result["warnings"]))

    def test_partial_fallback_counts_are_marked_unknown(self):
        with mock.patch.object(wc, "pdf_blocks", return_value=(["some words here"], None)):
            result = wc.count("paper.pdf")
        self.assertEqual(result["counts_available"], "unknown")
        self.assertTrue(any("may be partial" in w for w in result["warnings"]))

    def test_plain_text_counts_are_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.txt")
            open(path, "w", encoding="utf-8").write(
                "Body words here.\nReferences\nSmith, J. (2020). A work.\n")
            result = wc.count(path)
        self.assertIs(result["counts_available"], True)
        self.assertNotIn("extraction", result)

    def test_the_review_notes_fallback_counts(self):
        paper = {"paper_id": "P1", "path": "/tmp/a.pdf",
                 "word_counts": {"total": 2, "text": 2, "references": 0,
                                 "counts_available": "unknown"}}
        findings = {"title": "T", "date": "2026-11-18", "status": "complete",
                    "summary": {}, "occurrences": [], "references": [],
                    "works": [], "uncited": [], "audit": {},
                    "inventory_empty": True}
        text = rr.render_md(paper, findings, rr.counts(findings), {})
        self.assertIn("pypdf fallback", text)


class EvidenceWindowTests(unittest.TestCase):
    TEXT = ("=== PDF p19 | printed p9 |\n"
            "END OF CHAPTER NINE sentinel text here\n"
            "=== PDF p20 | printed p10 |\n"
            "the cited phrase appears in chapter ten\n"
            "=== PDF p21 | printed p11 |\n"
            "chapter eleven follows\n")

    def _marks(self):
        return [(m.start(), int(m.group(1)), m.group(2).strip())
                for m in mt.PAGE_BLOCK.finditer(self.TEXT)]

    def test_a_window_stops_at_the_previous_page(self):
        marks = self._marks()
        pos = self.TEXT.index("cited phrase")
        bounds = ew.bounded(self.TEXT, pos, len("cited phrase"), 240, marks, [10, 12])
        self.assertIsNotNone(bounds)
        start, end = bounds
        self.assertNotIn("END OF CHAPTER NINE", self.TEXT[start:end])

    def test_a_match_spanning_a_disallowed_boundary_is_dropped(self):
        marks = self._marks()
        pos = self.TEXT.index("sentinel text")
        self.assertIsNone(ew.bounded(self.TEXT, pos, len("sentinel text"), 40,
                                     marks, [10, 12]))

    def test_task_windows_stay_inside_the_cited_chapter(self):
        marks = self._marks()
        out = mt.windows(self.TEXT, marks, {}, ["cited phrase appears in chapter ten"],
                         240, [10, 12])
        self.assertTrue(out)
        self.assertNotIn("END OF CHAPTER NINE", out[0]["text"])

    def test_lookup_cli_stays_inside_the_page_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "text.txt")
            open(path, "w", encoding="utf-8").write(self.TEXT)
            proc = subprocess.run(
                [sys.executable, "-I", os.path.join(HERE, "lookup.py"),
                 "--text", path, "--query", "cited phrase", "--window", "240",
                 "--min-page", "10", "--max-page", "12"],
                capture_output=True, text=True)
            hits = json.loads(proc.stdout)["hits"]
        self.assertTrue(hits)
        self.assertNotIn("END OF CHAPTER NINE", hits[0]["text"])


class PrepareFailureTests(unittest.TestCase):
    def _run(self, script, *args):
        return subprocess.run([sys.executable, "-I", os.path.join(HERE, script), *args],
                              capture_output=True, text=True)

    def test_failed_extraction_removes_stale_candidates_and_blocks_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers = os.path.join(tmp, "papers", "participant-1")
            os.makedirs(papers)
            open(os.path.join(papers, "P1.docx"), "w").write("not a real docx")
            out = os.path.join(tmp, "batch")
            os.makedirs(os.path.join(out, "_work", "P1"))
            stale = os.path.join(out, "_work", "P1", "candidates.json")
            json.dump({"paper_id": "P1", "citations": [{"id": "X1"}],
                       "references": []}, open(stale, "w", encoding="utf-8"))

            prep = self._run("prepare_batch.py", "--papers",
                             os.path.join(tmp, "papers"), "--out", out)
            self.assertEqual(prep.returncode, 0, prep.stderr)

            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(os.path.join(out, "_work", "P1",
                                                        "extraction_failed.json")))
            paper = json.load(open(os.path.join(out, "_work", "P1", "paper.json"),
                                   encoding="utf-8"))
            self.assertEqual(paper["status"], "extraction_failed")
            summary = json.load(open(os.path.join(out, "prep_summary.json"),
                                     encoding="utf-8"))
            self.assertIn("P1", summary["extraction_failed"])

            task = self._run("make_task.py", "--batch", out, "--paper", "P1",
                             "--out", os.path.join(tmp, "task.json"))
            self.assertNotEqual(task.returncode, 0)
            self.assertIn("extraction failed", task.stderr)


class PaperIndexTests(unittest.TestCase):
    def _batch(self, tmp, ids, manifest=None):
        for pid in ids:
            folder = os.path.join(tmp, pid)
            os.makedirs(folder)
            json.dump({"paper_id": pid}, open(os.path.join(folder, "summary.json"), "w"))
        if manifest is not None:
            with open(os.path.join(tmp, "batch_manifest.tsv"), "w", encoding="utf-8") as fh:
                fh.write("paper_id\tparticipant_dir\tfilename\ttotal_words\n")
                for pid in manifest:
                    fh.write(f"{pid}\tparticipant-1\t{pid}.docx\t100\n")

    def test_manifest_ids_cover_a_non_participant_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._batch(tmp, ["essay", "P1"], manifest=["essay", "P1"])
            self.assertEqual(pi.paper_ids(tmp), ["P1", "essay"])

    def test_legacy_layout_without_a_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._batch(tmp, ["P1", "paper-P2"])
            self.assertEqual(pi.paper_ids(tmp), ["P1", "paper-P2"])

    def test_batch_report_finds_every_manifest_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._batch(tmp, ["essay", "P1"], manifest=["essay", "P1"])
            names = sorted(os.path.basename(os.path.dirname(p)) for p in br.summary_files(pathlib.Path(tmp)))
        self.assertEqual(names, ["P1", "essay"])

    def test_task_audit_finds_a_non_paper_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._batch(tmp, ["essay"], manifest=["essay"])
            self.assertIn("essay", at.paper_ids(tmp))

    def test_missing_summaries_lists_a_scheduled_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._batch(tmp, ["P1"], manifest=["P1", "P2"])
            self.assertEqual(pi.missing_summaries(tmp), ["P2"])

    def test_a_scheduled_paper_without_a_summary_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._batch(tmp, ["P1"], manifest=["P1", "P2"])
            self.assertEqual([os.path.basename(os.path.dirname(p)) for p in pi.summary_paths(tmp)],
                             ["P1"])


class ReappraiseIdentityTests(unittest.TestCase):
    def _data(self):
        return {
            "occurrences": {"total": 2, "supports": 1, "qualified": 0,
                            "contradicts": 0, "not_supported": 0, "not_judged": 1},
            "sources": [
                {"source_key": "hollis-2006", "retrieval": "unverifiable",
                 "identity_outcome": "unverifiable", "search_status": "searched"},
                {"source_key": "other-work", "retrieval": "identity_only",
                 "identity_outcome": "search_incomplete", "search_status": "searched"},
            ],
            "nonexistent_sources": [
                {"source": "Hollis 2006", "source_key": "hollis-2006", "r_id": "R1"},
                {"source": "Hollis 2006 again", "source_key": "hollis-2006", "r_id": "R2"}],
            "unverifiable_sources": [{"source": "Hollis 2006", "source_key": "hollis-2006",
                                      "r_id": "R1"}],
            "misrepresented_sources": [],
            "flags": {"fabrication_risk": True},
        }

    def _task(self, status="supplied_confirmed"):
        return {"sources": {"hollis-2006": {
                    "supplied_file": "/tmp/hollis.pdf",
                    "identity": {"title": "Convergent Media", "year": 2006},
                    "identity_status": status}},
                "entries": [{"id": "C1", "prior": "Not Judged"}]}

    def _judgments(self, **extra):
        change = {"id": "C1", "prior": "Not Judged", "new": "Supports",
                  "source_key": "hollis-2006", "evidence": "quote"}
        change.update(extra)
        return {"changes": [change]}

    def test_a_confirmed_supplied_copy_clears_the_finding_and_flag(self):
        data = ra.render_summary_data(self._data(), self._task(), self._judgments(), "2026-11-18")
        self.assertEqual(data["nonexistent_sources"], [])
        self.assertEqual(data["unverifiable_sources"], [])
        self.assertFalse(data["flags"]["fabrication_risk"])
        source = next(s for s in data["sources"] if s["source_key"] == "hollis-2006")
        self.assertEqual(source["retrieval"], "retrieved")
        self.assertEqual(source["identity_outcome"], "confirmed")
        audit = data["reappraisal"]["identity_resolved"]
        self.assertEqual(audit[0]["source_key"], "hollis-2006")
        self.assertEqual(audit[0]["prior_outcome"], "unverifiable")
        self.assertEqual(audit[0]["removed_from"], ["nonexistent_sources",
                                                   "unverifiable_sources"])

    def test_an_ambiguous_supplied_copy_leaves_the_finding_in_place(self):
        data = ra.render_summary_data(self._data(), self._task("ambiguous"),
                                      self._judgments(), "2026-11-18")
        self.assertEqual(len(data["nonexistent_sources"]), 2)
        self.assertEqual(len(data["unverifiable_sources"]), 1)
        self.assertTrue(data["flags"]["fabrication_risk"])
        self.assertEqual(data["reappraisal"]["identity_unresolved"][0]["source_key"],
                         "hollis-2006")

    def test_a_change_can_declare_the_identity_still_unresolved(self):
        data = ra.render_summary_data(self._data(), self._task(),
                                      self._judgments(identity_unresolved=True),
                                      "2026-11-18")
        self.assertEqual(len(data["nonexistent_sources"]), 2)
        self.assertTrue(data["flags"]["fabrication_risk"])

    def test_unrelated_sources_keep_their_entries(self):
        data = self._data()
        data["unverifiable_sources"].append({"source": "Other 2019",
                                             "source_key": "other-work", "r_id": "R2"})
        out = ra.render_summary_data(data, self._task(), self._judgments(), "2026-11-18")
        self.assertEqual([e["source_key"] for e in out["unverifiable_sources"]],
                         ["other-work"])


class SourceKeyTests(unittest.TestCase):
    A = "Smith, J. (2020). Digital Media and Cultural Change. Journal A. doi:10.1000/aaa"
    B = "Smith, J. (2020). Digital Media and Cultural Comparison. Journal B. doi:10.1000/bbb"

    def test_distinct_dois_with_the_same_prefix_stay_distinct(self):
        self.assertNotEqual(al.key_from_entry(self.A), al.key_from_entry(self.B))

    def test_the_same_doi_in_different_formats_shares_a_key(self):
        left = "Smith, J. (2020). Digital Media and Cultural Change. Journal A. doi:10.1000/AAA."
        right = ("Smith, J. (2020). Digital Media and Cultural Change. Journal A. "
                 "https://doi.org/10.1000/aaa")
        self.assertEqual(al.key_from_entry(left), al.key_from_entry(right))

    def test_identifierless_titles_sharing_a_prefix_stay_distinct(self):
        left = "Smith, J. (2020). Digital Media and Cultural Change. Journal A."
        right = "Smith, J. (2020). Digital Media and Cultural Comparison. Journal B."
        self.assertNotEqual(al.key_from_entry(left), al.key_from_entry(right))

    def test_identifierless_formatting_variants_share_a_key(self):
        left = "Smith, J. (2020). Digital Media and Cultural Change. Journal A."
        right = "Smith, J. (2020). digital media & cultural change. journal a."
        self.assertEqual(al.key_from_entry(left), al.key_from_entry(right))

    def test_candidate_sources_keeps_two_works_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "_work", "P1")
            os.makedirs(work)
            json.dump({"paper_id": "P1",
                       "references": [{"id": "R1", "verbatim": self.A},
                                      {"id": "R2", "verbatim": self.B}]},
                      open(os.path.join(work, "candidates.json"), "w",
                           encoding="utf-8"))
            works = sr.candidate_sources(tmp)
        self.assertEqual(len(works), 2, sorted(works))
        titles = sorted(w["entries"][0]["source"] for w in works.values())
        self.assertIn(self.A, titles)
        self.assertIn(self.B, titles)

    def test_canonicalise_will_not_merge_conflicting_dois(self):
        works = {
            "smith-2020-digital-media": {"papers": {"P1"}, "citations": 1,
                                         "entries": [{"source": self.A}]},
            "smith-2020-digital-media-cultural": {"papers": {"P2"}, "citations": 1,
                                                  "entries": [{"source": self.B}]},
        }
        aliases = sr.canonicalise(works)
        self.assertEqual(aliases, {})
        self.assertEqual(len(works), 2)

    def test_canonicalise_merges_variants_of_the_same_doi(self):
        variant = ("Smith, J. (2020). Digital media and cultural change. Journal A. "
                   "doi:10.1000/aaa")
        works = {
            "smith-2020-digital-media": {"papers": {"P1"}, "citations": 1,
                                         "entries": [{"source": self.A}]},
            "smith-2020-digital-culture": {"papers": {"P2"}, "citations": 2,
                                           "entries": [{"source": variant}]},
        }
        aliases = sr.canonicalise(works)
        self.assertEqual(len(works), 1)
        primary = next(iter(works))
        self.assertEqual(aliases.get("smith-2020-digital-media", primary), primary)
        self.assertEqual(works[primary]["papers"], {"P1", "P2"})
        self.assertEqual(works[primary]["citations"], 3)


class InventoryValidationTests(unittest.TestCase):
    def _cand(self):
        return {
            "paragraphs": [{"id": "p1", "text": "Claim (Doe, 2020) holds."}],
            "citations": [{"id": "X1", "paragraph_id": "p1",
                           "marker": "(Doe, 2020)"}],
            "references": [],
        }

    def _inv(self, occurrences=None, dispositions=None, coverage=True):
        return {
            "occurrences": occurrences or [],
            "references": [],
            "dispositions": dispositions if dispositions is not None else {"X1": "confirmed"},
            "coverage": ({"paragraphs_scanned": ["p1"]} if coverage else {}),
        }

    def test_strict_rejects_confirmed_candidate_with_no_final_entry(self):
        result = vi.validate(self._cand(), self._inv(), strict=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("X1" in e and "confirmed" in e for e in result["errors"]))

    def test_confirmed_candidate_matching_a_final_entry_is_accepted(self):
        occ = [{"id": "C1", "paragraph_id": "p1", "marker": "(Doe, 2020)",
                "statement": "Claim", "cited_r_ids": []}]
        result = vi.validate(self._cand(), self._inv(occurrences=occ), strict=True)
        self.assertTrue(result["ok"], result["errors"])

    def test_confirmed_as_target_must_exist(self):
        result = vi.validate(self._cand(),
                             self._inv(dispositions={"X1": "confirmed_as:C9"}),
                             strict=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("confirmed target C9" in e for e in result["errors"]))

    def test_strict_treats_absent_coverage_as_an_error(self):
        result = vi.validate(self._cand(), self._inv(coverage=False), strict=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("coverage block absent" in e for e in result["errors"]))
        loose = vi.validate(self._cand(), self._inv(coverage=False), strict=False)
        self.assertTrue(loose["ok"])
        self.assertTrue(any("coverage block absent" in w for w in loose["warnings"]))


class PaperPathTests(unittest.TestCase):
    @staticmethod
    def _link_directory(target, link):
        """Link a directory: a POSIX symlink, or a junction on Windows.

        A junction needs no privilege, unlike a Windows symbolic link, and
        os.path.realpath resolves it the same way.
        """
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           check=True, capture_output=True)
        else:
            os.symlink(target, link)

    def test_paper_folder_rejects_escaping_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("", ".", "..", "../evil", "/tmp/evil", "a/b", "a\\b"):
                with self.assertRaises(SystemExit, msg=bad):
                    bp.paper_folder(tmp, bad)

    def test_paper_folder_accepts_a_direct_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "P1"))
            self.assertEqual(bp.paper_folder(tmp, "P1"),
                             os.path.realpath(os.path.join(tmp, "P1")))

    def test_paper_folder_rejects_symlinked_paper_directory(self):
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as outside:
            self._link_directory(outside, os.path.join(tmp, "P2"))
            with self.assertRaises(SystemExit):
                bp.paper_folder(tmp, "P2")

    @unittest.skipIf(os.name == "nt",
                     "a Windows file symlink needs SeCreateSymbolicLinkPrivilege")
    def test_artifact_rejects_a_symlinked_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as outside:
            folder = os.path.join(tmp, "P1")
            os.makedirs(folder)
            target = os.path.join(outside, "review.md")
            open(target, "w", encoding="utf-8").write("elsewhere")
            os.symlink(target, os.path.join(folder, "review.md"))
            with self.assertRaises(SystemExit):
                bp.artifact(folder, "review.md")

    @unittest.skipIf(os.name == "nt",
                     "a Windows file symlink needs SeCreateSymbolicLinkPrivilege")
    def test_apply_all_refuses_a_symlinked_review_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as outside:
            folder = os.path.join(tmp, "P1")
            os.makedirs(folder)
            target = os.path.join(outside, "review.md")
            open(target, "w", encoding="utf-8").write("elsewhere")
            os.symlink(target, os.path.join(folder, "review.md"))
            json.dump({"occurrences": {"total": 1}},
                      open(os.path.join(folder, "summary.json"), "w",
                           encoding="utf-8"))
            task = {"entries": [{"id": "C1", "prior": "Not Judged"}]}
            judgments = {"changes": [{"id": "C1", "prior": "Not Judged",
                                      "new": "Supports"}]}
            with self.assertRaises(SystemExit):
                ra.apply_all(folder, task, judgments, "2026-11-18")
            self.assertEqual(open(target, encoding="utf-8").read(), "elsewhere")


class EnrichTests(unittest.TestCase):
    def _registry(self):
        return {
            "smith-2020-digital-media": {
                "identity": {"title": "Digital Media and Cultural Change",
                             "author": "Smith, J.", "doi": "10.1/abc"},
                "work_kind": "chapter",
            },
            "jones-2021-another-work": {
                "identity": {"title": "Another Work Title", "author": "Jones, A."},
                "work_kind": "other",
            },
        }

    def test_dry_run_plans_without_calls_or_writes(self):
        registry = self._registry()
        before_registry = json.loads(json.dumps(registry))
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(os.listdir(tmp))
            with mock.patch.object(er, "fetch",
                                   side_effect=lambda *a, **k: calls.append(a)):
                stats = er.enrich(registry, tmp, dry_run=True)
            after = sorted(os.listdir(tmp))
        self.assertEqual(calls, [])
        self.assertEqual(before, after)
        self.assertEqual(registry, before_registry)
        self.assertEqual(stats["planned"], 2)
        self.assertEqual(stats["lookups"], 2)
        self.assertEqual(stats["provider_calls"], 0)
        self.assertEqual(stats["writes"], 0)

    def test_normal_mode_still_calls_the_provider(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            def boom(*args, **kwargs):
                calls.append(args)
                raise RuntimeError("network down")
            with mock.patch.object(er, "fetch", side_effect=boom):
                stats = er.enrich(self._registry(), tmp)
        self.assertEqual(len(calls), 2)
        self.assertEqual(stats["errors"], 2)


class ExtractionCacheTests(unittest.TestCase):
    def test_differently_labelled_copy_is_rejected(self):
        self.assertTrue(ps.source_key_conflict({"source_key": "R1"}, "R2"))
        self.assertFalse(ps.source_key_conflict({"source_key": "R1"}, "R1"))
        self.assertFalse(ps.source_key_conflict({}, "R2"))

    def test_version_options_and_output_files_must_match(self):
        with tempfile.TemporaryDirectory() as out:
            options = {"dpi": 220, "max_ocr_pages": 0, "seed_json": False}
            text_path = os.path.join(out, "text.txt")
            pages_path = os.path.join(out, "pages.csv")
            open(text_path, "w", encoding="utf-8").write("text")
            open(pages_path, "w", encoding="utf-8").write("header\n")
            meta = {"sha256": "abc", "extractor_version": ps.EXTRACTOR_VERSION,
                    "extraction_options": options,
                    "text_sha256": ps.sha256_file(text_path),
                    "pages_sha256": ps.sha256_file(pages_path)}
            open(os.path.join(out, "meta.json"), "w", encoding="utf-8").write("{}")
            self.assertTrue(ps.cache_is_current(out, meta, "abc", options))
            self.assertFalse(ps.cache_is_current(out, {**meta, "extractor_version": "old"},
                                                "abc", options))
            self.assertFalse(ps.cache_is_current(out, meta, "abc",
                                                {**options, "dpi": 300}))
            open(text_path, "a", encoding="utf-8").write("tampered")
            self.assertFalse(ps.cache_is_current(out, meta, "abc", options))
            open(text_path, "w", encoding="utf-8").write("text")
            os.remove(text_path)
            self.assertFalse(ps.cache_is_current(out, meta, "abc", options))


class SourceBindingTests(unittest.TestCase):
    def _write_extraction(self, root, key, digest, text, offset=None, version=None):
        out = os.path.join(root, "_sources", key)
        os.makedirs(out, exist_ok=True)
        json.dump({"sha256": digest, "offset": offset or {},
                   "extractor_version": version or rp.prepare_source_module.EXTRACTOR_VERSION,
                   "extraction_options": {"dpi": rp.prepare_source_module.DEFAULT_DPI,
                                          "max_ocr_pages": 0, "seed_json": False},
                   "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "pages_sha256": ""},
                  open(os.path.join(out, "meta.json"), "w", encoding="utf-8"))
        open(os.path.join(out, "text.txt"), "w", encoding="utf-8").write(text)

    def test_new_copy_replaces_the_stale_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            supplied = os.path.join(tmp, "new-copy.pdf")
            open(supplied, "wb").write(b"NEW CONTENT")
            self._write_extraction(tmp, "smith-2020", "oldhash", "OLD TEXT",
                                   {"arabic_offset": 3})
            new_offset = {"arabic_offset": 7, "coverage": 0.9}

            def fake_prepare(path, out, key):
                self._write_extraction(tmp, key, rp.sha256_file(path),
                                       "NEW TEXT", new_offset)

            with mock.patch.object(rp, "run_prepare_source", side_effect=fake_prepare):
                bound = rp.prepare_extraction(tmp, "smith-2020", supplied)

            self.assertEqual(bound["limitation"], "")
            self.assertEqual(bound["page_map"], new_offset)
            self.assertEqual(open(bound["extraction"], encoding="utf-8").read(),
                             "NEW TEXT")
            self.assertEqual(bound["sha256"], rp.sha256_file(supplied))

    def test_old_extraction_version_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            supplied = os.path.join(tmp, "copy.pdf")
            open(supplied, "wb").write(b"COPY")
            self._write_extraction(tmp, "key", rp.sha256_file(supplied), "OLD",
                                   version="prepare-source-v1")
            calls = []

            def fake_prepare(path, out, key):
                calls.append(key)
                self._write_extraction(tmp, key, rp.sha256_file(path), "NEW")

            with mock.patch.object(rp, "run_prepare_source", side_effect=fake_prepare):
                bound = rp.prepare_extraction(tmp, "key", supplied)
            self.assertEqual(calls, ["key"])
            self.assertEqual(open(bound["extraction"], encoding="utf-8").read(), "NEW")
            self.assertEqual(bound["extraction_version"],
                             rp.prepare_source_module.EXTRACTOR_VERSION)

    def test_tampered_text_is_reextracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            supplied = os.path.join(tmp, "copy.pdf")
            open(supplied, "wb").write(b"COPY")
            self._write_extraction(tmp, "key", rp.sha256_file(supplied), "ORIGINAL")
            open(os.path.join(tmp, "_sources", "key", "text.txt"), "w",
                 encoding="utf-8").write("TAMPERED")
            calls = []

            def fake_prepare(path, out, key):
                calls.append(key)
                self._write_extraction(tmp, key, rp.sha256_file(path), "REBUILT")

            with mock.patch.object(rp, "run_prepare_source", side_effect=fake_prepare):
                bound = rp.prepare_extraction(tmp, "key", supplied)
            self.assertEqual(calls, ["key"])
            self.assertEqual(open(bound["extraction"], encoding="utf-8").read(), "REBUILT")
            self.assertEqual(bound["limitation"], "")

    def test_missing_extraction_text_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            supplied = os.path.join(tmp, "copy.pdf")
            open(supplied, "wb").write(b"COPY")
            self._write_extraction(tmp, "key", rp.sha256_file(supplied), "OLD")
            os.remove(os.path.join(tmp, "_sources", "key", "text.txt"))
            calls = []

            def fake_prepare(path, out, key):
                calls.append(key)
                self._write_extraction(tmp, key, rp.sha256_file(path), "NEW")

            with mock.patch.object(rp, "run_prepare_source", side_effect=fake_prepare):
                bound = rp.prepare_extraction(tmp, "key", supplied)
        self.assertEqual(calls, ["key"])
        self.assertEqual(bound["limitation"], "")

    def test_identical_copy_reuses_the_verified_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            supplied = os.path.join(tmp, "same-copy.pdf")
            open(supplied, "wb").write(b"SAME")
            digest = rp.sha256_file(supplied)
            self._write_extraction(tmp, "smith-2020", digest, "TEXT", {"arabic_offset": 2})
            with mock.patch.object(rp, "run_prepare_source",
                                   side_effect=AssertionError("should not re-extract")):
                bound = rp.prepare_extraction(tmp, "smith-2020", supplied)
            self.assertEqual(bound["limitation"], "")
            self.assertEqual(open(bound["extraction"], encoding="utf-8").read(), "TEXT")

    def test_failed_extraction_of_a_different_copy_reports_a_limitation(self):
        with tempfile.TemporaryDirectory() as tmp:
            supplied = os.path.join(tmp, "edition-two.pdf")
            open(supplied, "wb").write(b"DIFFERENT")
            self._write_extraction(tmp, "smith-2020", "oldhash", "OLD TEXT")
            with mock.patch.object(rp, "run_prepare_source", return_value=None):
                bound = rp.prepare_extraction(tmp, "smith-2020", supplied)
            self.assertEqual(bound["extraction"], "")
            self.assertEqual(bound["page_map"], {})
            self.assertEqual(bound["limitation"], "supplied_copy_not_extracted")

    def test_missing_extraction_after_a_failed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            supplied = os.path.join(tmp, "first-copy.pdf")
            open(supplied, "wb").write(b"ANY")
            with mock.patch.object(rp, "run_prepare_source", return_value=None):
                bound = rp.prepare_extraction(tmp, "smith-2020", supplied)
            self.assertEqual(bound["extraction"], "")
            self.assertEqual(bound["limitation"], "extraction_failed")


class ReappraisalBindingTests(unittest.TestCase):
    def _bind(self, folder, task):
        task["binding"] = {
            "paper_id": "P1",
            "review_sha256": ra.file_sha256(os.path.join(folder, "review.md")),
            "ledger_sha256": ra.file_sha256(os.path.join(folder, "ledger.md")),
            "summary_sha256": ra.file_sha256(os.path.join(folder, "summary.json")),
            "task_id": "task-1", "prepared_at": "2026-11-21T00:00:00Z"}
        return task

    def _folder(self, tmp):
        review = ("### C1 — ¶1 — (Smith, 2020)\n\n"
                  "**Overall judgment: Supports.**\n\nBody.\n\n"
                  "### C2 — ¶2 — (Jones, 2021)\n\n"
                  "**Overall judgment: Not Judged.**\n\nBody.\n")
        ledger = ("| C1 | ¶1 | m | s | R1 | old | old | old | none |\n"
                  "| C2 | ¶2 | m | s | R2 | old | old | old | none |\n")
        summary = {"occurrences": {"total": 2, "supports": 1, "qualified": 0,
                                   "contradicts": 0, "not_supported": 0,
                                   "not_judged": 1}}
        for name, body in (("review.md", review), ("ledger.md", ledger)):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as handle:
                handle.write(body)
        with open(os.path.join(tmp, "summary.json"), "w", encoding="utf-8") as handle:
            json.dump(summary, handle)
        return review, ledger, summary

    def test_stale_task_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            review, _ledger, _summary = self._folder(tmp)
            task = self._bind(tmp, {"entries": [{"id": "C2", "prior": "Not Judged"}]})
            with open(os.path.join(tmp, "review.md"), "a", encoding="utf-8") as handle:
                handle.write("\nchanged\n")
            judgments = {"changes": [{"id": "C2", "prior": "Not Judged",
                                      "new": "Supports", "evidence": "quote"}]}
            with self.assertRaises(SystemExit) as caught:
                ra.apply_all(tmp, task, judgments, "2026-11-17")
            self.assertIn("stale reappraisal task", str(caught.exception))
            self.assertTrue(open(os.path.join(tmp, "review.md"),
                                 encoding="utf-8").read().endswith("changed\n"))

    def test_amendment_must_name_its_source_member(self):
        review = "### C1 — ¶1\n\n**Overall judgment: Not Judged.**\n\nBody.\n"
        task = {"entries": [{"id": "C1", "prior": "Not Judged"}],
                "sources": {"a": {}, "b": {}}}
        judgments = {"changes": [{"id": "C1", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "quote"}]}
        with self.assertRaises(SystemExit) as caught:
            ra.render_review_text(review, task, judgments, "2026-11-17")
        self.assertIn("affected source member", str(caught.exception))

    def test_interruption_preserves_other_findings_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            review, ledger, summary = self._folder(tmp)
            task = self._bind(tmp, {"entries": [{"id": "C2", "prior": "Not Judged"}]})
            judgments = {"changes": [{"id": "C2", "prior": "Not Judged",
                                      "new": "Supports", "evidence": "quote",
                                      "locator": "printed p. 2"}]}
            def broken(items):
                for index, (temp, path) in enumerate(items):
                    os.replace(temp, path)
                    if index == 0:
                        raise RuntimeError("interrupted")
            with mock.patch.object(ra, "commit_staged", side_effect=broken):
                with self.assertRaises(RuntimeError):
                    ra.apply_all(tmp, task, judgments, "2026-11-17")
            self.assertEqual(open(os.path.join(tmp, "review.md"),
                                  encoding="utf-8").read(), review)
            self.assertEqual(open(os.path.join(tmp, "ledger.md"),
                                  encoding="utf-8").read(), ledger)
            self.assertEqual(json.load(open(os.path.join(tmp, "summary.json"),
                                            encoding="utf-8")), summary)
            ra.apply_all(tmp, task, judgments, "2026-11-17")
            retried = open(os.path.join(tmp, "review.md"), encoding="utf-8").read()
            self.assertIn("**Overall judgment: Supports.**", retried)
            self.assertIn("### C1 — ¶1 — (Smith, 2020)", retried)


class ReappraiseCoverageTests(unittest.TestCase):
    def test_unresolved_copy_cannot_support_a_negative_judgment(self):
        review = ("**C1 — ¶1**\nAssessed statement: “x”.\n**Not Judged.** old\n")
        task = {"entries": [{"id": "C1", "prior": "Not Judged"}],
                "sources": {"source": {"copy_observation": {
                    "status": "unresolved", "reason": "no printed page evidence",
                    "absence_allowed": False}}}}
        judgments = {"changes": [{"id": "C1", "source_key": "source",
                                  "prior": "Not Judged", "new": "Not Supported",
                                  "evidence": "quote", "locator": ""}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(review)
            with self.assertRaises(SystemExit):
                ra.apply_review(path, task, judgments, "2026-11-21")

    def test_later_edition_cannot_support_absence(self):
        review = ("**C1 — ¶1**\nAssessed statement: “x”.\n**Not Judged.** old\n")
        task = {"entries": [{"id": "C1", "prior": "Not Judged"}],
                "sources": {"source": {"copy_observation": {
                    "status": "adequate", "absence_allowed": True,
                    "edition": {"status": "later_edition", "absence_allowed": False}}}}}
        judgments = {"changes": [{"id": "C1", "source_key": "source",
                                  "prior": "Not Judged", "new": "Not Supported",
                                  "evidence": "quote", "locator": ""}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(review)
            with self.assertRaises(SystemExit):
                ra.apply_review(path, task, judgments, "2026-11-21")

    def test_prepare_scope_observes_cited_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "_sources", "key")
            os.makedirs(source)
            with open(os.path.join(source, "pages.csv"), "w", encoding="utf-8") as handle:
                handle.write("pdf_page,printed_folio,source,confidence,chars,words\n")
                for page in range(57, 83):
                    handle.write(f"{page},{page},native,1.0,10,2\n")
            text = os.path.join(source, "text.txt")
            with open(text, "w", encoding="utf-8") as handle:
                handle.write("text\n")
            with open(os.path.join(source, "meta.json"), "w", encoding="utf-8") as handle:
                json.dump({"offset": {"coverage": 1.0}}, handle)
            observation = rp.scope_for(text, [57, 82])
        self.assertEqual(observation["status"], "adequate")
        self.assertTrue(observation["absence_allowed"])


class ReappraiseApplyTests(unittest.TestCase):
    def test_bold_and_heading_sections(self):
        text = ("**C1 — ¶1**\nAssessed statement: “x”.\n**Not Judged.** old\n\n"
                "### C2 — ¶2\n**Overall judgment: Not Judged.**\nold\n")
        self.assertIn("**C1", text[slice(*ra.block_span(text, "C1"))])
        self.assertIn("### C2", text[slice(*ra.block_span(text, "C2"))])

    def test_standalone_judgment_is_replaced(self):
        block = "**C1 — ¶1**\n**Not Judged.** old explanation\n"
        self.assertIn("**Supports.**", ra.set_judgment(block, "Supports"))
        self.assertNotIn("**Not Judged.**", ra.set_judgment(block, "Supports"))

    def test_apply_review_removes_old_bold_assessment(self):
        review = ("**C1 — ¶1**\nAssessed statement: “x”.\n"
                  "**Not Judged.** old explanation that must go\n\n"
                  "**C2 — ¶2**\nAssessed statement: “y”.\n**Supports.** keep\n")
        task = {"entries": [{"id": "C1", "prior": "Not Judged"}]}
        judgments = {"changes": [{"id": "C1", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "quote",
                                  "locator": "printed p. 2"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(review)
            ra.apply_review(path, task, judgments, "2026-11-17")
            text = open(path, encoding="utf-8").read()
        self.assertNotIn("old explanation that must go", text)
        self.assertIn("**Supports.**", text)
        self.assertIn("**Reappraisal (2026-11-17):**", text)

    def test_apply_review_preserves_sections_after_final_entry(self):
        """SF-01: reappraising the last citation must not truncate the review."""
        review = (
            "## 1. Findings, counts, and limitations\n\n"
            "**Citation occurrences.** 2 inventoried. Supports 1; Qualified or Mixed 0; "
            "Contradicts 0; Not Supported 0; Not Judged 1.\n\n"
            "## 2. Numbered citation entries, in paper order\n\n"
            "### C1 — ¶1 — (Smith, 2020)\n\n"
            "**Statement:** “first”.\n\n"
            "**Overall judgment: Supports.**\n\n"
            "Evidence for the first claim.\n\n"
            "### C2 — ¶2 — (Jones, 2021)\n\n"
            "**Statement:** “second”.\n\n"
            "**Overall judgment: Not Judged.**\n\n"
            "Old assessment of the second claim.\n\n"
            "## 3. Bibliography review, in submitted order\n\n"
            "### R1 — Smith 2020\n\n"
            "**Identity:** verified.\n\n"
            "## 4. Uncited substantive passages and other attribution concerns\n\n"
            "None.\n\n"
            "## 5. Unresolved items and audit\n\n"
            "- Audit trail line that must survive.\n")
        summary = {"occurrences": {"total": 2, "supports": 1, "qualified": 0,
                                   "contradicts": 0, "not_supported": 0, "not_judged": 1}}
        task = {"entries": [{"id": "C2", "prior": "Not Judged"}]}
        judgments = {"changes": [{"id": "C2", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "quote",
                                  "locator": "printed p. 2"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(review)
            json.dump(summary, open(os.path.join(tmp, "summary.json"), "w",
                                    encoding="utf-8"))
            ra.apply_review(path, task, judgments, "2026-11-17")
            text = open(path, encoding="utf-8").read()

        self.assertIn("## 3. Bibliography review, in submitted order", text)
        self.assertIn("### R1 — Smith 2020", text)
        self.assertIn("## 5. Unresolved items and audit", text)
        self.assertIn("Audit trail line that must survive.", text)
        self.assertIn("**Citation occurrences.** 2 inventoried. Supports 2;", text)
        self.assertIn("| 2026-11-17 |", text)

    def test_apply_review_keeps_prior_log_rows(self):
        """SF-01: a second reappraisal must append, not replace, the log."""
        review = (
            "## 2. Numbered citation entries, in paper order\n\n"
            "### C1 — ¶1 — (Smith, 2020)\n\n"
            "**Overall judgment: Qualified or Mixed.**\n\n"
            "Body.\n\n"
            "### C2 — ¶2 — (Jones, 2021)\n\n"
            "**Overall judgment: Not Judged.**\n\n"
            "Body.\n\n"
            "## 3. Bibliography review, in submitted order\n\n"
            "### R1 — Smith 2020\n\n"
            "**Identity:** verified.\n\n"
            "## Reappraisal log\n\n"
            "| Date | Source | C/R | Prior judgment | New judgment | Evidence | Locator |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| 2026-10-01 | smith-2020 | C1 | Supports | Qualified or Mixed | earlier | p. 1 |\n")
        summary = {"occurrences": {"total": 2, "supports": 0, "qualified": 1,
                                   "contradicts": 0, "not_supported": 0, "not_judged": 1}}
        task = {"entries": [{"id": "C2", "prior": "Not Judged"}]}
        judgments = {"changes": [{"id": "C2", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "later quote",
                                  "locator": "printed p. 2"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(review)
            json.dump(summary, open(os.path.join(tmp, "summary.json"), "w",
                                    encoding="utf-8"))
            ra.apply_review(path, task, judgments, "2026-11-17")
            text = open(path, encoding="utf-8").read()

        self.assertIn("| 2026-10-01 | smith-2020 | C1 | Supports | Qualified or Mixed | earlier | p. 1 |", text)
        self.assertIn("| 2026-11-17 |", text)
        self.assertIn("## 3. Bibliography review, in submitted order", text)
        self.assertEqual(text.count("| 2026-"), 2)

    def test_apply_review_on_renderer_output_keeps_all_sections(self):
        """SF-01 acceptance: the final citation, in real renderer output."""
        paper = {"paper_id": "P1", "path": "/tmp/a.docx",
                 "word_counts": {"total": 10, "text": 8, "references": 2}}
        findings = {
            "title": "T", "date": "2026-11-17", "status": "complete",
            "summary": {}, "works": [], "uncited": [], "audit": {},
            "occurrences": [
                {"id": "C1", "locator": "¶1", "marker": "(Smith, 2020)",
                 "statement": "first", "judgment": "Supports"},
                {"id": "C2", "locator": "¶2", "marker": "(Jones, 2021)",
                 "statement": "second", "judgment": "Not Judged"}],
            "references": [{"id": "R1", "short": "Smith 2020",
                            "verbatim": "Smith, J. (2020). A work. Journal."}],
        }
        occ_counts = rr.counts(findings)
        review = rr.render_md(paper, findings, occ_counts, {})
        task = {"entries": [{"id": "C2", "prior": "Not Judged"}]}
        judgments = {"changes": [{"id": "C2", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "quote",
                                  "locator": "printed p. 2"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(review)
            json.dump({"occurrences": dict(occ_counts)},
                      open(os.path.join(tmp, "summary.json"), "w", encoding="utf-8"))
            ra.apply_review(path, task, judgments, "2026-11-17")
            text = open(path, encoding="utf-8").read()

        self.assertIn("## 3. Bibliography review, in submitted order", text)
        self.assertIn("### R1 — Smith 2020", text)
        self.assertIn("## 4. Uncited substantive passages", text)
        self.assertIn("## 4. Uncited substantive passages", text)
        self.assertIn("Counts: Category A", text)
        self.assertIn("Occurrences: 2 inventoried (Supports 2;", text)
        self.assertIn("Not Judged 0).", text)

    def test_apply_review_refuses_to_write_a_truncated_review(self):
        """SF-01: a rewrite that loses a section must leave the file untouched."""
        review = ("### C1 — ¶1\n\n**Overall judgment: Not Judged.**\n\nbody\n\n"
                  "## 3. Bibliography review, in submitted order\n\n### R1 — x\n")
        task = {"entries": [{"id": "C1", "prior": "Not Judged"}]}
        judgments = {"changes": [{"id": "C1", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "quote"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(review)
            with mock.patch.object(ra, "block_span", return_value=(0, len(review))):
                with self.assertRaises(SystemExit):
                    ra.apply_review(path, task, judgments, "2026-11-17")
            text = open(path, encoding="utf-8").read()
        self.assertEqual(review, text)

    def test_validate_rewrite_rejects_lost_section(self):
        before = "## 1. A\n\ntext\n\n## 3. B\n"
        after = "## 1. A\n\ntext\n"
        with self.assertRaises(SystemExit):
            ra.validate_rewrite(before, after)

    def test_validate_rewrite_rejects_lost_bold_entry(self):
        before = "**C1 — ¶1**\n\n**Supports.**\n\n**C2 — ¶2**\n\n**Not Judged.**\n"
        after = "**C1 — ¶1**\n\n**Supports.**\n"
        with self.assertRaises(SystemExit):
            ra.validate_rewrite(before, after)

    def test_validate_rewrite_allows_body_changes(self):
        before = "## 1. A\n\n### C1 — x\n\n**Overall judgment: Not Judged.**\n"
        after = "## 1. A\n\n### C1 — x\n\n**Overall judgment: Supports.**\n"
        ra.validate_rewrite(before, after)

    def test_apply_all_updates_review_ledger_and_summary_together(self):
        """SF-01: one pending change, three artifacts, one commit."""
        review = ("## 1. Findings, counts, and limitations\n\n"
                  "**Citation occurrences.** 1 inventoried. Supports 0; Qualified or Mixed 0; "
                  "Contradicts 0; Not Supported 0; Not Judged 1.\n\n"
                  "## 2. Numbered citation entries, in paper order\n\n"
                  "### C2 — ¶2 — (Jones, 2021)\n\n"
                  "**Overall judgment: Not Judged.**\n\n"
                  "Body.\n\n"
                  "## 3. Bibliography review, in submitted order\n\n"
                  "### R1 — Smith 2020\n\n"
                  "**Identity:** verified.\n")
        ledger = ("| C2 | ¶2 | marker | statement | R1 | old | old status | none |\n"
                  "| R1 | entry | identity | link | status | old | searched | C2 | none |\n")
        summary = {"occurrences": {"total": 1, "supports": 0, "qualified": 0,
                                   "contradicts": 0, "not_supported": 0, "not_judged": 1}}
        task = {"entries": [{"id": "C2", "prior": "Not Judged"}]}
        judgments = {"changes": [{"id": "C2", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "quote",
                                  "locator": "printed p. 2"}]}
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (("review.md", review), ("ledger.md", ledger),):
                open(os.path.join(tmp, name), "w", encoding="utf-8").write(body)
            json.dump(summary, open(os.path.join(tmp, "summary.json"), "w",
                                    encoding="utf-8"))
            ra.apply_all(tmp, task, judgments, "2026-11-17")
            text = open(os.path.join(tmp, "review.md"), encoding="utf-8").read()
            ledger_text = open(os.path.join(tmp, "ledger.md"), encoding="utf-8").read()
            data = json.load(open(os.path.join(tmp, "summary.json"), encoding="utf-8"))
            backup = open(os.path.join(tmp, "review.pre-reappraisal.md"),
                          encoding="utf-8").read()
            temps = [n for n in os.listdir(tmp) if ".tmp" in n]

        self.assertIn("**Citation occurrences.** 1 inventoried. Supports 1;", text)
        self.assertIn("| 2026-11-17 |", text)
        self.assertIn("**Reappraised 2026-11-17: Supports**", ledger_text)
        self.assertEqual(data["occurrences"]["supports"], 1)
        self.assertEqual(data["occurrences"]["not_judged"], 0)
        self.assertEqual(backup, review)
        self.assertEqual(temps, [])

    def test_apply_all_writes_nothing_when_a_render_fails(self):
        review = "### C2 — ¶2\n\n**Overall judgment: Not Judged.**\n\nBody.\n"
        ledger = "| C1 | ¶1 | m | s | R1 | old | old | none |\n"
        summary = {"occurrences": {"total": 1, "supports": 0, "qualified": 0,
                                   "contradicts": 0, "not_supported": 0, "not_judged": 1}}
        task = {"entries": [{"id": "C2", "prior": "Not Judged"}]}
        judgments = {"changes": [{"id": "C2", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "quote"}]}
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (("review.md", review), ("ledger.md", ledger),):
                open(os.path.join(tmp, name), "w", encoding="utf-8").write(body)
            json.dump(summary, open(os.path.join(tmp, "summary.json"), "w",
                                    encoding="utf-8"))
            with mock.patch.object(ra, "render_summary_data",
                                   side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    ra.apply_all(tmp, task, judgments, "2026-11-17")
            after = {n: open(os.path.join(tmp, n), encoding="utf-8").read()
                     for n in ("review.md", "ledger.md")}
            after_summary = json.load(open(os.path.join(tmp, "summary.json"),
                                          encoding="utf-8"))
            backup_exists = os.path.exists(os.path.join(tmp, "review.pre-reappraisal.md"))

        self.assertEqual(after["review.md"], review)
        self.assertEqual(after["ledger.md"], ledger)
        self.assertEqual(after_summary, summary)
        self.assertFalse(backup_exists)

    def test_ledger_updates_r_row_and_pending(self):
        ledger = (
            "| C1 | ¶1 | marker | statement | R1 | old | old | old | none |\n"
            "| R1 | entry | identity | link | status | old | searched | C1 | none |\n"
            "\n## 6. Pending items\n\n"
            "| C1 (R1) | not supplied | acquire |\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.md")
            open(path, "w", encoding="utf-8").write(ledger)
            ra.apply_ledger(path, [{"id": "C1", "new": "Supports",
                                    "prior": "Not Judged", "evidence": "quote"}],
                            "2026-11-17")
            text = open(path, encoding="utf-8").read()
        self.assertIn("Supplied copy inspected 2026-11-17", text)
        self.assertIn("Resolved 2026-11-17", text)
        self.assertNotIn("not supplied | acquire", text)

    def test_summary_access_note_is_replaced(self):
        summary = {
            "occurrences": {"total": 1, "supports": 0, "qualified": 0,
                            "contradicts": 0, "not_supported": 0, "not_judged": 1},
            "sources": [{"source_key": "book", "retrieval": "identity_only",
                         "access_notes": "not in supplied folder; no preview"}],
            "misrepresented_sources": [], "nonexistent_sources": [], "flags": {},
        }
        task = {"sources": {"book": {"supplied_file": "/x/book.pdf",
                                     "identity": {"title": "Book", "year": "2021"}}}}
        judgments = {"changes": [{"id": "C1", "prior": "Not Judged",
                                  "new": "Supports", "evidence": "quote"}],
                     "unchanged": [], "notes": ""}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "summary.json")
            json.dump(summary, open(path, "w", encoding="utf-8"))
            ra.apply_summary(path, task, judgments, "2026-11-17")
            data = json.load(open(path, encoding="utf-8"))
        self.assertEqual(data["occurrences"]["supports"], 1)
        self.assertEqual(data["occurrences"]["not_judged"], 0)
        self.assertNotIn("not in supplied", data["sources"][0]["access_notes"])
        self.assertIn("Supplied copy inspected on 2026-11-17", data["sources"][0]["access_notes"])


class MetricsTests(unittest.TestCase):
    USAGE = {"input_tokens": 1000, "cache_read_tokens": 900,
             "output_tokens": 100, "total_tokens": 1100}

    def test_off_peak_and_peak_cost(self):
        off = rm.estimate_cost(self.USAGE, "deepseek-v4-flash",
                               "2026-11-15T19:00:00Z")
        peak = rm.estimate_cost(self.USAGE, "deepseek-v4-flash",
                                "2026-11-17T02:00:00Z")
        self.assertEqual(off["cost_status"], "estimated")
        self.assertAlmostEqual(off["estimated_cost_usd"], 0.0000777)
        self.assertAlmostEqual(peak["estimated_cost_usd"], 0.0001554)
        self.assertEqual(rm.format_cost(peak["estimated_cost_usd"]), "$0.000155")

    def test_peak_windows(self):
        self.assertTrue(rm.is_peak("2026-11-17T02:00:00Z"))
        self.assertFalse(rm.is_peak("2026-11-17T10:00:00Z"))
        self.assertFalse(rm.is_peak("2026-11-15T02:00:00Z"))  # Sunday

    def test_missing_usage_is_not_zero(self):
        result = rm.estimate_cost({}, "deepseek-v4-flash",
                                  "2026-11-17T02:00:00Z")
        self.assertEqual(result["cost_status"], "not_recorded")
        self.assertNotIn("estimated_cost_usd", result)

    def test_duration_format(self):
        self.assertEqual(rm.format_duration(813065), "13m 33s")
        self.assertEqual(rm.format_duration(59_000), "59s")

    def test_block_insert_and_replace(self):
        runs = {"runs": [
            {"phase": "review", "model": "deepseek-v4-flash",
             "duration_ms": 813065, "usage": self.USAGE,
             "started_at": "2026-11-17T02:00:00Z",
             "completed_at": "2026-11-17T02:13:33Z",
             "cost_status": "estimated",
             "estimated_cost_usd": 0.0001554},
            {"phase": "reappraisal", "model": "deepseek-v4-flash",
             "duration_ms": 60_000, "usage": self.USAGE,
             "started_at": "2026-11-17T03:00:00Z",
             "completed_at": "2026-11-17T03:01:00Z",
             "cost_status": "estimated",
             "estimated_cost_usd": 0.0001554},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(
                "# Review\n\n## Summary of academic-integrity findings\n")
            self.assertTrue(rm.update_review_block(path, runs))
            first = open(path, encoding="utf-8").read()
            self.assertTrue(rm.update_review_block(path, runs))
            second = open(path, encoding="utf-8").read()
        self.assertIn("**Review time:**", first)
        self.assertIn("**Reappraisal time:**", first)
        self.assertIn("**Estimated total review cost:**", first)
        self.assertEqual(first.count(rm.MARKER_START), 1)
        self.assertEqual(first.count(rm.MARKER_START), second.count(rm.MARKER_START))
        self.assertLess(first.index("**Review time:**"),
                        first.index("## Summary of academic-integrity findings"))

    def test_summarize_does_not_count_missing_cost_as_zero(self):
        summary = rm.summarize_runs([
            {"phase": "review", "duration_ms": 1000,
             "usage": None, "cost_status": "not_recorded"},
            {"phase": "review", "duration_ms": 2000,
             "usage": self.USAGE, "cost_status": "estimated",
             "estimated_cost_usd": 0.0001554},
        ])
        self.assertEqual(summary["duration_ms"], 3000)
        self.assertEqual(summary["unpriced_runs"], 1)
        self.assertAlmostEqual(summary["cost_usd"], 0.0001554)

    def test_record_metrics_from_metadata(self):
        metadata = {
            "status": "completed",
            "createdAt": "2026-11-17T02:00:00Z",
            "completedAt": "2026-11-17T02:13:33Z",
            "totalDurationMs": 813000,
            "usage": {"inputTokens": 1000, "outputTokens": 100,
                      "cacheReadTokens": 900, "totalTokens": 1100},
        }
        with tempfile.TemporaryDirectory() as tmp:
            meta_path = os.path.join(tmp, "metadata.json")
            json.dump(metadata, open(meta_path, "w", encoding="utf-8"))
            batch = os.path.join(tmp, "batch")
            os.makedirs(os.path.join(batch, "_work", "P1"))
            os.makedirs(os.path.join(batch, "P1"))
            open(os.path.join(batch, "P1", "review.md"), "w",
                 encoding="utf-8").write(
                "# Review\n\n## Summary of academic-integrity findings\n")
            argv = ["record_metrics.py", "--batch", batch, "--paper", "P1",
                    "--phase", "review", "--metadata-path", meta_path]
            with mock.patch.object(sys, "argv", argv):
                with redirect_stdout(io.StringIO()):
                    rec.main()
            sidecar = json.load(open(os.path.join(
                batch, "_work", "P1", "review_metrics.json"), encoding="utf-8"))
            review = open(os.path.join(batch, "P1", "review.md"),
                          encoding="utf-8").read()
        self.assertEqual(sidecar["runs"][0]["phase"], "review")
        self.assertEqual(sidecar["runs"][0]["duration_ms"], 813000)
        self.assertAlmostEqual(sidecar["runs"][0]["estimated_cost_usd"],
                               0.0001554)
        self.assertIn("**Review time:**", review)

    def test_record_metrics_from_dsh_session(self):
        lines = [
            {"type": "session", "version": 4, "id": "session-test",
             "createdAt": 1794964969000},
            {"type": "subagent/descriptor", "seq": 2, "time": 1794964970000,
             "data": {"agentModel": "deepseek-flash"}},
            {"type": "assistant/message", "seq": 3, "time": 1794964990000,
             "data": {"usage": {"inputTokens": 1200, "outputTokens": 200,
                                "cacheReadTokens": 800, "cacheWriteTokens": 0,
                                "totalTokens": 2200}}},
            {"type": "assistant/message", "seq": 4, "time": 1794965090000,
             "data": {"usage": {"inputTokens": 800, "outputTokens": 300,
                                "cacheReadTokens": 400, "cacheWriteTokens": 0,
                                "totalTokens": 1500}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            session = os.path.join(tmp, "session.v4.jsonl")
            with open(session, "w", encoding="utf-8") as handle:
                for event in lines:
                    handle.write(json.dumps(event) + "\n")
            batch = os.path.join(tmp, "batch")
            os.makedirs(os.path.join(batch, "_work", "P1"))
            os.makedirs(os.path.join(batch, "P1"))
            open(os.path.join(batch, "P1", "review.md"), "w",
                 encoding="utf-8").write("# Review\n\n")
            argv = ["record_metrics.py", "--batch", batch, "--paper", "P1",
                    "--phase", "review", "--session", session]
            with mock.patch.object(sys, "argv", argv):
                with redirect_stdout(io.StringIO()):
                    rec.main()
            sidecar = json.load(open(os.path.join(
                batch, "_work", "P1", "review_metrics.json"), encoding="utf-8"))
        run = sidecar["runs"][0]
        self.assertEqual(run["duration_ms"], 121000)
        self.assertEqual(run["usage"]["input_tokens"], 2000)
        self.assertEqual(run["usage"]["output_tokens"], 500)
        self.assertEqual(run["usage"]["total_tokens"], 3700)
        self.assertEqual(run["phase"], "review")

    def test_batch_aggregate(self):
        usage = {"input_tokens": 1000, "output_tokens": 100,
                 "cache_read_tokens": 900, "total_tokens": 1100}
        rows = [
            {"review_metrics": {"runs": [
                {"phase": "review", "duration_ms": 1000, "usage": usage,
                 "started_at": "2026-11-17T02:00:00Z",
                 "completed_at": "2026-11-17T02:00:01Z",
                 "cost_status": "estimated", "estimated_cost_usd": 0.0001554}]}},
            {"review_metrics": {"runs": [
                {"phase": "review", "duration_ms": 2000, "usage": usage,
                 "started_at": "2026-11-17T02:00:02Z",
                 "completed_at": "2026-11-17T02:00:04Z",
                 "cost_status": "estimated", "estimated_cost_usd": 0.0001554},
                {"phase": "reappraisal", "duration_ms": 500,
                 "usage": usage, "cost_status": "estimated",
                 "estimated_cost_usd": 0.0001554}]}},
        ]
        aggregate = br.aggregate_review_metrics(rows)
        self.assertEqual(aggregate["papers_with_review"], 2)
        self.assertEqual(aggregate["review"]["runs"], 2)
        self.assertEqual(aggregate["reappraisal"]["runs"], 1)
        self.assertGreater(aggregate["wall_span_ms"], 0)
        out = []
        br.write_metrics_section(out.append, aggregate)
        text = "\n".join(out)
        self.assertIn("## Batch review metrics", text)
        self.assertIn("Estimated total cost", text)
        empty = []
        br.write_metrics_section(empty.append, br.aggregate_review_metrics([]))
        self.assertIn("No review metrics recorded", "\n".join(empty))


class ReferenceHeadingTests(unittest.TestCase):
    def test_reference_heading_variants(self):
        for text in ("References", "Reference list:", "Reference lists:", "Works Cited",
                     "Works cited", "Bibliography", "List of References",
                     "References Cited"):
            self.assertIsNotNone(ec.REF_HEAD_RE.match(text), text)

    def test_heading_is_inferred_from_unsegmented_entries(self):
        paras = [{"text": "Body paragraph about the case study and its argument."} for _ in range(6)]
        paras += [
            {"text": "Smith, J. (2020). A book about stars. Alder House."},
            {"text": "Jones, A., & Lee, B. (2019). An article. Journal of Film, 4(2), 1-20."},
            {"text": "Brown, C. (2018). Another source. https://example.org/x"},
        ]
        self.assertEqual(ec.reference_heading_index(paras), 6)

    def test_annotated_reference_lists_heading_is_recognised(self):
        paras = [{"text": "Reference lists:"}] + [
            {"text": "Smith, J. (2020). A book about stars. Alder House."},
            {"text": "Jones, A. (2019). An article. Journal of Film, 4(2), 1-20."},
        ]
        self.assertEqual(ec.reference_heading_index(paras), 0)

class RetrievalPortTests(unittest.TestCase):
    OA = {"results": [{"id": "W1", "title": "Heavenly Bodies",
                       "publication_year": 2004, "doi": "https://doi.org/10.1/x",
                       "best_oa_location": {"pdf_url": "https://example.org/oa.pdf"},
                       "open_access": {"is_oa": True}}]}
    SRU_XML = ('''<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
      <srw:records><srw:record><srw:recordData>
        <oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
                   xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Le cinma francais</dc:title>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/btv1b9</dc:identifier>
          <dc:date>1925</dc:date>
        </oai_dc:dc>
      </srw:recordData></srw:record></srw:records>
    </srw:searchRetrieveResponse>''')

    def test_openalex_returns_identity_and_oa_location(self):
        with mock.patch.object(providers, "get_json", return_value=(self.OA, {"text": ""})):
            found = providers.openalex_search("Heavenly Bodies", "Dyer", "2004")
        self.assertTrue(found["found"])
        self.assertEqual(found["doi"], "10.1/x")
        self.assertEqual(found["access_url"], "https://example.org/oa.pdf")
        self.assertTrue(found["is_oa"])

    def test_internet_archive_metadata_finds_text_and_lending(self):
        meta = {"metadata": {"title": "Stars", "date": "2004"},
                "files": [{"name": "stars_djvu.txt", "format": "DjVuTXT"}]}
        with mock.patch.object(providers, "get_json", return_value=(meta, {})):
            record = providers.ia_metadata("stars")
        self.assertTrue(record["text_url"].endswith("stars_djvu.txt"))
        self.assertEqual(record["classification"], "full_text")
        lending = {"metadata": {"title": "Stars", "collection": ["inlibrary"]},
                   "files": [{"name": "x_djvu.txt", "format": "DjVuTXT"}]}
        with mock.patch.object(providers, "get_json", return_value=(lending, {})):
            record = providers.ia_metadata("stars")
        self.assertEqual(record["classification"], "lending_only")

    def test_wayback_finds_the_closest_snapshot(self):
        payload = {"archived_snapshots": {"closest": {"url": "https://web.archive.org/x"}}}
        with mock.patch.object(providers, "get_json", return_value=(payload, {})):
            found = providers.wayback_snapshot("https://example.org/dead")
        self.assertEqual(found["access_url"], "https://web.archive.org/x")

    def test_lantern_returns_catalogue_records(self):
        payload = {"response": {"docs": [{"id": "abc", "title_info_primary_tsi": "Motion Picture News"}]}}
        with mock.patch.object(providers, "get_json", return_value=(payload, {})):
            found = providers.lantern_search("Motion Picture News")
        self.assertTrue(found["found"])
        self.assertEqual(found["records"][0]["access_url"],
                         "https://lantern.mediahist.org/catalog/abc")

    def test_gallica_parses_sru_records(self):
        with mock.patch.object(providers, "fetch",
                               return_value={"text": self.SRU_XML, "limitation": ""}):
            found = providers.gallica_search("cinema francais")
        self.assertTrue(found["found"])
        self.assertEqual(found["records"][0]["ark"], "btv1b9")

    def test_open_fetch_stage_stores_full_text_and_records_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "_work"))
            json.dump({"works": {"k1": {"identity": {"title": "A Book"}}}},
                      open(os.path.join(tmp, "source_registry.json"), "w"))
            long_text = "Hollywood stars and their images. " * 120
            with mock.patch.object(fos.providers, "openalex_search",
                                   return_value={"access_url": "https://example.org/t"}):
                with mock.patch.object(fos.transport, "get_text",
                                       return_value=(long_text, {})):
                    summary = fos.collect(tmp)
            index = json.load(open(os.path.join(tmp, "_work", "open_sources.json")))
            text = open(index["k1"]["text_path"], encoding="utf-8").read()
        self.assertEqual(summary["open_text"], 1)
        self.assertEqual(index["k1"]["classification"], "full_text")
        self.assertIn("Hollywood stars", text)

    def test_task_sources_attach_open_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "_work")
            os.makedirs(work)
            text_path = os.path.join(work, "text.txt")
            open(text_path, "w").write("open text")
            json.dump({"k1": {"text_path": text_path, "provider": "openalex",
                              "access_url": "https://example.org/t"}},
                      open(os.path.join(work, "open_sources.json"), "w"))
            sources = {"R1": {"source_key": "k1"}}
            mt.attach_open_sources(tmp, sources)
        self.assertEqual(sources["R1"]["open_text"], text_path)
        self.assertEqual(sources["R1"]["open_provider"], "openalex")

    def test_provider_pacing_enforces_the_interval(self):
        slept, now = [], [1000.0]

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"SOURCEFIDELITY_CACHE": tmp}), \
                mock.patch.dict(pacing.MIN_INTERVAL, {"openalex": 2.0}):
            pacing.wait("openalex", clock=lambda: now[0], sleep=sleep)
            now[0] += 0.5
            pacing.wait("openalex", clock=lambda: now[0], sleep=sleep)
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 1.5, places=1)


class SharedPacingTests(unittest.TestCase):
    def test_cooldown_written_in_subprocess_is_seen_within_deadline(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"SOURCEFIDELITY_CACHE": tmp}):
            command = ("import sys; sys.path.insert(0, sys.argv[1]); "
                       "import provider_pacing as p; p.observe('openalex', 429, '60')")
            subprocess.run([sys.executable, "-IB", "-c", command, REVIEW], check=True)
            with self.assertRaises(pacing.PacingTimeout):
                pacing.wait("openalex", deadline=time.monotonic() + 0.25)
            self.assertEqual(pacing.wait("crossref", deadline=time.monotonic() + 1), 0)

    def test_curl_publishes_final_retry_after_without_exporting_payload(self):
        def curl(command, **kwargs):
            Path(command[2]).write_text("HTTP/1.1 302 Found\nRetry-After: 120\n\n"
                                        "HTTP/2 429 Too Many Requests\nRetry-After: 6\n\n")
            return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

        shared = rd.provider_pacing
        with mock.patch.object(shared, "wait") as wait, \
                mock.patch.object(shared.subprocess, "run", side_effect=curl), \
                mock.patch.object(shared, "observe") as observe:
            shared.curl_request("https://api.crossref.org/works", ["-L"])
        wait.assert_called_once_with("crossref")
        observe.assert_called_once_with("crossref", 429, "6")


class DoiPrefetchTests(unittest.TestCase):
    def test_openalex_batches_one_filter_and_only_keeps_requested_records(self):
        first, second = "10.1000/first", "10.1000/second"
        payload = {"results": [
            {"doi": f"https://doi.org/{first}", "title": "First"},
            {"doi": "https://doi.org/10.1000/other", "title": "Unrequested"},
        ]}
        with mock.patch.object(rd, "_json", return_value=payload) as request, \
                mock.patch.object(rd, "_key", return_value=""):
            found = rd.openalex_batch([first, second])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[0]).query)
        self.assertEqual(query["filter"], [f"doi:https://doi.org/{first}|https://doi.org/{second}"])
        self.assertEqual(query["per_page"], ["2"])
        self.assertEqual(list(found), [first])

    def test_prefetch_skips_warm_and_leaves_unreturned_doi_for_individual_lookup(self):
        first, second, third = ("10.1000/first", "10.1000/second", "10.1000/third")
        entries = [f"https://doi.org/{doi.upper()}." for doi in (first, second, third, first)]
        first_meta = {"doi": first, "title": "First"}
        second_meta = {"doi": second, "title": "Second"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(rd, "CACHE_ROOT", tmp):
            rd.cache_put("metadata", first, first_meta)
            with mock.patch.object(rd, "crossref_batch", return_value={second: second_meta}) as cr, \
                    mock.patch.object(rd, "openalex_batch", return_value={}) as oa:
                result = rd.batch_metadata(entries)
            self.assertEqual(result, {"crossref": 1, "openalex": 0, "warm": 1})
            cr.assert_called_once_with([second, third], "")
            oa.assert_called_once_with([third])
            self.assertEqual(rd.cache_get("metadata", first), first_meta)
            with mock.patch.object(rd, "crossref_by_doi", return_value={"doi": third}) as individual:
                rd.crossref_by_doi_cached(second)
                rd.crossref_by_doi_cached(third)
            individual.assert_called_once_with(third)
            with mock.patch.object(rd, "crossref_batch") as cr, \
                    mock.patch.object(rd, "openalex_batch") as oa:
                self.assertEqual(rd.batch_metadata(entries)["warm"], 3)
            cr.assert_not_called()
            oa.assert_not_called()


class ResolveDoisTests(unittest.TestCase):
    def test_parse_reference_splits_author_year_and_title(self):
        parsed = rd.parse_reference(
            "Addison, H. (2005). Transcending time: Jean Harlow and Hollywood's "
            "narrative of decline. Journal of Film and Video, 57(4), 32-46.")
        self.assertEqual(parsed["surname"], "Addison")
        self.assertEqual(parsed["year"], "2005")
        self.assertTrue(parsed["title"].startswith("Transcending time"))
        self.assertNotIn("Journal of Film", parsed["title"])

    def test_parse_reference_keeps_a_supplied_doi(self):
        parsed = rd.parse_reference(
            "Wallace, W. T. (1993). The role of actors and actresses in the success "
            "of films: How much is a movie star worth?. Journal of cultural "
            "economics, 1-27. https://doi.org/10.1007/BF00820765")
        self.assertEqual(parsed["doi"], "10.1007/BF00820765")
        self.assertTrue(parsed["title"].endswith("worth?"))

    def test_entry_kind_routes_films_articles_and_books(self):
        self.assertEqual(
            rd.entry_kind("Brown, C. (Director). (1936). Wife vs. Secretary [Film]. Loew's Inc."),
            "film")
        self.assertEqual(
            rd.entry_kind("Addison, H. (2005). Transcending time. Journal of Film and Video, "
                          "57(4), 32-46."), "article")
        parsed = rd.parse_reference("Bret, D. (2014). Jean Harlow: tarnished angel. Aurum.")
        self.assertEqual(rd.entry_kind(parsed["entry"], parsed), "book")

    def test_author_match_folds_record_hyphens(self):
        self.assertTrue(rd.author_match({"surname": "Bailey-Goldschmidt"},
                                        ["Bailey\u2010Goldschmidt, Janice"]))
        self.assertFalse(rd.author_match({"surname": "Bailey-Goldschmidt"},
                                         ["Smith, John"]))
        self.assertIsNone(rd.author_match({"surname": "Bret"}, []))

    def test_year_ok_tolerates_one_year(self):
        self.assertTrue(rd.year_ok({"year": "2005"}, 2005))
        self.assertTrue(rd.year_ok({"year": "2005"}, 2006))
        self.assertFalse(rd.year_ok({"year": "2005"}, 2019))
        self.assertTrue(rd.year_ok({"year": ""}, 2019))

    def test_title_score_ignores_articles_and_stopwords(self):
        self.assertGreater(rd.title_score("The role of actors in films",
                                          "Role of actors in films"), 0.9)
        self.assertLess(rd.title_score("Love done her wrong", "A book review"), 0.2)

    def test_match_record_treats_another_edition_as_the_same_work(self):
        parsed = rd.parse_reference(
            "Dyer, R. (1986). Heavenly bodies: Film stars and society. Macmillan.")
        verdict, evidence = rd.match_record(
            parsed, {"title": "Heavenly Bodies: Film Stars and Society",
                     "authors": ["Dyer"], "year": 2004, "publisher": "Routledge"}, "book")
        self.assertEqual(verdict, "same_work_other_edition")
        self.assertEqual(evidence["year_delta"], 18)

    def test_match_record_conflicts_on_pages_without_losing_identity(self):
        parsed = rd.parse_reference(
            "Wallace, W. T. (1993). The role of actors and actresses in the success of "
            "films: How much is a movie star worth?. Journal of cultural economics, 1-27.")
        verdict, evidence = rd.match_record(
            parsed, {"title": "The role of actors and actresses in the success of films: "
                             "how much is a movie star worth?",
                     "authors": ["Wallace"], "year": 1993,
                     "container": "Journal of Cultural Economics", "page": "210-240"},
            "article")
        self.assertEqual(verdict, "bibliographic_conflict")
        self.assertFalse(evidence["pages"])

    def test_match_record_needs_more_than_a_similar_title(self):
        parsed = rd.parse_reference(
            "Smith, J. (2001). Heavenly bodies: Film stars and society.")
        verdict, _ = rd.match_record(
            parsed, {"title": "Heavenly Bodies: Film Stars and Society",
                     "authors": ["Dyer"], "year": 2004}, "book")
        self.assertEqual(verdict, "possible_match")

    def test_match_record_flags_real_coordinates_under_an_unknown_title(self):
        # The fabrication shape: a real author, journal, volume and pages with a
        # title no record carries. It must not be resolved to the nearest record.
        parsed = rd.parse_reference(
            "Wallace, W. T. (1993). Stardom and the studio accountants. "
            "Journal of cultural economics, 17(1), 1-27.")
        verdict, evidence = rd.match_record(
            parsed, {"title": "The role of actors and actresses in the success of films: "
                             "how much is a movie star worth?",
                     "authors": ["Wallace"], "year": 1993,
                     "container": "Journal of Cultural Economics", "volume": "17",
                     "issue": "1", "page": "1-27"}, "article")
        self.assertEqual(verdict, "title_conflict_at_coordinates")
        self.assertLess(evidence["title"], 0.3)

    def test_match_record_calls_a_wrong_author_a_bibliographic_conflict(self):
        parsed = rd.parse_reference(
            "Smith, J. (1993). The role of actors and actresses in the success of films: "
            "How much is a movie star worth?. Journal of cultural economics, 17(1), 1-27.")
        verdict, _ = rd.match_record(
            parsed, {"title": "The role of actors and actresses in the success of films: "
                             "how much is a movie star worth?",
                     "authors": ["Wallace"], "year": 1993,
                     "container": "Journal of Cultural Economics", "volume": "17",
                     "issue": "1", "page": "1-27"}, "article")
        self.assertEqual(verdict, "bibliographic_conflict")


    def test_page_span_and_occupancy(self):
        self.assertEqual(rd.page_span("1-27"), (1, 27))
        self.assertEqual(rd.page_span("e123"), (123, 123))
        self.assertEqual(rd.page_span("223\u2013235"), (223, 235))
        listing = [{"page": "1-27", "title": "A"}, {"page": "28-40", "title": "B"}]
        self.assertEqual([item["title"] for item in rd.occupancy(listing, (10, 20))], ["A"])
        self.assertEqual(rd.occupancy(listing, (41, 50)), [])

    def test_contents_check_needs_a_journal_and_volume(self):
        parsed = rd.parse_reference(
            "Bret, D. (2014). Jean Harlow: tarnished angel. Aurum.")
        result = rd.contents_check(parsed)
        self.assertEqual(result["status"], "not_applicable")

    def test_contents_check_treats_pages_outside_the_issue_as_absent(self):
        parsed = rd.parse_reference(
            "Wallace, W. T. (1993). Stardom and the studio accountants. "
            "Journal of cultural economics, 17(1), 500-520.")
        listing = [{"page": "1-27", "title": "A", "issue": "1", "doi": "10.1/a"},
                   {"page": "29-61", "title": "B", "issue": "1", "doi": "10.1/b"}]
        with mock.patch.object(rd, "journal_issn", return_value=("1234-5678", "issn")), \
                mock.patch.object(rd, "volume_listing", return_value=listing):
            result = rd.contents_check(parsed)
        self.assertEqual(result["status"], "vacant_out_of_extent")
        self.assertEqual(result["covered"], (1, 61))

    def test_contents_check_names_the_occupant_of_the_cited_pages(self):
        parsed = rd.parse_reference(
            "Wallace, W. T. (1993). Stardom and the studio accountants. "
            "Journal of cultural economics, 17(1), 1-27.")
        listing = [{"page": "1-27", "title": "The role of actors and actresses",
                    "issue": "1", "doi": "10.1/x"}]
        with mock.patch.object(rd, "journal_issn", return_value=("1234-5678", "issn")), \
                mock.patch.object(rd, "volume_listing", return_value=listing):
            result = rd.contents_check(parsed)
        self.assertEqual(result["status"], "occupied")
        self.assertEqual(result["occupants"][0]["doi"], "10.1/x")

    def test_book_catalogue_confirms_a_held_book(self):
        parsed = rd.parse_reference(
            "Dyer, R. (2004). Heavenly bodies: Film stars and society. Routledge.")
        record = {"count": 10, "title": "Heavenly Bodies: Film Stars and Society",
                  "score": 0.9}
        with mock.patch.object(rd, "_probe_k10plus", return_value=(record, "")):
            result = rd.book_affirmative(parsed)
        self.assertEqual(result["status"], "present")
        self.assertEqual(result["routes"][0]["catalogue"], "K10plus")

    def test_book_absence_needs_two_strong_catalogues(self):
        parsed = rd.parse_reference(
            "Doe, J. (2019). A fabricated monograph. Invented Press.")
        zero = ({"count": 0, "title": "", "score": 0.0}, "")
        with mock.patch.object(rd, "_probe_k10plus", return_value=zero), \
                mock.patch.object(rd, "_probe_bnf", return_value=zero), \
                mock.patch.object(rd, "_probe_dnb", return_value=zero), \
                mock.patch.object(rd, "_probe_openlibrary", return_value=(None, "URLError")), \
                mock.patch.object(rd, "_json", side_effect=Exception("429")):
            result = rd.book_affirmative(parsed)
        self.assertEqual(result["status"], "absent")

    def test_unreachable_primary_routes_do_not_establish_absence(self):
        # A host that fails here may answer on another machine, and a
        # secondary catalogue's zero is not proof of absence: the verdict must
        # stay incomplete rather than name a real book nonexistent.
        parsed = rd.parse_reference(
            "Doe, J. (2019). A fabricated monograph. Invented Press.")
        failed = (None, "URLError")
        zero = ({"count": 0, "title": "", "score": 0.0}, "")
        with mock.patch.object(rd, "_probe_k10plus", return_value=failed), \
                mock.patch.object(rd, "_probe_bnf", return_value=failed), \
                mock.patch.object(rd, "_probe_dnb", return_value=failed), \
                mock.patch.object(rd, "_probe_internet_archive", return_value=zero), \
                mock.patch.object(rd, "_probe_cinii", return_value=failed), \
                mock.patch.object(rd, "_probe_googlebooks", return_value=failed), \
                mock.patch.object(rd, "_probe_openlibrary", return_value=(None, "URLError")):
            result = rd.book_affirmative(parsed)
        self.assertEqual(result["status"], "search_incomplete")

    def test_book_catalogue_flags_a_different_title(self):
        parsed = rd.parse_reference(
            "Doe, J. (2019). A fabricated monograph. Routledge.")
        other = ({"count": 3, "title": "Something Else Entirely", "score": 0.1}, "")
        failed = (None, "URLError")
        with mock.patch.object(rd, "_probe_k10plus", return_value=other), \
                mock.patch.object(rd, "_probe_bnf", return_value=failed), \
                mock.patch.object(rd, "_probe_dnb", return_value=failed), \
                mock.patch.object(rd, "_probe_internet_archive", return_value=failed), \
                mock.patch.object(rd, "_probe_cinii", return_value=failed), \
                mock.patch.object(rd, "_probe_googlebooks", return_value=failed), \
                mock.patch.object(rd, "_probe_openlibrary", return_value=other):
            result = rd.book_affirmative(parsed)
        self.assertEqual(result["status"], "present_title_mismatch")

    def test_unpaywall_pdf_urls_read_the_oa_locations(self):
        data = {"oa_locations": [
            {"url_for_pdf": "https://example.org/a.pdf", "url": "https://example.org/a"},
            {"url": "https://example.org/b"}]}
        with mock.patch.object(rd, "_json", return_value=data):
            urls = rd.oa_pdf_urls("10.1000/x", "review@example.org")
        self.assertEqual(urls, ["https://example.org/a.pdf",
                                "https://example.org/a", "https://example.org/b"])

    def test_oa_failure_falls_through_and_duplicate_urls_are_downloaded_once(self):
        unavailable = "https://example.test/unavailable"
        working = "https://example.test/copy.pdf"
        calls = []

        def fetch(url, **_):
            calls.append(url)
            if url != working:
                raise OSError("unavailable")
            return 200, url, {"Content-Type": "application/pdf"}, b"%PDF-1.4"

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(rd, "cache_get", return_value=None), \
                mock.patch.object(rd, "_unpaywall_oa", side_effect=OSError), \
                mock.patch.object(rd, "_openalex_oa", return_value=[unavailable]), \
                mock.patch.object(rd, "_semantic_scholar_oa", return_value=[unavailable, working]), \
                mock.patch.object(rd, "_europepmc_oa") as later, \
                mock.patch.object(rd, "_request", side_effect=fetch), \
                mock.patch.object(rd, "_keep_pdf", side_effect=lambda record, *_: record):
            result = rd._resolve_copy("10.1000/x", tmp)
        self.assertEqual(calls, ["https://doi.org/10.1000/x", unavailable, working])
        self.assertEqual(result["resolver"], "semantic_scholar")
        later.assert_not_called()

    def test_successful_unpaywall_download_skips_other_provider_calls(self):
        url = "https://example.test/copy.pdf"
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(rd, "cache_get", return_value=None), \
                mock.patch.object(rd, "_unpaywall_oa", return_value=[url]), \
                mock.patch.object(rd, "_openalex_oa") as later, \
                mock.patch.object(rd, "_request", side_effect=[
                    OSError("publisher unavailable"),
                    (200, url, {"Content-Type": "application/pdf"}, b"%PDF-1.4")]), \
                mock.patch.object(rd, "_keep_pdf", side_effect=lambda record, *_: record):
            result = rd._resolve_copy("10.1000/x", tmp)
        self.assertEqual(result["resolver"], "unpaywall")
        later.assert_not_called()

    def test_doi_acquisition_uses_shared_policy_and_rejects_a_partial_pdf(self):
        doi_url = "https://doi.org/10.1000/x"
        candidate = "https://example.test/copy.pdf"
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(rd, "cache_get", return_value=None), \
                mock.patch.object(rd, "_throttle"), \
                mock.patch.object(rd, "_oa_pdf_candidates",
                                  return_value=[("openalex", candidate)]), \
                mock.patch.object(rd.public_fetch, "fetch", side_effect=[
                    {"status": 0, "limitation": "peer_refused", "final_url": doi_url},
                    {"status": 200, "limitation": "", "final_url": candidate,
                     "truncated": True, "body": b"%PDF-1.4"}]) as fetch, \
                mock.patch.object(rd, "_keep_pdf") as keep_pdf:
            rd._resolve_copy("10.1000/x", tmp)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [doi_url, candidate])
        for call in fetch.call_args_list:
            self.assertTrue(call.kwargs["include_body"])
            self.assertEqual(call.kwargs["max_bytes"], rd.PDF_MAX_BYTES)
        keep_pdf.assert_not_called()

    def test_publisher_pdf_hint_uses_shared_policy_after_link_refusal(self):
        landing = "https://publisher.test/article"
        hint = "https://publisher.test/download/10.1000/x"
        def result(url, body, content_type):
            return {"status": 200, "limitation": "", "final_url": url,
                    "truncated": False, "content_type": content_type, "body": body}

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(rd, "cache_get", return_value=None), \
                mock.patch.object(rd, "_throttle"), \
                mock.patch.dict(rd.PDF_HINTS, {"publisher.test": "https://publisher.test/download/{doi}"}), \
                mock.patch.object(rd.public_fetch, "fetch", side_effect=[
                    result(landing, b'<a href="/broken.pdf">PDF</a>', "text/html"),
                    {"status": 0, "limitation": "peer_refused", "final_url": landing},
                    result(hint, b"%PDF-1.4", "application/octet-stream")]) as fetch, \
                mock.patch.object(rd, "_keep_pdf", side_effect=lambda record, *_: record):
            copy = rd._resolve_copy("10.1000/x", tmp)
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         ["https://doi.org/10.1000/x", "https://publisher.test/broken.pdf", hint])
        self.assertEqual(copy["final_url"], hint)

    def test_publisher_toc_uses_shared_policy(self):
        url = "https://example.test/issue/2"
        with mock.patch.object(rd, "_throttle"), \
                mock.patch.object(rd.public_fetch, "fetch", return_value={
                    "status": 200, "limitation": "", "final_url": url,
                    "truncated": False, "content_type": "text/html",
                    "body": b'<a href="/article">A research article about film</a>'}) as fetch:
            self.assertEqual(rd.toc_entries(url), [
                {"title": "A research article about film", "url": "https://example.test/article"}])
        self.assertEqual(fetch.call_args.kwargs["max_bytes"], rd.LANDING_MAX_BYTES)

    def test_resolve_copy_still_exists(self):
        self.assertTrue(callable(rd.resolve_copy))

    def test_cql_terms_drops_ordinals_and_punctuation(self):
        self.assertEqual(rd._cql_terms("Heavenly bodies: Film stars and society (2nd ed.)"),
                         "Heavenly bodies Film stars society")

    def test_sru_probe_treats_a_diagnostic_as_unknown(self):
        with mock.patch.object(rd, "_request",
                               return_value=(200, "u", {}, b"<zs:diagnostics/>")):
            count, detail = rd.sru_probe("https://example.invalid/sru", "q")
        self.assertIsNone(count)
        self.assertIn("no record count", detail)


class CopyScopeObservationTests(unittest.TestCase):
    def test_saved_pdf_gets_a_shared_scope_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "article.pdf")
            with open(pdf, "wb") as handle:
                handle.write(b"%PDF-1.4")
            record = {"entry": "Journal, 28(2), 501-510.", "kind": "article",
                      "parsed": {"authors": "Author", "title": "Article"},
                      "copy": {"pdf": pdf, "pages": 10}}
            pages = [{"pdf_page": number, "printed": str(500 + number),
                      "text": "text"} for number in range(1, 11)]
            offset = {"arabic_offset": 0, "coverage": 1.0, "observed": 10,
                      "corroborated": True, "method": "test", "deltas": {}}
            with mock.patch.object(rd.page_map, "read_pages", return_value=pages), \
                    mock.patch.object(rd.page_map, "infer_offset", return_value=offset), \
                    mock.patch.object(rd.page_map, "assign_printed", return_value=None):
                rd.observe_copy_scope(record)
        self.assertEqual(record["copy"]["scope"]["status"], "adequate")


class HyperlinkAuditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        patcher = mock.patch.object(ha.public_fetch, "resolve", return_value={"93.184.216.34"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_hyperlink_fields_are_read_without_a_visible_url(self):
        document = (
            '<?xml version="1.0"?><w:document '
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<w:body>"
            '<w:p><w:hyperlink r:id="rId1"><w:r><w:t>link here</w:t></w:r></w:hyperlink></w:p>'
            '<w:p><w:r><w:instrText> HYPERLINK "https://example.org/field" </w:instrText></w:r>'
            "<w:r><w:t>field link</w:t></w:r></w:p>"
            "</w:body></w:document>")
        relationships = (
            '<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/hyperlink" Target="https://example.org/rel" '
            'TargetMode="External"/></Relationships>')
        path = os.path.join(self.tmp, "field.docx")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document)
            archive.writestr("word/_rels/document.xml.rels", relationships)
        records = ha.paragraph_records(path)
        self.assertEqual(records[0]["links"], ["https://example.org/rel"])
        self.assertEqual(records[1]["links"], ["https://example.org/field"])

    def test_media_classification_excludes_traditional_av(self):
        self.assertEqual(ha.media_type(
            "Brown, C. (Director). (1936). Wife vs. Secretary [Film]."), "traditional")
        self.assertEqual(ha.media_type(
            "Interview with the director. https://www.youtube.com/watch?v=x"), "online")
        self.assertEqual(ha.media_type("Dyer, R. (2004). Heavenly bodies."), "text")
        self.assertEqual(ha.classify_media_link(
            "Brown, C. (Director). (1936). Wife vs. Secretary [Film].", ""), "not-linkable")
        # An online copy of a film is linkable and must resolve.
        self.assertEqual(ha.classify_media_link(
            "[Film]. https://www.youtube.com/watch?v=x",
            "https://www.youtube.com/watch?v=x"), "permanent")

    def test_scope_lines_exclude_non_linkable_media(self):
        report = {"rows": [
            {"id": "R1", "link_class": "not-linkable",
             "fetch": {"state": "not required", "status": None, "detail": ""}},
            {"id": "R2", "link_class": "non-permanent", "fetch": None},
        ]}
        lines = "\n".join(ha.scope_lines(report))
        self.assertIn("Excluded as non-linkable (traditional audio-visual media): 1", lines)
        self.assertIn("1 not permanent, 0 wrong work", lines)
        self.assertNotIn("Hyperlink findings (HL)", lines)

    def test_link_classes_separate_absence_from_permanence(self):
        self.assertEqual(ha.classify_link("https://pan.baidu.com/s/1abc?pwd=1234"),
                         "non-permanent")
        # A full-text link to an online source is the link to the work, even
        # when this machine cannot reach the host.
        self.assertEqual(ha.classify_link("https://lantern.mediahist.org/catalog/x"),
                         "full-text")
        self.assertEqual(ha.classify_link("https://www.worldcat.org/title/1"),
                         "record-only")
        self.assertEqual(ha.classify_link("https://doi.org/10.1007/bf00820765"), "doi")
        self.assertEqual(ha.classify_link("https://www.routledge.com/book/1"), "permanent")
        self.assertEqual(ha.classify_link(""), "absent")

    def test_corporate_authors_start_an_entry(self):
        self.assertTrue(ha.looks_like_entry(
            "Library of Congress, MBRS, & Moving Image Section. (1931). Dressed in Dignity."))
        self.assertTrue(ha.looks_like_entry("Dyer, R. (2004). Heavenly bodies."))
        self.assertFalse(ha.looks_like_entry("see the appendix for the full list"))

    def test_reference_entries_attach_a_url_only_line(self):
        paragraphs = ["Reference:", "Addison, H. (2005). Transcending time.",
                      "https://pan.baidu.com/s/1abc?pwd=1234",
                      "Brown, C. (Director). (1936). Wife vs. Secretary [Film].",
                      "Library of Congress. (1931). Dressed in Dignity. "
                      "https://lantern.mediahist.org/catalog/x"]
        entries = ha.reference_entries(paragraphs)
        self.assertEqual(len(entries), 3)
        self.assertIn("pan.baidu.com", entries[0]["text"])
        self.assertNotIn("pan.baidu.com", entries[1]["text"])
        self.assertIn("lantern.mediahist.org", entries[2]["text"])

    def test_scope_lines_separate_the_counts(self):
        report = {"rows": [
            {"id": "R1", "link_class": "absent", "fetch": None},
            {"id": "R2", "link_class": "non-permanent", "fetch": None},
            {"id": "R3", "link_class": "full-text",
             "fetch": {"state": "unreachable", "status": None, "detail": "reset",
                       "environment": True}},
            {"id": "R4", "link_class": "permanent",
             "fetch": {"state": "ok", "status": 200, "detail": "text/html"}},
        ]}
        lines = "\n".join(ha.scope_lines(report))
        self.assertIn("Hyperlinks absent: 1", lines)
        self.assertIn("not permanent: 1", lines)
        self.assertIn("Full-text links to the work: 1", lines)
        self.assertIn("unreachable 1", lines)
        self.assertIn("unreachable from the review environment: 1", lines)
        self.assertIn("1 absent, 1 not permanent", lines)
        self.assertNotIn("Hyperlink findings (HL)", lines)

    def test_fetch_state_names_the_failure_kind(self):
        with mock.patch.object(ha.public_fetch, "transport",
                               side_effect=urllib.error.HTTPError("u", 404, "no", {}, None)):
            self.assertEqual(ha.fetch_state("https://example.org/x")["state"], "dead")
        with mock.patch.object(ha.public_fetch, "transport",
                               side_effect=urllib.error.URLError("timed out")):
            self.assertEqual(ha.fetch_state("https://example.org/x")["state"], "timeout")
        with mock.patch.object(ha.public_fetch, "transport",
                               side_effect=urllib.error.URLError(
                                   "certificate verify failed: CERTIFICATE_VERIFY_FAILED")):
            self.assertEqual(ha.fetch_state("https://example.org/x")["state"], "tls")
    def test_environment_failures_are_flagged(self):
        with mock.patch.object(ha.public_fetch, "transport",
                               side_effect=urllib.error.URLError("connection reset")):
            self.assertTrue(ha.fetch_state("https://example.org/x")["environment"])
        with mock.patch.object(ha.public_fetch, "transport",
                               side_effect=urllib.error.HTTPError("u", 404, "no", {}, None)):
            self.assertFalse(ha.fetch_state("https://example.org/x")["environment"])

    def test_redirect_to_site_home_is_not_a_verified_article(self):
        response = FetchPolicyTests.Response(
            200, {"Content-Type": "text/html"}, b"<html>home</html>",
            "https://example.org/")
        with mock.patch.object(ha.public_fetch, "transport", return_value=response):
            state = ha.fetch_state("https://example.org/article")
        self.assertEqual(state["state"], "dead")
        self.assertIn("site home", state["detail"])
        self.assertFalse(state["environment"])

    def test_login_page_is_not_counted_as_working(self):
        response = FetchPolicyTests.Response(200, {"Content-Type": "text/html"},
                                             b"<html>please sign in</html>",
                                             "https://example.org/login")
        with mock.patch.object(ha.public_fetch, "transport", return_value=response):
            state = ha.fetch_state("https://example.org/x")
        self.assertEqual(state["state"], "login-page")


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._env = os.environ.get("SOURCEFIDELITY_PRICING")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("SOURCEFIDELITY_PRICING", None)
        else:
            os.environ["SOURCEFIDELITY_PRICING"] = self._env
        self._tmp.cleanup()

    def test_pricing_loader_reads_the_json_table(self):
        path = os.path.join(self.tmp, "pricing.json")
        data = {"checked": "2027-01-01", "stale_after_days": 90,
                "models": {"m1": {"label": "Model One", "windows": [
                    {"effective_from": "2027-01-01", "cache_hit": 0.1,
                     "cache_miss": 0.2, "output": 0.3}]}}}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.environ["SOURCEFIDELITY_PRICING"] = path
        tables = rm.load_pricing()
        self.assertEqual(tables["checked"], "2027-01-01")
        self.assertEqual(tables["stale_after_days"], 90)
        self.assertEqual(tables["models"]["m1"][0]["rates"], (0.1, 0.2, 0.3))

    def test_stale_pricing_is_not_priced(self):
        old = rm.TABLES
        try:
            rm.TABLES = {"checked": "2020-01-01", "stale_after_days": 30,
                         "models": rm.PRICING}
            cost = rm.estimate_cost({"input_tokens": 10, "output_tokens": 10},
                                    "deepseek-v4-flash", "2026-11-19T11:12:45Z")
        finally:
            rm.TABLES = old
        self.assertEqual(cost["cost_status"], "not_recorded")
        self.assertEqual(cost["reason"], "stale_pricing")

    def test_refresh_appends_a_dated_window(self):
        path = os.path.join(self.tmp, "pricing.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"checked": "2026-11-17", "models": {"m1": {
                "label": "Model One",
                "windows": [{"effective_from": "2026-11-17", "cache_hit": 1,
                             "cache_miss": 2, "output": 3}]}}}, handle)
        data = pricing_refresh.append_window(path, "m1", 4, 5, 6, "2027-01-01")
        self.assertEqual(len(data["models"]["m1"]["windows"]), 2)
        self.assertEqual(data["checked"], "2027-01-01")
        self.assertEqual(data["models"]["m1"]["windows"][-1]["effective_from"], "2027-01-01")

    def test_pricing_is_chosen_for_the_run_date(self):
        rates, label, effective = rm.rates_for("deepseek-v4-flash",
                                               "2026-11-19T11:12:45Z")
        self.assertEqual(rates, (0.003, 0.15, 0.60))
        self.assertEqual(label, "DeepSeek V4 Flash")
        self.assertEqual(effective, "2026-11-17")
        self.assertIsNone(rm.rates_for("no-such-model")[0])

    def test_usage_whose_cache_exceeds_the_uncached_prompt_is_priced(self):
        cost = rm.estimate_cost(
            {"input_tokens": 39488, "output_tokens": 40409,
             "cache_read_tokens": 12895744},
            "deepseek-v4-flash", "2026-11-19T11:12:45Z")
        self.assertEqual(cost["cost_status"], "estimated")
        self.assertEqual(cost["cache_miss_tokens"], 39488)
        self.assertGreater(cost["estimated_cost_usd"], 0)

    def test_tokens_line_shows_cached_and_uncached(self):
        block = "\n".join(rm.render_block({"runs": [{
            "phase": "review", "status": "completed",
            "started_at": "2026-11-19T11:12:45Z",
            "duration_ms": 384000,
            "usage": {"input_tokens": 39488, "output_tokens": 40409,
                      "cache_read_tokens": 12895744, "total_tokens": 12975641},
            "cost_status": "estimated", "estimated_cost_usd": 0.0689,
            "model": "deepseek-v4-flash", "window": "off_peak",
        }]}))
        self.assertIn("cached +", block)
        self.assertIn("uncached", block)


class LinkStatesTests(unittest.TestCase):
    def test_block_counts_each_class(self):
        report = {"rows": [
            {"link_class": "not-linkable", "fetch": None},
            {"link_class": "absent", "fetch": None},
            {"link_class": "non-permanent", "fetch": None},
            {"link_class": "full-text",
             "fetch": {"state": "unreachable"}},
            {"link_class": "full-text", "fetch": {"state": "ok"}},
        ]}
        block = ls.block_from_report(report)
        self.assertEqual(block["non_linkable"], 1)
        self.assertEqual(block["absent"], 1)
        self.assertEqual(block["non_permanent"], 1)
        self.assertEqual(block["unreachable"], 1)
        self.assertEqual(block["verified"], 1)
        self.assertNotIn("hl_total", block)

    def test_a_fetched_body_is_kept_as_a_source_copy(self):
        tmp = tempfile.TemporaryDirectory()
        old = ha.SAVE_DIR
        try:
            ha.SAVE_DIR = tmp.name
            path = ha._save_body(b"<html>body</html>", "text/html; charset=utf-8",
                                 "https://example.org/x")
        finally:
            ha.SAVE_DIR = old
        exists = bool(path) and os.path.exists(path)
        tmp.cleanup()
        self.assertTrue(exists)

    def test_refresh_updates_summary_and_scope_line(self):
        tmp = tempfile.TemporaryDirectory()
        folder = tmp.name
        with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as handle:
            json.dump({"paper_id": "P1"}, handle)
        with open(os.path.join(folder, "review.md"), "w", encoding="utf-8") as handle:
            handle.write("## Review scope and counts\n\nHyperlinks missing: 3.\n")
        block = {"absent": 0, "non_linkable": 4, "non_permanent": 10,
                 "record_only": 0, "full_text": 11, "unreachable": 11,
                 "blocked": 0, "dead": 0, "verified": 0, "hl_total": 10}
        changed = ls.refresh(folder, block)
        summary = json.load(open(os.path.join(folder, "summary.json"), encoding="utf-8"))
        review = open(os.path.join(folder, "review.md"), encoding="utf-8").read()
        tmp.cleanup()
        self.assertEqual(summary["hyperlinks"]["hl_total"], 10)
        self.assertIn("HL total: 10", review)
        self.assertNotIn("Hyperlinks missing:", review)
        self.assertEqual(len(changed), 2)


class ProviderTests(unittest.TestCase):
    def test_core_provider_needs_a_key(self):
        with mock.patch.object(rd, "core_api_key", return_value=""):
            self.assertEqual(rd._core_oa("10.1000/x"), [])
        with mock.patch.object(rd, "core_api_key", return_value="k"), \
                mock.patch.object(rd, "_json",
                                  return_value={"results": [{"downloadUrl": "https://x/y.pdf"}]}):
            self.assertEqual(rd._core_oa("10.1000/x"), ["https://x/y.pdf"])

    def test_hathitrust_keeps_only_full_view_items(self):
        parsed = {"isbn": "978-0-415-00000-0", "title": "Heavenly bodies"}
        data = {"items": [
            {"rightsCode": "pd", "itemURL": "https://babel.example/pd"},
            {"rightsCode": "ic", "itemURL": "https://babel.example/ic"}]}
        with mock.patch.object(rd, "_json", return_value=data):
            urls = rd._hathitrust_urls(parsed)
        self.assertEqual(urls, ["https://babel.example/pd"])

    def test_semantic_scholar_sends_bearer_token(self):
        seen = {}

        def fake_json(url, timeout=25, headers=None):
            seen["headers"] = headers
            return {"openAccessPdf": {"url": "https://x/y.pdf"}}

        with mock.patch.object(rd, "_key", return_value="secret"), \
                mock.patch.object(rd, "_json", side_effect=fake_json):
            self.assertEqual(rd._semantic_scholar_oa("10.1000/x"),
                             ["https://x/y.pdf"])
        self.assertEqual(seen["headers"], {"Authorization": "Bearer secret"})


class PublisherPathTests(unittest.TestCase):
    def test_landing_metadata_reads_the_highwire_tags(self):
        html = ('<meta name="citation_title" content="A paper">'
                '<meta name="citation_firstpage" content="1">'
                '<meta name="citation_lastpage" content="27">'
                '<meta name="citation_volume" content="17">'
                '<a href="/toc/x">Issue</a>').encode()
        with mock.patch.object(rd, "_request",
                               return_value=(200, "https://x/y", {}, html)):
            meta = rd.landing_metadata("10.1000/x")
        self.assertEqual(meta["title"], "A paper")
        self.assertEqual(meta["first_page"], "1")
        self.assertEqual(meta["last_page"], "27")
        self.assertEqual(meta["volume"], "17")
        self.assertEqual(meta["toc_url"], "https://x/toc/x")

    def test_toc_entries_skip_navigation_text(self):
        html = ('<a href="/a">Sign in to your account</a>'
                '<a href="/b">The role of actors and actresses in the success of films</a>'
                '<a href="/c">About this journal</a>').encode()
        with mock.patch.object(rd, "_request",
                               return_value=(200, "https://x/y", {}, html)):
            entries = rd.toc_entries("https://x/toc")
        self.assertEqual(entries[0]["url"], "https://x/b")

    def test_extraction_cache_is_shared_across_papers(self):
        with tempfile.TemporaryDirectory() as root:
            paper_a = os.path.join(root, "_work", "P1", "sources")
            paper_b = os.path.join(root, "_work", "P2", "sources")
            os.makedirs(paper_a)
            os.makedirs(paper_b)
            pdf = os.path.join(paper_a, "copy.pdf")
            open(pdf, "wb").write(b"%PDF-1.4 test")
            cache_a = rd.extraction_root(paper_a)
            self.assertEqual(cache_a, os.path.join(root, "_work", "_extracted"))
            self.assertEqual(cache_a, rd.extraction_root(paper_b))
            target = os.path.join(root, "_work", "_extracted")
            digest = os.path.join(target, "x")
            os.makedirs(digest)
            open(os.path.join(digest, "meta.json"), "w").write("{}")
            self.assertIn("_extracted", rd.extraction_root(paper_b))

    def test_extract_pdf_skips_a_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(rd.extract_pdf(os.path.join(folder, "none.pdf"), folder), {})


class SourceCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.old = rd.CACHE_ROOT
        rd.CACHE_ROOT = self.root

    def tearDown(self):
        rd.CACHE_ROOT = self.old
        self._tmp.cleanup()

    def test_identity_cache_skips_provider_routes(self):
        entry = ("Wallace, W. T. (1993). The role of actors in films. "
                 "https://doi.org/10.1000/x")
        with mock.patch.object(rd, "crossref_by_doi_cached",
                               return_value={"title": "The role of actors in films"}), \
                mock.patch.object(rd, "match_record", return_value=("confirmed", {})):
            rd.process(entry, self.root, False, "")
        with mock.patch.object(rd, "crossref_by_doi_cached",
                               side_effect=AssertionError("network used")), \
                mock.patch.object(rd, "crossref_search",
                                  side_effect=AssertionError("network used")):
            record = rd.process(entry, self.root, False, "", check_contents=False)
        self.assertEqual(record["identity"], "confirmed")
        self.assertTrue(any("cache hit" in note for note in record["notes"]))

    def test_a_linked_copy_shares_the_inode(self):
        pdf = os.path.join(self.root, "copy.pdf")
        with open(pdf, "wb") as handle:
            handle.write(b"%PDF-1.4 linked")
        owned = rd._cache_fetch_pdf(pdf)
        self.assertTrue(os.path.samefile(pdf, owned))

    def test_extracted_text_store_respects_its_cap(self):
        base = os.path.join(self.root, "_extracted")
        for index in range(3):
            folder = os.path.join(base, "e%d" % index)
            os.makedirs(folder)
            with open(os.path.join(folder, "text.txt"), "wb") as handle:
                handle.write(b"x" * 1000)
            os.utime(folder, (index, index))
        removed = rd.prune_extracted(max_bytes=1500)
        self.assertGreaterEqual(removed, 1)
        self.assertLessEqual(len(os.listdir(base)), 2)

    def test_a_lock_is_created_and_released(self):
        path = rd._acquire_lock("fetch:10.1000/x")
        self.assertTrue(os.path.exists(path))
        rd._release_lock(path)
        self.assertFalse(os.path.exists(path))

    def test_a_cached_http_response_skips_the_request(self):
        cached = {"state": "ok", "status": 200, "detail": "text/html 20 B (cache)",
                  "saved": "", "environment": False}
        with mock.patch.object(ha, "_cached_state", return_value=cached), \
                mock.patch.object(ha.public_fetch, "resolve", return_value={"93.184.216.34"}), \
                mock.patch.object(ha.public_fetch, "fetch",
                                  side_effect=AssertionError("network used")):
            state = ha.fetch_state("https://x/page")
        self.assertEqual(state["state"], "ok")
        self.assertIn("cache", state["detail"])

    def test_default_cache_root_is_the_batch_work_directory(self):
        batch = os.path.join(self.root, "batch")
        out = os.path.join(batch, "_work", "P1", "sources")
        self.assertEqual(rd.default_cache_root(out), os.path.join(batch, "_work"))

    def test_a_fetched_copy_is_reused_without_a_request(self):
        pdf = os.path.join(self.root, "copy.pdf")
        with open(pdf, "wb") as handle:
            handle.write(b"%PDF-1.4 test")
        rd.cache_put(rd.PDF_CACHE_KIND, "10.1000/x",
                     {"pdf": pdf, "bytes": 12, "final_url": "https://x",
                      "content_type": "application/pdf"})
        with mock.patch.object(rd, "_request",
                               side_effect=AssertionError("network used")):
            result = rd.resolve_copy("10.1000/x", self.root)
        self.assertEqual(result["resolver"], "cache")
        self.assertEqual(result["pdf"], pdf)

    def test_cached_metadata_skips_crossref(self):
        rd.cache_put("metadata", "10.1000/x", {"title": "Cached"})
        with mock.patch.object(rd, "crossref_by_doi",
                               side_effect=AssertionError("network used")):
            meta = rd.crossref_by_doi_cached("10.1000/X", "me@example.org")
        self.assertEqual(meta["title"], "Cached")

    def test_fetched_pdf_is_owned_by_the_cache(self):
        pdf = os.path.join(self.root, "copy.pdf")
        with open(pdf, "wb") as handle:
            handle.write(b"%PDF-1.4 owned")
        owned = rd._cache_fetch_pdf(pdf)
        self.assertTrue(owned.startswith(os.path.join(self.root, "_fetched")))
        self.assertTrue(os.path.exists(owned))
        self.assertEqual(rd._cache_fetch_pdf(owned), owned)

    def test_prune_drops_expired_entries(self):
        rd.cache_put("metadata", "10.1000/x", {"title": "Old"})
        data = rd.cache_load()
        data["metadata"]["10.1000/x"]["stored_at"] = 0
        with open(rd.cache_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        rd.cache_prune()
        self.assertIsNone(rd.cache_get("metadata", "10.1000/x"))

    def test_an_expired_entry_is_ignored(self):
        rd.cache_put("metadata", "10.1000/x", {"title": "Old"})
        data = rd.cache_load()
        data["metadata"]["10.1000/x"]["stored_at"] = 0
        with open(rd.cache_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        self.assertIsNone(rd.cache_get("metadata", "10.1000/x"))


class CacheMaintenanceTests(unittest.TestCase):
    def test_failed_pages_expire_sooner_than_full_text(self):
        import http_cache as hc
        with tempfile.TemporaryDirectory() as root:
            folder = hc.text_store(root, "https://x/blocked",
                                   {"text": "", "status": 403})
            meta_path = os.path.join(folder, "meta.json")
            meta = json.load(open(meta_path, encoding="utf-8"))
            meta["stored_at"] = time.time() - 7200
            json.dump(meta, open(meta_path, "w", encoding="utf-8"))
            text, _meta = hc.text_for(root, "https://x/blocked")
            self.assertEqual(text, "")
            full = hc.text_store(root, "https://x/full", {"text": "word " * 400})
            self.assertNotEqual(hc.text_for(root, "https://x/full")[0], "")
            del full

    def test_prune_removes_expired_text_entries(self):
        import http_cache as hc
        with tempfile.TemporaryDirectory() as root:
            folder = hc.text_store(root, "https://x/old", {"text": "", "status": 404})
            meta_path = os.path.join(folder, "meta.json")
            meta = json.load(open(meta_path, encoding="utf-8"))
            meta["stored_at"] = 0
            json.dump(meta, open(meta_path, "w", encoding="utf-8"))
            result = hc.prune_text_cache(root)
            self.assertGreaterEqual(result["expired"], 1)
            self.assertFalse(os.path.isdir(folder))

    def test_cache_caps_are_settings(self):
        import user_settings
        self.assertEqual(user_settings.get_int("cache_max_pdf_bytes"), 3 * 1024 ** 3)
        with mock.patch.dict(os.environ, {"SOURCEFIDELITY_MAX_PDF_BYTES": "123"}):
            self.assertEqual(user_settings.get_int("cache_max_pdf_bytes"), 123)
            self.assertEqual(rd._max_bytes("cache_max_pdf_bytes", 1), 123)

    def test_http_cache_reads_its_cap_from_settings(self):
        import http_cache
        with mock.patch.dict(os.environ, {"SOURCEFIDELITY_MAX_TEXT_BYTES": "456"}):
            self.assertEqual(http_cache.max_text_bytes(), 456)

    def test_the_fetch_lock_waits_two_minutes(self):
        self.assertEqual(rd._acquire_lock.__defaults__[0], 120.0)

class RetrievalEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.old_root = rd.CACHE_ROOT
        rd.CACHE_ROOT = self.root

    def tearDown(self):
        rd.CACHE_ROOT = self.old_root
        self._tmp.cleanup()

    def _book(self, title="Heavenly bodies: Film stars and society"):
        return rd.parse_reference(f"Dyer, R. (2004). {title}. Routledge.")

    def test_a_held_catalogue_answer_is_not_probed_twice(self):
        parsed = self._book()
        held = {"count": 10, "title": "Heavenly Bodies: Film Stars and Society",
                "score": 0.9}
        calls = []
        with mock.patch.object(rd, "_probe_k10plus",
                               side_effect=lambda p: (calls.append(1), (held, ""))[1]):
            first = rd.book_affirmative(parsed)
            second = rd.book_affirmative(parsed)
        self.assertEqual(first["status"], "present")
        self.assertEqual(second["status"], "present")
        self.assertIn("cached", second["note"])
        self.assertEqual(len(calls), 1)

    def test_a_dead_host_is_not_retried_for_every_reference(self):
        zero = ({"count": 0, "title": "", "score": 0.0}, "")
        with mock.patch.object(rd, "_probe_k10plus", return_value=(None, "TimeoutError")), \
                mock.patch.object(rd, "_probe_bnf", return_value=zero), \
                mock.patch.object(rd, "_probe_dnb", return_value=zero):
            first = rd.book_affirmative(self._book())
        self.assertTrue(any("K10plus" == route["catalogue"] for route in first["routes"]))
        with mock.patch.object(rd, "_probe_k10plus",
                               side_effect=AssertionError("dead host retried")), \
                mock.patch.object(rd, "_probe_bnf", return_value=zero), \
                mock.patch.object(rd, "_probe_dnb", return_value=zero):
            second = rd.book_affirmative(self._book("An entirely different monograph"))
        k10 = [route for route in second["routes"] if route["catalogue"] == "K10plus"]
        self.assertTrue(k10 and "host unavailable" in k10[0]["detail"])

    def test_a_confirmed_identity_stops_the_catalogue_chain(self):
        entry = ("Dyer, R. (2004). Heavenly bodies: Film stars and society. "
                 "Routledge. https://doi.org/10.4324/x")
        meta = {"title": "Heavenly bodies", "year": "2004"}
        with mock.patch.object(rd, "crossref_by_doi_cached", return_value=meta), \
                mock.patch.object(rd, "match_record", return_value=("confirmed", {})), \
                mock.patch.object(rd, "book_affirmative",
                                  side_effect=AssertionError("catalogue chain used")):
            record = rd.process(entry, self.root, False, "", check_contents=True)
        self.assertEqual(record["identity"], "confirmed")

    def test_a_confirmed_doi_stops_title_search_routes(self):
        entry = ("Wallace, W. T. (1993). The role of actors in films. "
                 "Journal of cultural economics, 17(1), 1-27. "
                 "https://doi.org/10.1007/BF00820765")
        with mock.patch.object(rd, "crossref_by_doi_cached",
                               return_value={"title": "The role of actors",
                                             "year": "1993"}), \
                mock.patch.object(rd, "match_record", return_value=("confirmed", {})), \
                mock.patch.object(rd, "landing_metadata", return_value={}), \
                mock.patch.object(rd, "openalex_search",
                                  side_effect=AssertionError("title search used")), \
                mock.patch.object(rd, "crossref_search",
                                  side_effect=AssertionError("title search used")):
            record = rd.process(entry, self.root, False, "", check_contents=False)
        self.assertEqual(record["identity"], "confirmed")

    def test_google_books_is_a_secondary_route_that_can_hold(self):
        parsed = rd.parse_reference(
            "Dyer, R. (2004). Heavenly bodies: Film stars and society. Routledge.")
        failed = (None, "URLError")
        held = ({"count": 2, "title": "Heavenly Bodies", "score": 0.8}, "")
        with mock.patch.object(rd, "_probe_k10plus", return_value=failed), \
                mock.patch.object(rd, "_probe_bnf", return_value=failed), \
                mock.patch.object(rd, "_probe_dnb", return_value=failed), \
                mock.patch.object(rd, "_probe_cinii", return_value=failed), \
                mock.patch.object(rd, "_probe_googlebooks", return_value=held):
            result = rd.book_affirmative(parsed)
        self.assertEqual(result["status"], "present")
        self.assertTrue(any(route["catalogue"] == "Google Books"
                            for route in result["routes"]))

    def test_a_google_books_zero_cannot_prove_absence(self):
        parsed = rd.parse_reference(
            "Doe, J. (2019). A fabricated monograph. Invented Press.")
        failed = (None, "URLError")
        zero = ({"count": 0, "title": "", "score": 0.0}, "")
        with mock.patch.object(rd, "_probe_k10plus", return_value=failed), \
                mock.patch.object(rd, "_probe_bnf", return_value=failed), \
                mock.patch.object(rd, "_probe_dnb", return_value=failed), \
                mock.patch.object(rd, "_probe_cinii", return_value=zero), \
                mock.patch.object(rd, "_probe_googlebooks", return_value=zero), \
                mock.patch.object(rd, "_probe_internet_archive", return_value=zero), \
                mock.patch.object(rd, "_probe_openlibrary", return_value=zero):
            result = rd.book_affirmative(parsed)
        self.assertNotEqual(result["status"], "absent")
        self.assertNotEqual(result["status"], "nonexistent_as_cited")

    def test_a_keyless_install_can_be_simulated(self):
        parsed = rd.parse_reference(
            "Dyer, R. (2004). Heavenly bodies: Film stars and society. Routledge.")
        captured = {}

        def fake_json(url, timeout=25, headers=None):
            captured["url"] = url
            return {"totalItems": 0, "items": []}

        with mock.patch.dict(os.environ, {
                "SOURCEFIDELITY_DISABLE_KEYS": "1",
                "GOOGLE_BOOKS_API_KEY": "env-key",
                "OPENALEX_API_KEY": "env-key",
                "CORE_API_KEY": "env-key",
                "SOURCEFIDELITY_EMAIL": "prof@example.edu"}), \
                mock.patch.object(user_settings, "load",
                                  return_value={"google_books_key": "file-key",
                                                "openalex_api_key": "file-key"}):
            self.assertEqual(rd._key("openalex_api_key"), "")
            self.assertEqual(rd.google_books_key(), "")
            self.assertEqual(rd.core_api_key(), "")
            self.assertEqual(user_settings.get("email"), "prof@example.edu")
            with mock.patch.object(rd, "_json", side_effect=fake_json):
                rd._probe_googlebooks(parsed)
        self.assertNotIn("key=", captured["url"])

    def test_an_http_host_failure_expires_before_a_dead_network(self):
        rd.cache_put("host", "HTTPFlaky", {"reason": "HTTPError"}, ttl=120)
        rd.cache_put("host", "DeadHost", {"reason": "URLError"}, ttl=900)
        with mock.patch.object(rd.time, "time", return_value=rd.time.time() + 130):
            self.assertIsNone(rd.cache_get("host", "HTTPFlaky", ttl=900))
            self.assertEqual(rd.cache_get("host", "DeadHost", ttl=900)["reason"], "URLError")

    def test_legacy_batch_and_resolver_share_same_provider_slot(self):
        now, slept = [1000.0], []

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"SOURCEFIDELITY_CACHE": tmp}), \
                mock.patch.object(rd.time, "time", side_effect=lambda: now[0]), \
                mock.patch.object(rd.time, "sleep", side_effect=sleep):
            rd._throttle("https://api.openalex.org/works?search=x")
            pacing.wait("crossref")  # an unrelated provider has no OpenAlex wait
            pacing.wait("openalex")
        self.assertEqual(slept, [1.0])

    def test_a_discovery_route_holds_on_a_partial_title_match(self):
        parsed = rd.parse_reference(
            "Dyer, R. (2004). Heavenly bodies: Film stars and society. Routledge.")
        failed = (None, "URLError")
        partial = ({"count": 1, "title": "Heavenly Bodies", "score": 0.33}, "")
        with mock.patch.object(rd, "_probe_k10plus", return_value=failed), \
                mock.patch.object(rd, "_probe_bnf", return_value=failed), \
                mock.patch.object(rd, "_probe_dnb", return_value=failed), \
                mock.patch.object(rd, "_probe_cinii", return_value=failed), \
                mock.patch.object(rd, "_probe_googlebooks", return_value=partial):
            result = rd.book_affirmative(parsed)
        self.assertEqual(result["status"], "present")
        self.assertEqual(result["first_title"], "Heavenly Bodies")

    def test_sru_probes_are_one_short_attempt(self):
        seen = {}

        def fake_request(url, **kwargs):
            seen.update(kwargs)
            return 200, url, {}, b"<numberOfRecords>0</numberOfRecords>"

        with mock.patch.object(rd, "_request", side_effect=fake_request):
            count, _title = rd.sru_probe("https://sru.example/x", "q")
        self.assertEqual(count, 0)
        self.assertEqual(seen.get("attempts"), 1)
        self.assertEqual(seen.get("timeout"), 8)

    def test_a_reference_deadline_stops_the_catalogue_chain(self):
        parsed = rd.parse_reference(
            "Doe, J. (2019). A fabricated monograph. Invented Press.")
        with mock.patch.object(rd, "_probe_k10plus",
                               side_effect=AssertionError("catalogue used")):
            result = rd.book_affirmative(parsed, deadline=time.monotonic() - 1)
        self.assertEqual(result["status"], "search_incomplete")
        self.assertTrue(any(route["detail"] == "reference deadline"
                            for route in result["routes"]))

    def test_the_reference_deadline_is_recorded_on_the_record(self):
        entry = "Doe, J. (2019). A fabricated monograph. Invented Press."
        with mock.patch.object(rd, "_reference_budget", return_value=0.001), \
                mock.patch.object(rd, "crossref_search",
                                  side_effect=AssertionError("route used")), \
                mock.patch.object(rd, "book_affirmative",
                                  side_effect=AssertionError("catalogue used")):
            record = rd.process(entry, self._tmp.name, False, "", check_contents=True)
        self.assertIn("reference deadline", " ".join(record.get("notes", [])))

    def test_transport_timeout_and_retry_share_the_reference_budget(self):
        url = "https://example.test/works"
        error = urllib.error.HTTPError(url, 503, "Busy", {}, None)
        token = rd._IDENTITY_DEADLINE.set(101.0)
        try:
            with mock.patch.object(rd.time, "monotonic", return_value=100.0), \
                    mock.patch.object(rd, "_throttle"), \
                    mock.patch.object(rd.urllib.request, "urlopen", side_effect=error) as request, \
                    mock.patch.object(rd.time, "sleep") as sleep:
                with self.assertRaises(rd.IdentityBudgetExhausted):
                    rd._request(url, timeout=10)
                request.assert_called_once()
                self.assertEqual(request.call_args.kwargs["timeout"], 1.0)
                sleep.assert_not_called()
        finally:
            rd._IDENTITY_DEADLINE.reset(token)

    def test_incomplete_identity_retries_but_completed_miss_is_cached(self):
        entry = "Wallace, W. T. (1993). The role of actors in films. Journal, 17(1), 1-27."
        with mock.patch.object(rd, "crossref_search",
                               side_effect=[TimeoutError, []]) as search, \
                mock.patch.object(rd, "openalex_search", return_value=[]):
            results = [rd.process(entry, self.root, False, "") for _ in range(3)]
        self.assertEqual([result["identity"] for result in results],
                         ["search_incomplete", "unlocated_after_search",
                          "unlocated_after_search"])
        self.assertEqual(search.call_count, 2)

    def test_catalogue_deadline_does_not_cache_the_verdict(self):
        parsed = self._book()
        first = rd.book_affirmative(parsed, deadline=time.monotonic() - 1)
        held = {"count": 1, "title": parsed["title"], "score": 1.0}
        with mock.patch.object(rd, "_probe_k10plus", return_value=(held, "")):
            second = rd.book_affirmative(parsed)
        self.assertEqual(first["status"], "search_incomplete")
        self.assertEqual(second["status"], "present")

    def test_distinct_citations_of_same_doi_do_not_share_verdict(self):
        first = "Wallace, W. T. (1993). The role of actors in films. https://doi.org/10.1000/x"
        second = first.replace("1993", "2025")
        with mock.patch.object(rd, "crossref_by_doi_cached",
                               return_value={"title": "The role of actors in films"}), \
                mock.patch.object(rd, "doi_verdict", side_effect=[
                    ("confirmed", {}), ("bibliographic_conflict", {})]) as verdict:
            records = [rd.process(entry, self.root, False, "") for entry in (first, second)]
        self.assertEqual([record["identity"] for record in records],
                         ["confirmed", "bibliographic_conflict"])
        self.assertEqual(verdict.call_count, 2)

    def test_submitted_doi_fallback_preserves_404_and_retrieves_only_replacement(self):
        entry = ("Doe, J. (2020). Evidence travels across archives. "
                 "Archive Journal, 12(1), 5-8. https://doi.org/10.1000/old")
        replacement = {"doi": "10.1000/new", "title": "Evidence travels across archives",
                       "authors": ["Doe"], "year": 2020}
        error = urllib.error.HTTPError("https://example.test", 404, "Missing", {}, None)
        with mock.patch.object(rd, "crossref_by_doi_cached", side_effect=error) as direct, \
                mock.patch.object(rd, "crossref_search", return_value=[replacement]), \
                mock.patch.object(rd, "openalex_search", return_value=[
                    dict(replacement, doi="10.1000/NEW")]), \
                mock.patch.object(rd, "landing_metadata", return_value={}) as landing, \
                mock.patch.object(rd, "resolve_copy", return_value={}) as copy:
            record = rd.process(entry, self.root, True, "")
            warm = rd.process(entry, self.root, False, "")
        self.assertEqual(record["submitted_doi"], "10.1000/old")
        self.assertEqual(record["submitted_doi_check"], {"status": "not_found", "http_status": 404})
        self.assertEqual(record["doi"], "10.1000/new")
        self.assertEqual(record["identity"], "confirmed")
        self.assertEqual(warm["submitted_doi_check"], record["submitted_doi_check"])
        self.assertEqual(warm["doi"], record["doi"])
        direct.assert_called_once_with("10.1000/old")
        landing.assert_called_once_with("10.1000/new", "")
        copy.assert_called_once_with("10.1000/new", self.root, "")

    def test_same_title_with_wrong_author_triggers_doi_fallback(self):
        entry = ("Doe, J. (2020). Evidence travels across archives. "
                 "Archive Journal, 12(1), 5-8. https://doi.org/10.1000/old")
        direct = {"doi": "10.1000/old", "title": "Evidence travels across archives",
                  "authors": ["Other"], "year": 2020}
        replacement = dict(direct, doi="10.1000/new", authors=["Doe"])
        with mock.patch.object(rd, "crossref_by_doi_cached", return_value=direct), \
                mock.patch.object(rd, "crossref_search", return_value=[replacement]), \
                mock.patch.object(rd, "openalex_search", return_value=[]):
            record = rd.process(entry, self.root, False, "")
        self.assertEqual(record["identity"], "confirmed")
        self.assertEqual(record["doi"], "10.1000/new")
        self.assertEqual(record["submitted_doi_check"]["status"], "mismatched")
        self.assertIs(record["submitted_doi_check"]["evidence"]["author"], False)

    def test_competing_replacements_leave_wrong_submitted_doi_unresolved(self):
        entry = ("Doe, J. (2020). Evidence travels across archives. "
                 "Archive Journal, 12(1), 5-8. https://doi.org/10.1000/old")
        wrong = {"doi": "10.1000/old", "title": "Transactions in horticulture",
                 "authors": ["Other"], "year": 2020}
        plausible = {"doi": "10.1000/first", "title": "Evidence travels across archives",
                     "authors": ["Doe"], "year": 2020}
        with mock.patch.object(rd, "crossref_by_doi_cached", return_value=wrong), \
                mock.patch.object(rd, "crossref_search", return_value=[plausible]), \
                mock.patch.object(rd, "openalex_search", return_value=[
                    dict(plausible, doi="10.1000/second")]), \
                mock.patch.object(rd, "landing_metadata") as landing, \
                mock.patch.object(rd, "resolve_copy") as copy:
            record = rd.process(entry, self.root, True, "")
        self.assertEqual(record["identity"], "bibliographic_conflict")
        self.assertEqual(record["doi"], "")
        self.assertIn("multiple matching replacement DOIs", " ".join(record["notes"]))
        landing.assert_not_called()
        copy.assert_not_called()

    def test_doi_transport_failure_with_no_match_is_incomplete(self):
        entry = ("Doe, J. (2020). Evidence travels across archives. "
                 "Archive Journal, 12(1), 5-8. https://doi.org/10.1000/old")
        with mock.patch.object(rd, "crossref_by_doi_cached", side_effect=TimeoutError), \
                mock.patch.object(rd, "crossref_search", return_value=[]), \
                mock.patch.object(rd, "openalex_search", return_value=[]):
            record = rd.process(entry, self.root, False, "")
        self.assertEqual(record["submitted_doi_check"]["status"], "lookup_failed")
        self.assertEqual(record["identity"], "search_incomplete")
        self.assertEqual(record["doi"], "")

    def test_a_keyed_provider_429_is_recorded_without_a_retry_ladder(self):
        calls = []

        def boom(request, timeout):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many",
                                         {}, None)

        url = "https://www.googleapis.com/books/v1/volumes?q=x"
        with mock.patch.object(rd.urllib.request, "urlopen", side_effect=boom), \
                mock.patch.object(rd, "_throttle"), \
                mock.patch.object(rd.provider_pacing, "observe") as observe, \
                mock.patch.object(rd, "google_books_key", return_value="key"), \
                mock.patch.object(rd.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                rd._request(url)
        self.assertEqual(len(calls), 1)
        observe.assert_called_once_with("google_books", 429, "")

    def test_an_unkeyed_provider_still_retries_a_transient_429(self):
        calls = []

        def boom(request, timeout):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many",
                                         {}, None)

        url = "https://api.crossref.org/works?query=x"
        with mock.patch.object(rd.urllib.request, "urlopen", side_effect=boom), \
                mock.patch.object(rd, "_throttle"), \
                mock.patch.object(rd.provider_pacing, "observe") as observe, \
                mock.patch.object(rd.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                rd._request(url)
        self.assertEqual(len(calls), 3)
        self.assertEqual(observe.call_count, 3)

    def test_crossref_search_sends_only_valid_select_fields(self):
        captured = {}

        def fake_json(url, timeout=25, headers=None):
            captured["url"] = url
            return {"message": {"items": []}}

        parsed = rd.parse_reference(
            "Dyer, R. (2004). Heavenly bodies: Film stars and society. Routledge.")
        with mock.patch.object(rd, "_json", side_effect=fake_json):
            rd.crossref_search(parsed)
        self.assertNotIn("edition-number", captured["url"])
        self.assertIn("select=", captured["url"])


class OpenFetchFallbackTests(unittest.TestCase):
    def test_core_unescape_apostrophes(self):
        self.assertEqual(of._core_unescape(r"O\u27Connell"), "O'Connell")
        self.assertEqual(of._core_unescape(r"O\\u27Connell"), "O'Connell")

    def test_core_queries_use_clean_phrase_then_author(self):
        parsed = rd.parse_reference(
            "Presburger, P., & Tyler, M. R. (1990). Television without Frontiers: "
            "Opportunity and Debate Created by the New European Community Directive. "
            "Hastings International and Comparative Law Review, 13, 495-510.")
        queries = of._core_queries(parsed)
        self.assertIn('title:"Television without Frontiers Opportunity and Debate '
                      'Created by the New European Community Directive"', queries)
        self.assertTrue(any("authors:Presburger" in query for query in queries))

    def test_core_queries_strip_colon_and_put_doi_first(self):
        parsed = rd.parse_reference(
            "Nenova, M. B. (2007). The Reform of the European Community Audiovisual "
            "Media Regulation: Television Without Cultural Diversity. "
            "International Journal of Cultural Property, 14(2), 169-204. "
            "doi:10.1017/s0940739107070105")
        queries = of._core_queries(parsed)
        self.assertTrue(queries[0].startswith("doi:10.1017/s0940739107070105"))
        self.assertFalse(any("Media Regulation:" in query for query in queries))

    def test_landing_page_is_not_a_copy(self):
        def fake_download(url, target, timeout=40):
            path = target + ".html"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("<html>landing</html>")
            return "saved", 200, "text/html", path

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(of.fetch_helper, "download", side_effect=fake_download):
                status, _detail, saved = of._fetch(
                    "https://openfetch.test/x", os.path.join(tmp, "x"))
        self.assertEqual(status, "landing")
        self.assertTrue(saved.endswith(".html"))

    def test_core_fulltext_reads_the_output_record(self):
        output = of.CORE_OUTPUTS + "123"

        def fake_json(url, headers=None, timeout=25):
            if url == output:
                return {"fullText": "source text " * 200}
            return {}

        with mock.patch.object(of, "_curl_json", side_effect=fake_json):
            text, route = of._core_fulltext({"id": 123, "fullText": ""}, "key")
        self.assertEqual(route, "outputs/123")
        self.assertIn("source text", text)


class UpdateCheckTests(unittest.TestCase):
    def _installed(self):
        return {"version": "1.0.0", "commit": "abc1234", "channel": "dev"}

    def test_notice_versions(self):
        self.assertEqual(uc.notice("1.0.0", "1.0.0", ""), "")
        self.assertIn("1.2.0 is available", uc.notice("1.0.0", "1.2.0", ""))
        self.assertIn("minimum supported", uc.notice("1.0.0", "1.2.0", "1.2.0"))

    def test_fresh_state_skips_network_and_keeps_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "update.json")
            uc._write_json(state, {"last_check": time.time(),
                                   "latest_version": "1.2.0"})
            buf = io.StringIO()
            with mock.patch.object(uc, "installed", return_value=self._installed()), \
                    mock.patch.object(uc, "fetch_latest",
                                      side_effect=AssertionError("network")), \
                    redirect_stdout(buf):
                uc.main(["--state", state])
        self.assertIn("1.2.0 is available", buf.getvalue())

    def test_stale_state_fetches_once_and_persists_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "update.json")
            eight_days = time.time() - 8 * 86400
            uc._write_json(state, {"last_check": eight_days,
                                   "last_attempt": eight_days})
            fetch = mock.Mock(return_value={"version": "1.2.0"})
            buf = io.StringIO()
            with mock.patch.object(uc, "installed", return_value=self._installed()), \
                    mock.patch.object(uc, "fetch_latest", fetch), \
                    redirect_stdout(buf):
                uc.main(["--state", state])
            self.assertEqual(fetch.call_count, 1)
            self.assertIn("1.2.0 is available", buf.getvalue())
            buf2 = io.StringIO()
            with mock.patch.object(uc, "installed", return_value=self._installed()), \
                    mock.patch.object(uc, "fetch_latest",
                                      side_effect=AssertionError("second run fetched")), \
                    redirect_stdout(buf2):
                uc.main(["--state", state])
            self.assertIn("1.2.0 is available", buf2.getvalue())

    def test_fetch_failure_is_reported_in_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "update.json")
            buf = io.StringIO()
            with mock.patch.object(uc, "installed", return_value=self._installed()), \
                    mock.patch.object(uc, "fetch_latest", return_value={}), \
                    redirect_stdout(buf):
                uc.main(["--check", "--json", "--state", state])
        data = json.loads(buf.getvalue())
        self.assertTrue(data["fetch_failed"])
        self.assertEqual(data["notice"], "")

    def test_stamp_reads_the_installed_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".sourcefidelity"))
            with open(os.path.join(tmp, ".sourcefidelity", "installed.json"),
                      "w", encoding="utf-8-sig") as handle:
                json.dump({"version": "2.3.4", "commit": "abc1234",
                           "channel": "stable"}, handle)
            buf = io.StringIO()
            with mock.patch.object(uc, "ROOT", tmp), redirect_stdout(buf):
                uc.main(["--stamp"])
        self.assertIn("v2.3.4 (commit abc1234)", buf.getvalue())


class DocxFormattingTests(unittest.TestCase):
    def _write_docx(self, directory, body, relationships=""):
        path = os.path.join(directory, "paper.docx")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml",
                             f'<w:document xmlns:w="{dt.W[1:-1]}" xmlns:r="{dt.R[1:-1]}">'
                             f"<w:body>{body}</w:body></w:document>")
            if relationships:
                archive.writestr("word/_rels/document.xml.rels", relationships)
        return path

    def test_docx_dump_preserves_formatting_separators_tables_and_links(self):
        body = (
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:ind w:hanging="720"/>'
            '<w:jc w:val="center"/></w:pPr>'
            '<w:r><w:rPr><w:i/><w:b w:val="0"/></w:rPr><w:t>Journal</w:t><w:tab/></w:r>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>Title</w:t><w:br/></w:r>'
            '<w:hyperlink r:id="rId1"><w:r><w:t>Link</w:t></w:r></w:hyperlink></w:p>'
            '<w:p/><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p>'
            '</w:tc></w:tr></w:tbl>')
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_docx(tmp, body,
                                    '<Relationships><Relationship Id="rId1" '
                                    'Target="https://example.test/article" '
                                    'TargetMode="External"/></Relationships>')
            data = dt.build(path)
        self.assertEqual(data["tables"], 1)
        first, second = data["paragraphs"]
        self.assertEqual((first["id"], second["id"], second["text"]), ("P1", "P2", "Cell"))
        self.assertEqual(first["text"], "Journal\tTitle\nLink")
        self.assertEqual(first["words"], 3)
        self.assertEqual(first["links"], ["https://example.test/article"])
        self.assertEqual((first["style"], first["align"], first["indent"]),
                         ("Heading1", "center", {"hanging": "720"}))
        self.assertEqual(first["runs"][:2], [
            {"text": "Journal\t", "italic": True, "bold": False},
            {"text": "Title\n", "italic": False, "bold": True},
        ])
        self.assertIn("_Journal", dt.markdown(data))

    def test_citation_groups_preserve_mixed_width_punctuation_and_offsets(self):
        marker = "（Hart, 1996；Lee, 2000)"
        paragraph = f"The study reports a rise {marker}. The year alone was （2002）."
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_docx(tmp, f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>")
            result = ec.build(path, "sample")
        citation, = result["citations"]
        self.assertEqual(citation["marker"], marker)
        self.assertEqual(paragraph[citation["start"]:citation["end"]], marker)
        self.assertEqual(citation["parts"], ["Hart, 1996", "Lee, 2000"])

    def test_short_named_assertion_is_an_uncited_candidate(self):
        body = ("<w:p><w:r><w:t>Quiet authority is the central feature of Dana Hart's "
                "screen persona.</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Early publications about Dana Hart and American film "
                "practice.</w:t></w:r></w:p>")
        with tempfile.TemporaryDirectory() as tmp:
            data = ec.build(self._write_docx(tmp, body), "sample")
        self.assertEqual([(row["paragraph_id"], row["signals"])
                          for row in data["uncited_candidates"]], [("P1", ["name"])])


class SourcePackIndexTests(unittest.TestCase):
    def test_edited_collection_chapter_is_not_called_an_article(self):
        chapter = {"text": "Berry, M. (2019). Neoliberalism and the media. In J. Curran & "
                           "D. Hesmondhalgh (Eds.), Media and society (6th ed., pp. 57-82). "
                           "Bloomsbury Academic."}
        self.assertEqual(sp.reference_kind(chapter), "chapter_edited_collection")
        article = {"text": "Nenova, M. (2007). Reform. Journal, 14(2), 169-204."}
        self.assertEqual(sp.reference_kind(article), "article")

    def test_file_pages_and_cited_pages_are_separate_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp.write_index(tmp, [{
                "ref": "R1", "entry": "Berry chapter", "path": "/tmp/berry.pdf",
                "why": "override", "status": "extracted", "dir": "/tmp/berry",
                "printed": "55-82", "cited": "57-82", "scope": "adequate",
                "edition": "same_year_or_unstated"}])
            with open(os.path.join(tmp, "_work", "sourcepack", "index.md"),
                      encoding="utf-8") as handle:
                text = handle.read()
        self.assertIn("File pages", text)
        self.assertIn("Cited", text)
        self.assertIn("55-82", text)
        self.assertIn("57-82", text)


class SourcePackObservationTests(unittest.TestCase):
    def test_observation_records_hash_mapping_and_unknowns(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "_work", "sources", "R1")
            os.makedirs(source)
            with open(os.path.join(source, "meta.json"), "w", encoding="utf-8") as handle:
                json.dump({"source_file": "/tmp/book.pdf", "sha256": "abc123",
                           "first_page_head": "A Book 2018",
                           "pdf_metadata": {"title": "A Book", "author": "A. Author"},
                           "gaps": []}, handle)
            with open(os.path.join(source, "text.txt"), "w", encoding="utf-8") as handle:
                for page in range(57, 83):
                    handle.write(f"=== PDF p{page} | printed p{page} | native | conf 1.0 ===\n")
                    handle.write(f"text of page {page}\n")
            observation = sp._observe(
                {"text": "Author, A. (2018). A Book. In Collection (pp. 57-82)."},
                {"dir": source}, "override")
            self.assertEqual(observation["source_hash"], "abc123")
            self.assertEqual(observation["mapping"], "override")
            self.assertEqual(observation["status"], "adequate")
            self.assertEqual(observation["observed"]["title"], "A Book")
            self.assertTrue(os.path.exists(os.path.join(source, "observation.json")))


class KindCoverageTests(unittest.TestCase):
    def test_kind_routes(self):
        self.assertEqual(kc.kind_for({"publisher": "", "volume": "12", "issue": "2"},
                                      "Journal, 12(2), 1-10."), "article")
        self.assertEqual(kc.kind_for({"publisher": "Routledge", "volume": ""},
                                      "Book. Routledge."), "book")
        self.assertEqual(kc.kind_for({"publisher": "Routledge", "volume": ""},
                                      "Chapter. In A. Editor (Ed.), Book (pp. 1-10)."),
                         "chapter")
        self.assertEqual(kc.kind_for({}, "A report on media."), "report")
        self.assertEqual(kc.kind_for({}, "A film [Film]."), "film")

    def test_article_needs_both_indexes(self):
        only_crossref = kc.assess("article", ["crossref"], [], False)
        self.assertFalse(only_crossref["complete"])
        both = kc.assess("article", ["crossref", "openalex"], [], False)
        self.assertTrue(both["complete"])

    def test_book_needs_a_book_index_and_an_article_index(self):
        missing_book = kc.assess("book", ["crossref", "openalex"], [], False)
        self.assertFalse(missing_book["complete"])
        complete = kc.assess("book", ["openlibrary", "openalex"], [], False)
        self.assertTrue(complete["complete"])

    def test_batch_coverage_uses_attempted_providers(self):
        article = {"work_kind": "article"}
        crossref_only = {"attempts": [{"provider": "crossref", "outcome": "no_results"}]}
        self.assertFalse(ru.coverage_for(article, crossref_only)["complete"])
        both = {"attempts": [{"provider": "crossref", "outcome": "no_results"},
                             {"provider": "openalex", "outcome": "no_results"}]}
        self.assertTrue(ru.coverage_for(article, both)["complete"])
        book = {"work_kind": "monograph"}
        book_routes = {"attempts": [{"provider": "openlibrary", "outcome": "no_results"},
                                    {"provider": "crossref", "outcome": "no_results"}]}
        self.assertTrue(ru.coverage_for(book, book_routes)["complete"])

    def test_required_failure_and_budget_are_incomplete(self):
        failed = kc.assess("article", ["crossref"], ["openalex timeout"], False)
        self.assertFalse(failed["complete"])
        budget = kc.assess("article", ["crossref", "openalex"], [], True)
        self.assertFalse(budget["complete"])


class PrepareTextTests(unittest.TestCase):
    def test_raw_text_becomes_one_labelled_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("The article begins here. A second sentence follows.")
            out = os.path.join(tmp, "extract")
            pt.write_extraction(path, out, "R1")
            text = open(os.path.join(out, "text.txt"), encoding="utf-8").read()
            meta = json.load(open(os.path.join(out, "meta.json"), encoding="utf-8"))
        self.assertIn("The article begins here", text)
        self.assertEqual(meta["extractor_version"], "prepare-text-v1")
        self.assertIn("no printed page", " ".join(meta["gaps"]))


class OnePaperOrchestrationTests(unittest.TestCase):
    def test_packets_are_written_to_each_citation(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_path = os.path.join(tmp, "task.json")
            task = {"candidates": {"citations": [{
                "id": "C1", "marker": "(Smith, 2020)",
                "sentence": "The directive creates a single market."}]},
                "windows": {"C1": [{"window_id": "C1:R1:1", "pdf_page": 3,
                                    "text": "The directive creates a single market. "
                                            "It also sets minimum rules."}]}}
            with open(task_path, "w", encoding="utf-8") as handle:
                json.dump(task, handle)
            result = op.write_packets(task_path)
            updated = json.load(open(task_path, encoding="utf-8"))
        citation = updated["candidates"]["citations"][0]
        self.assertIn("assessment_packet", citation)
        self.assertIn("evidence_selection", citation)
        self.assertEqual(result["with_windows"], 1)

    def test_single_and_batch_reject_surname_only_supplied_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            papers = root / "papers"
            sources = root / "sources"
            papers.mkdir()
            sources.mkdir()
            paper = papers / "paper.docx"
            paragraphs = [
                "A policy account of broadcasting (Hesmondhalgh, 2008).",
                "References",
                "Hesmondhalgh, D. (2008). The media and social theory. Routledge.",
            ]
            body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in paragraphs)
            with zipfile.ZipFile(paper, "w") as archive:
                archive.writestr("word/document.xml",
                                 f'<w:document xmlns:w="{dt.W[1:-1]}"><w:body>'
                                 f"{body}</w:body></w:document>")
            (sources / "other.html").write_text(
                "<html><body><h1>Neoliberalism and broadcasting</h1>"
                "<p>Edited by David Hesmondhalgh</p></body></html>", encoding="utf-8")
            batch_scripts = Path(__file__).parent
            review_scripts = Path(op.__file__).parent

            def run(script, *args):
                result = subprocess.run([sys.executable, "-IB", str(script), *map(str, args)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            single = root / "single"
            batch = root / "batch"
            run(review_scripts / "one_paper.py", "--paper", paper, "--sources", sources,
                "--out", single)
            run(batch_scripts / "prepare_batch.py", "--papers", papers, "--out", batch)
            run(batch_scripts / "source_registry.py", "--batch", batch, "--sources", sources)
            for output in (single, batch):
                registry = json.loads((output / "source_registry.json").read_text(encoding="utf-8"))
                self.assertEqual(len(registry["works"]), 1)
                record = next(iter(registry["works"].values()))
                self.assertEqual(record["identity_status"], "unresolved")
                self.assertFalse(record["supplied_file"])
                self.assertFalse(record["extraction"])


class SingleReviewRenderingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.work = self.root / "_work" / "paper"
        self.work.mkdir(parents=True)
        self.output = self.root / "paper"
        statement = "The rule sets a common standard."
        marker = "(Smith, 2020)"
        self.reference = {"id": "R1", "verbatim": "Smith, A. (2020). Broadcasting."}
        citation = {"id": "X1", "paragraph_id": "P1", "marker": marker}
        candidates = {"paragraphs": [{"id": "P1", "text": f"{statement} {marker}"},
                                     {"id": "P2", "text": self.reference["verbatim"]}],
                      "citations": [citation], "references": [self.reference]}
        evidence = "The rule sets a common standard."
        window = {"window_id": "X1:R1:1", "source_key": "smith", "r_id": "R1", "text": evidence}
        self.write(self.work / "paper.json", {"paper_id": "paper", "path": "paper.docx",
                                              "word_counts": {"total": 20, "text": 12, "references": 8}})
        self.write(self.work / "candidates.json", candidates)
        self.write(self.work / "task.json", {"candidates": candidates, "windows": {"X1": [window]}})
        occurrence = {"id": "C1", **{key: citation[key] for key in ("paragraph_id", "marker")},
                      "statement": statement, "cited_r_ids": ["R1"]}
        self.inventory = {"occurrences": [occurrence], "references": [self.reference],
                          "dispositions": {"X1": "confirmed_as:C1", "R1": "confirmed"},
                          "coverage": {"paragraphs_scanned": ["P1", "P2"]}}
        self.contribution = {"r_id": "R1", "source_key": "smith", "window_id": window["window_id"],
                             "evidence": evidence, "locator": "printed p. 63"}
        self.findings = {"occurrences": [dict(occurrence, judgment="Supports",
                                              sources=[self.contribution])],
                         "references": [dict(self.reference, source_key="smith")]}

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def render(self):
        inv, findings = self.root / "inventory.json", self.root / "findings.json"
        self.write(inv, self.inventory)
        self.write(findings, self.findings)
        return subprocess.run([sys.executable, "-IB", op.__file__, "--out", str(self.root),
                               "--inventory", str(inv), "--findings", str(findings)],
                              capture_output=True, text=True)

    def test_renders_validated_judgments_and_counts(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["occurrences"]["supports"], 1)
        self.assertTrue((self.output / "ledger.md").is_file())
        self.assertTrue((self.output / "summary.json").is_file())
        self.assertIn("printed p. 63", (self.output / "review.md").read_text())

    def test_native_and_batch_link_audits_and_metrics_render_identically(self):
        rows = [{"link_class": "absent"}, {"link_class": "wrong-work"},
                {"link_class": "doi", "fetch": {"state": "dead"}},
                {"link_class": "doi"}]
        self.write(self.root / "_work" / "hyperlink-audit.json", {"rows": rows})
        metrics = {"runs": [
            {"phase": "review", "duration_ms": 1500, "usage": {
                "input_tokens": 20, "output_tokens": 5, "cache_read_tokens": 10}},
            {"phase": "reappraisal", "duration_ms": 9000}]}
        self.write(self.work / "review_metrics.json", metrics)
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["hyperlinks"]["absent"], 1)
        self.assertEqual(summary["hyperlinks"]["wrong_work"], 1)
        self.assertEqual(summary["hyperlinks"]["dead"], 1)
        self.assertEqual(summary["hyperlinks"]["unchecked"], 1)
        self.assertEqual(summary["hyperlinks"]["full_text"], 0)
        self.assertEqual(summary["hyperlinks"]["hl_total"], 0)
        self.assertEqual(summary["references"]["link_states"], summary["hyperlinks"])
        single_review = (self.output / "review.md").read_text()
        self.assertIn(rm.scope_line(metrics), single_review)
        self.assertIn("fetch failures (unreachable 0; blocked 0; dead 1)", single_review)
        self.write(self.work / "hyperlinks.json", {"rows": rows, "hyperlinks": {"hl_total": 9}})
        result = subprocess.run(
            [sys.executable, "-IB", rr.__file__, "--batch", str(self.root), "--paper", "paper",
             "--inventory", str(self.root / "inventory.json"),
             "--findings", str(self.root / "findings.json"), "--strict-inventory"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / "review.md").read_text(), single_review)
        self.assertEqual(json.loads((self.output / "summary.json").read_text()), summary)

    def test_record_metrics_preserves_phases_and_syncs_structured_report(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        command = [sys.executable, "-IB", rec.__file__, "--batch", str(self.root),
                   "--paper", "paper", "--agent-id", "fixture",
                   "--input-tokens", "20", "--output-tokens", "5"]
        for phase, milliseconds in (("review", "2000"), ("reappraisal", "9000")):
            run = subprocess.run(command + ["--phase", phase, "--duration-ms", milliseconds],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads((self.work / "review_metrics.json").read_text())
        self.assertEqual([r["phase"] for r in data["runs"]], ["review", "reappraisal"])
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["review_metrics"], data)
        review = (self.output / "review.md").read_text()
        self.assertEqual(review.count("Review time:"), 1)
        self.assertIn(rm.scope_line(data), review)

    def test_reappraisal_refreshes_observations_without_changing_adjudicated_hl(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        task = {"entries": [{"id": "C1", "prior": "Supports"}]}
        binding = {"review_sha256": ra.file_sha256(self.output / "review.md"),
                   "ledger_sha256": ra.file_sha256(self.output / "ledger.md"),
                   "summary_sha256": ra.file_sha256(self.output / "summary.json")}
        task["binding"] = binding
        observations = {"absent": 2, "wrong_work": 1, "hl_total": 3}
        changes = {"changes": [{"id": "C1", "prior": "Supports", "new": "Not Judged"}]}
        updated = ra.apply_all(self.output, task, changes, "2026-11-22", link_block=observations)
        summary = updated["summary"]
        self.assertEqual(summary["hyperlinks"]["hl_total"], 0)
        self.assertEqual(summary["references"]["link_states"], summary["hyperlinks"])
        review = (self.output / "review.md").read_text()
        self.assertIn("Not Judged 1).", review)
        self.assertIn("1 wrong work", review)
        self.assertIn("HL total: 0", review)

    def test_reappraisal_rollback_preserves_exact_bytes(self):
        self.assertEqual(self.render().returncode, 0)
        paths = [self.output / name for name in ("review.md", "ledger.md", "summary.json")]
        for path in paths:
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        before = [path.read_bytes() for path in paths]

        def interrupt(items):
            for temporary, path in items:
                os.replace(temporary, path)
            raise RuntimeError("interrupted after replacements")

        task = {"entries": [{"id": "C1", "prior": "Supports"}]}
        changes = {"changes": [{"id": "C1", "prior": "Supports", "new": "Not Judged"}]}
        with mock.patch.object(ra, "commit_staged", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                ra.apply_all(self.output, task, changes, "2026-11-22", link_block={"dead": 1})
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_stale_no_change_reappraisal_refuses_to_update_summary(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        summary_path = self.output / "summary.json"
        summary = summary_path.read_bytes()
        task = self.root / "reappraisal.json"
        changes = self.root / "changes.json"
        self.write(task, {"paper_id": "paper", "binding": {"review_sha256": "stale"}})
        self.write(changes, {"paper_id": "paper", "changes": [], "unchanged": ["C1"]})
        result = subprocess.run(
            [sys.executable, "-IB", ra.__file__, "--batch", str(self.root), "--task", str(task),
             "--judgments", str(changes)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale reappraisal task", result.stderr)
        self.assertEqual(summary_path.read_bytes(), summary)

    def test_rejects_invalid_inventory_before_writing(self):
        self.inventory["coverage"]["paragraphs_scanned"].remove("P2")
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("coverage does not list", result.stderr)
        self.assertFalse(self.output.exists())

    def test_rejects_evidence_outside_the_bound_window(self):
        self.contribution["evidence"] = "A sentence absent from this window."
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence is not in window", result.stderr)
        self.assertFalse(self.output.exists())


class ClaimAssessmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.work = self.root / "_work" / "paper"
        self.work.mkdir(parents=True)
        self.quote = "A common rule applies across borders"
        self.claim = "The rule creates a common market."
        statement = f"{self.claim} “{self.quote}”"
        ref = {"id": "R1", "verbatim": "Smith, A. (2020). Broadcasting."}
        marker = "(Smith, 2020)"
        occurrence = {"id": "C1", "paragraph_id": "P1", "marker": marker,
                      "statement": statement, "cited_r_ids": ["R1"]}
        candidates = {"paragraphs": [{"id": "P1", "text": f"{statement} {marker}"},
                                     {"id": "P2", "text": ref["verbatim"]}],
                      "citations": [{"id": "X1", "paragraph_id": "P1", "marker": marker}],
                      "references": [ref]}
        self.text = f"{self.quote}. {self.claim}"
        self.task = {"candidates": candidates, "sources": {"R1": {
            "identity_status": "supplied_confirmed", "source_key": "smith"}},
            "windows": {"X1": [{"window_id": "X1:R1:1", "text": self.text, "printed_page": 5,
                                "source_key": "smith", "r_id": "R1"}]}}
        self.write(self.work / "task.json", self.task)
        self.write(self.work / "candidates.json", candidates)
        self.write(self.work / "paper.json", {"paper_id": "paper", "path": "paper.docx",
                                              "word_counts": {"total": 20, "text": 12, "references": 8}})
        self.write(self.root / "inventory.json", {"occurrences": [occurrence], "references": [ref],
            "dispositions": {"X1": "confirmed_as:C1", "R1": "confirmed"},
            "coverage": {"paragraphs_scanned": ["P1", "P2"]}})
        self.plan = [{"occurrence_id": "C1", "start": 0, "end": len(self.claim), "kind": "claim"},
                     {"occurrence_id": "C1", "start": len(self.claim) + 1,
                      "end": len(statement), "kind": "quotation"}]
        self.write(self.root / "claims.json", self.plan)
        self.command = [sys.executable, "-IB", str(Path(op.__file__).with_name("assess_claims.py")),
                        "--root", str(self.root), "--inventory", str(self.root / "inventory.json"),
                        "--claims", str(self.root / "claims.json")]

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def run_claims(self, *args, status=0):
        run = subprocess.run(self.command + list(args), capture_output=True)
        self.assertEqual(run.returncode, status, run.stderr.decode())
        return run

    def response(self):
        packet = json.loads(self.run_claims("emit", "C1.1").stdout)
        return {"packet_sha256": packet["packet_sha256"], "judgment": "Supports",
                "reason": "The passage explicitly confirms the claim.",
                "evidence": [{"passage_id": "X1:R1:1",
                              "start": self.text.index(self.claim), "end": len(self.text),
                              "contribution": "States the effect of the rule."}]}

    def test_persist_and_render_multiple_claims_with_local_quote_bypass(self):
        response = self.response()
        path = self.root / "response.json"
        self.write(path, response)
        self.run_claims("record", "C1.1", str(path))
        self.assertEqual(json.loads(self.run_claims("emit", "C1.2").stdout)["judgment"], "Supports")
        result = self.run_claims("render")
        self.assertEqual(json.loads(result.stdout)["occurrences"]["supports"], 1)
        report = self.root / "paper" / "review.md"
        first = report.read_bytes()
        self.assertIn(b"printed p. 5", first)
        self.assertIn(b"Exact wording", first)
        self.assertEqual(len(list((self.work / "claim-records").glob("*.json"))), 2)
        self.run_claims("render")
        self.assertEqual(report.read_bytes(), first)

    def test_rejects_invalid_responses_and_abstains_on_stale_record(self):
        response = self.response()
        path = self.root / "response.json"
        self.write(path, response)
        self.run_claims("record", "C1.1", str(path))
        record = self.work / "claim-records" / "C1.1.json"
        original = record.read_bytes()
        for bad in ({**response, "extra": True}, {**response, "reason": "x" * 1700},
                    {**response, "evidence": [dict(response["evidence"][0], passage_id="invented")]},
                    {**response, "evidence": [dict(response["evidence"][0], start=True)]}):
            self.write(path, bad)
            self.run_claims("record", "C1.1", str(path), status=1)
            self.assertEqual(record.read_bytes(), original)
        path.write_bytes(original[:-1] + b',"reason":"duplicate"}')
        self.run_claims("record", "C1.1", str(path), status=1)
        self.task["windows"]["X1"][0]["printed_page"] = 6
        self.write(self.work / "task.json", self.task)
        result = json.loads(self.run_claims("render").stdout)
        self.assertEqual(result["occurrences"]["qualified"], 1)
        self.assertIn("earlier packet", (self.root / "paper" / "review.md").read_text())

    def test_verdict_label_as_reason_is_rejected_on_write_and_replay(self):
        response = {**self.response(), "judgment": "Contradicts", "reason": " Not Judged. "}
        path = self.root / "response.json"
        self.write(path, response)
        result = self.run_claims("record", "C1.1", str(path), status=1)
        self.assertIn(b"reason must explain", result.stderr)
        self.assertFalse((self.work / "claim-records").exists())
        record = self.work / "claim-records" / "C1.1.json"
        record.parent.mkdir()
        self.write(record, response)
        counts = json.loads(self.run_claims("render").stdout)["occurrences"]
        self.assertEqual(counts["contradicts"], 0)
        self.assertEqual(counts["qualified"], 1)

    def test_quote_bypass_refuses_ambiguous_or_unconfirmed_evidence(self):
        window = self.task["windows"]["X1"][0]
        window["text"] += f" {self.quote}"
        self.write(self.work / "task.json", self.task)
        result = json.loads(self.run_claims("emit", "C1.2").stdout)
        self.assertEqual(result["kind"], "quotation")
        self.assertFalse((self.work / "claim-records" / "C1.2.json").exists())
        self.task["sources"]["R1"]["identity_status"] = "supplied_edition_mismatch"
        self.write(self.work / "task.json", self.task)
        self.assertIn(b"No eligible evidence", self.run_claims("emit", "C1.2", status=1).stderr)

    def test_passage_and_request_limits_abstain_without_hiding_other_claims(self):
        hits = self.task["windows"]["X1"]
        hits.extend(dict(hits[0], window_id=f"X1:R1:{n}", text="Other evidence.")
                    for n in range(2, 8))
        self.write(self.work / "task.json", self.task)
        result = self.run_claims("emit", "C1.1")
        self.assertEqual(len(json.loads(result.stdout)["passages"]), 3)
        self.assertLessEqual(len(result.stdout), 4000)
        inventory_path = self.root / "inventory.json"
        inventory = json.loads(inventory_path.read_text())
        long_claim = "界" * 1500
        occurrence = inventory["occurrences"][0]
        occurrence["statement"] = long_claim + occurrence["statement"]
        candidates = self.task["candidates"]
        candidates["paragraphs"][0]["text"] = long_claim + candidates["paragraphs"][0]["text"]
        self.write(inventory_path, inventory)
        self.write(self.work / "candidates.json", candidates)
        quote = self.plan[1]
        self.write(self.root / "claims.json",
                   [dict(self.plan[0], end=len(long_claim)),
                    dict(quote, start=quote["start"] + len(long_claim),
                         end=quote["end"] + len(long_claim))])
        self.assertIn(b"budget", self.run_claims("emit", "C1.1", status=1).stderr)
        self.assertEqual(json.loads(self.run_claims("render").stdout)["occurrences"]["qualified"], 1)


class SharedAssessmentTests(unittest.TestCase):
    def _task(self):
        return {"candidates": {"citations": [{"id": "C1", "marker": "(Smith, 2020)",
                                              "sentence": "The directive creates a single market."}]},
                "windows": {"C1": [{"window_id": "C1:R1:1", "pdf_page": 3,
                                    "source_key": "smith",
                                    "text": "The directive creates a single market. It also sets minimum rules."}]}}

    def test_packet_uses_selected_evidence(self):
        task = self._task()
        packet = core_assessment.citation_packet(task, task["candidates"]["citations"][0])
        self.assertEqual(packet["citation_id"], "C1")
        self.assertIn(packet["selection"]["status"], {"fallback", "selected"})
        self.assertTrue(packet["selection"]["sentences"])

    def test_missing_evidence_or_locator_is_rejected(self):
        errors = core_assessment.judgment_errors(
            {"id": "C1", "judgment": "Supports",
             "sources": [{"evidence": "", "locator": ""}]})
        self.assertTrue(any("evidence" in error for error in errors))
        self.assertTrue(any("locator" in error for error in errors))

    def test_snapshots_and_compare(self):
        left = {"R1": {"supplied_file": "a", "extraction": "x", "representations": []}}
        right = {"R1": {"supplied_file": "a", "extraction": "x", "representations": []}}
        self.assertEqual(core_assessment.compare(left, right), [])
        self.assertEqual(core_assessment.compare(left, {**right, "R2": {}}) != [], True)

class ParityCheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.command = [sys.executable, "-IB", str(Path(__file__).with_name("parity_check.py")),
                        "--left-root", str(self.root / "left"), "--left-paper", "P1",
                        "--right-root", str(self.root / "right"), "--right-paper", "P1"]

    def write_run(self, side, *, copy_hash="copy", representation_hash="acquired", **window):
        root = self.root / side
        task_path = root / "_work" / "P1" / "task.json"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        (root / "source_registry.json").write_text(json.dumps({"works": {"S1": {
            "supplied_file": str(root / "book.pdf"),
            "copy_observation": {"sha256": copy_hash},
            "representations": [{"representation": "pdf", "source_hash": representation_hash}],
        }}}), encoding="utf-8")
        hit = {"source_key": "S1", "window_id": "C1:R1:1", "pdf_page": 5,
               "printed_page": "63", "locator": "p. 63", "start": 10, "end": 20,
               "text": "An excerpt"}
        hit.update(window)
        task_path.write_text(json.dumps({"candidates": {"citations": [{"id": "C1"}]},
                                         "windows": {"C1": [hit]}}), encoding="utf-8")

    def check(self, expected_status):
        result = subprocess.run(self.command, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_status, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_missing_and_invalid_artifacts_fail(self):
        self.assertEqual(len(self.check(1)["artifact_errors"]), 4)
        self.write_run("left")
        self.write_run("right")
        (self.root / "right" / "_work" / "P1" / "task.json").write_text("{}", encoding="utf-8")
        errors = self.check(1)["artifact_errors"]
        self.assertEqual([(error["side"], error["artifact"]) for error in errors],
                         [("right", "task")])

    def test_equivalent_runs_in_separate_directories_pass(self):
        self.write_run("left")
        self.write_run("right")
        self.assertTrue(self.check(0)["parity"])

    def test_different_copy_hashes_fail(self):
        self.write_run("left")
        for change in ({"copy_hash": "other"}, {"representation_hash": "other"}):
            with self.subTest(change=change):
                self.write_run("right", **change)
                self.assertEqual(self.check(1)["source_differences"][0]["key"], "S1")

    def test_same_source_with_different_evidence_fails(self):
        self.write_run("left")
        for change in ({"pdf_page": 6}, {"printed_page": "64"}, {"locator": "p. 64"},
                       {"start": 11}, {"end": 21}, {"text": "A different excerpt"}):
            with self.subTest(change=change):
                self.write_run("right", **change)
                differences = self.check(1)["window_differences"]
                self.assertEqual(differences[0]["key"], "C1")
                self.assertNotIn("An excerpt", json.dumps(differences))


class SharedSourceUnderstandingTests(unittest.TestCase):
    def test_supplied_copy_outranks_acquired_and_web(self):
        record = {"representations": [
            {"representation": "web", "path": "w", "usable": True},
            {"representation": "acquired", "path": "a", "usable": True},
            {"representation": "supplied", "path": "s", "usable": True},
        ]}
        self.assertEqual(core_understanding.choose_representation(record)["path"], "s")

    def test_unusable_copy_is_not_chosen(self):
        record = {"representations": [
            {"representation": "invalid", "path": "bad", "usable": False,
             "limitation": "not_a_pdf"},
            {"representation": "acquired", "path": "good", "usable": True},
        ]}
        self.assertEqual(core_understanding.choose_representation(record)["path"], "good")
        self.assertEqual(core_understanding.representation_limitation(record), "not_a_pdf")

class SharedAcquisitionTests(unittest.TestCase):
    def test_staging_hashes_and_records_one_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "copy.pdf")
            with open(source, "wb") as handle:
                handle.write(b"%PDF-1.4 test")
            entry = core_acquired.stage(tmp, "smith-2020", source,
                                        route="openalex", url="https://example.org/x.pdf")
            self.assertEqual(entry["representation"], "pdf")
            self.assertTrue(entry["path"].startswith(os.path.join(tmp, "_acquired", "smith-2020")))
            self.assertEqual(core_acquired.representations(tmp, "smith-2020")[0]["route"],
                             "openalex")
            self.assertIn(entry["path"], core_acquired.candidate_files(tmp))

    def test_legacy_loose_fetch_is_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "_work", "_fetched_supplied_url")
            os.makedirs(legacy)
            path = os.path.join(legacy, "article.pdf")
            with open(path, "wb") as handle:
                handle.write(b"%PDF-1.4 legacy")
            self.assertIn(path, core_acquired.candidate_files(tmp))

    def test_invalid_download_is_recorded_not_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "challenge.pdf")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("<!DOCTYPE html><title>Just a moment...</title>")
            entry = core_acquired.stage(tmp, "blocked", source, route="open")
            self.assertFalse(entry["usable"])
            self.assertEqual(entry["limitation"], "not_a_pdf")
            self.assertFalse(core_acquired.candidate_files(tmp))

    def test_review_and_batch_import_the_shared_core(self):
        self.assertEqual(sp.core_contracts.CORE_VERSION, "sourcefidelity-core-v1")
        self.assertTrue(hasattr(sr, "core_acquired"))

class PrepareHtmlTests(unittest.TestCase):
    def test_local_html_becomes_a_bounded_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("<html><head><title>Example article</title>"
                             "<style>body{color:red}</style></head><body>"
                             "<script>ignore_this()</script>"
                             "<p>First substantive sentence.</p>"
                             "<p>Second substantive sentence.</p></body></html>")
            out = os.path.join(tmp, "extract")
            result = ph.write_extraction(path, out, "R1")
            text = open(os.path.join(out, "text.txt"), encoding="utf-8").read()
            meta = json.load(open(os.path.join(out, "meta.json"), encoding="utf-8"))
        self.assertEqual(result["status"], "prepared")
        self.assertIn("First substantive sentence", text)
        self.assertNotIn("ignore_this", text)
        self.assertEqual(meta["first_page_head"], "Example article")
        self.assertIn("no printed page numbers", " ".join(meta["gaps"]))

    def test_html_is_an_identity_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "article.html")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("<html><body><p>Identity text here.</p></body></html>")
            self.assertIn(path, sr.source_candidates(tmp))
            self.assertIn("Identity text here", sr.first_pages_text(path))

class OpenTextPageTests(unittest.TestCase):
    def test_running_heads_become_page_blocks(self):
        text = ("front matter\n502 [Vol. 28:501\n"
                "CULTURAL SURVIVAL AND THE EUROPEAN UNION\n"
                "ness.7 Since the early days of television.\n"
                "50319961\nCASE W. RES. J. INT'L L.\nnext page text\n")
        pages = otp.split_pages(text, start=501)
        by_printed = {page["printed"]: page for page in pages}
        self.assertIn("502", by_printed)
        self.assertIn("503", by_printed)
        self.assertIn("Since the early days", "\n".join(by_printed["502"]["lines"]))
        self.assertIn("next page text", "\n".join(by_printed["503"]["lines"]))

    def test_no_running_heads_stays_one_page(self):
        pages = otp.split_pages("one undifferentiated blob", start=57)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["printed"], "57")

    def test_writes_text_and_page_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = otp.write_extraction(
                "502 [Vol. 28:501\npassage here\n50319961\nnext page\n",
                tmp, start=501)
            self.assertIn("502", result["printed"])
            self.assertTrue(os.path.exists(os.path.join(tmp, "text.txt")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "pages.csv")))
            self.assertIn("PDF p2", open(os.path.join(tmp, "text.txt"),
                                          encoding="utf-8").read())


class EvidenceSelectionTests(unittest.TestCase):
    def _passages(self):
        return [{"passage_id": "w1", "page_index": 1,
                 "text": "The directive creates a single market. "
                         "It also sets a minimum level of partial harmonization."}]

    def test_numbered_request_has_exact_sentence_text(self):
        request = ev.prepare_request("The directive creates a single market",
                                     self._passages())
        self.assertEqual(list(request.sentences), ["s1", "s2"])
        self.assertIn("single market", request.sentences["s1"].text)
        self.assertIn("minimum level", request.sentences["s2"].text)

    def test_selected_response_binds_ids_and_reasons(self):
        request = ev.prepare_request("The directive creates a single market",
                                     self._passages())
        chosen = ev.bind_response({"selections": [
            {"sentence_id": "s2", "reason": "qualifies_or_contradicts"}]}, request)
        self.assertEqual(chosen[0][0].alias, "s2")
        self.assertEqual(chosen[0][1], "qualifies_or_contradicts")

    def test_unknown_or_duplicate_ids_reject_the_response(self):
        request = ev.prepare_request("claim text", self._passages())
        for raw in (
                {"selections": [{"sentence_id": "s9", "reason": "bears_on_statement"}]},
                {"selections": [{"sentence_id": "s1", "reason": "bears_on_statement"},
                                {"sentence_id": "s1", "reason": "necessary_context"}]}):
            with self.assertRaises(ev.SelectionResponseInvalid):
                ev.bind_response(raw, request)

    def test_lexical_fallback_selects_shared_terms(self):
        result = ev.select_evidence("minimum harmonization", self._passages())
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["sentences"][0]["sentence_id"], "s2")

    def test_model_call_and_invalid_fallback(self):
        def call(_system, _user):
            return '{"selections": [{"sentence_id": "s1", "reason": "bears_on_statement"}]}'
        result = ev.select_evidence("single market", self._passages(), call=call)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["sentences"][0]["sentence_id"], "s1")

        def broken(_system, _user):
            return "not json"
        fallback = ev.select_evidence("minimum harmonization", self._passages(), call=broken)
        self.assertEqual(fallback["status"], "fallback")
        self.assertTrue(fallback["limitations"])

    def test_file_requests_round_trip_to_bound_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_path = os.path.join(tmp, "task.json")
            task = {"citations": [{"id": "C1", "marker": "(Smith, 2020)",
                                   "sentence": "The directive creates a single market."}],
                    "windows": {"C1": [{"window_id": "C1:R1:1", "pdf_page": 4,
                                        "text": "The directive creates a single market. "
                                                "It also sets minimum rules."}]}}
            with open(task_path, "w", encoding="utf-8") as handle:
                json.dump(task, handle)
            request_dir = os.path.join(tmp, "requests")
            self.assertEqual(len(ev.emit_requests(task_path, request_dir)), 1)
            self.assertTrue(os.path.exists(os.path.join(request_dir, "C1.request.json")))
            with open(os.path.join(request_dir, "C1.response.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"selections": [{"sentence_id": "s1",
                                           "reason": "bears_on_statement"}]}, handle)
            result = ev.bind_responses(task_path, request_dir)
            self.assertEqual(result["bound"], ["C1"])
            updated = json.load(open(task_path, encoding="utf-8"))
            selection = updated["citations"][0]["evidence_selection"]
            self.assertEqual(selection["status"], "selected")
            self.assertIn("single market", selection["sentences"][0]["text"])

    def test_invalid_host_response_is_reported_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_path = os.path.join(tmp, "task.json")
            task = {"citations": [{"id": "C1", "sentence": "A claim.",
                                   "marker": "(Smith, 2020)"}],
                    "windows": {"C1": [{"window_id": "w1", "pdf_page": 1,
                                        "text": "A claim. Another sentence here."}]}}
            with open(task_path, "w", encoding="utf-8") as handle:
                json.dump(task, handle)
            request_dir = os.path.join(tmp, "requests")
            ev.emit_requests(task_path, request_dir)
            with open(os.path.join(request_dir, "C1.response.json"), "w",
                      encoding="utf-8") as handle:
                handle.write('{"selections": [{"sentence_id": "s9", '
                             '"reason": "bears_on_statement"}]}')
            result = ev.bind_responses(task_path, request_dir)
            self.assertFalse(result["bound"])
            self.assertEqual(result["errors"][0]["error"], "SelectionResponseInvalid")

    def test_lexical_fallback_ignores_generic_questions(self):
        result = ev.select_evidence("media policy", [{
            "passage_id": "w1", "page_index": 1,
            "text": "What is media policy? What are its general purposes?"}])
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["sentences"], [])

    def test_no_sentences_is_empty_not_absence(self):
        result = ev.select_evidence("claim", [])
        self.assertEqual(result["status"], "empty")
        self.assertIn("not absence", " ".join(result["limitations"]))


class StageReceiptTests(unittest.TestCase):
    def _batch(self, tmp):
        work = os.path.join(tmp, "_work", "P1")
        os.makedirs(work)
        extraction_dir = os.path.join(tmp, "_sources", "k")
        os.makedirs(extraction_dir)
        with open(os.path.join(extraction_dir, "meta.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"ocr_pages_count": 2}, handle)
        registry = {"works": {
            "R1": {"source_key": "k",
                   "extraction": os.path.join(extraction_dir, "text.txt"),
                   "extraction_version": "prepare-source-v2",
                   "search": {"cached": False, "attempts": [
                       {"provider": "crossref", "outcome": "returned"},
                       {"provider": "crossref", "outcome": "provider_failure"}]}},
            "R2": {"source_key": "k2", "search": {"cached": True,
                                                   "attempts": [
                                                       {"provider": "openalex",
                                                        "outcome": "no_results"}]}},
        }}
        with open(os.path.join(tmp, "source_registry.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(registry, handle)
        task = {"sources": {"R1": {}, "R2": {}},
                "windows": {"C1": [{"query": "SECRET QUOTE"}]},
                "citations": [{"id": "C1", "statement": "SECRET QUOTE"}]}
        with open(os.path.join(work, "task.json"), "w", encoding="utf-8") as handle:
            json.dump(task, handle)
        with open(os.path.join(work, "review_metrics.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"runs": [{"duration_ms": 1200}]}, handle)

    def test_receipt_counts_without_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._batch(tmp)
            receipt = stg.collect(tmp)
        stages = receipt["stages"]
        self.assertEqual(stages["identity_search"]["requests"], 3)
        self.assertEqual(stages["identity_search"]["retries"], 2)
        self.assertEqual(stages["identity_search"]["reuse"], 1)
        self.assertEqual(stages["retrieval"]["windows"], 1)
        self.assertEqual(stages["review"]["latency_ms"], 1200)
        self.assertEqual(stages["extraction"]["ocr_pages"], 2)
        self.assertTrue(receipt["text_free"])
        self.assertFalse(receipt["evidence_quality_included"])
        self.assertNotIn("SECRET QUOTE", json.dumps(receipt))

    def test_offline_replay_verifies_and_detects_changed_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._batch(tmp)
            receipt = stg.collect(tmp)
            self.assertEqual(stg.verify(tmp, receipt), [])
            task_path = os.path.join(tmp, "_work", "P1", "task.json")
            data = json.load(open(task_path, encoding="utf-8"))
            data["windows"] = {"C1": [{"query": "changed"}]}
            with open(task_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            problems = stg.verify(tmp, receipt)
        self.assertTrue(problems)
        self.assertIn("input hashes changed", " ".join(problems))


class FindingCountsTests(unittest.TestCase):
    def _findings(self):
        return {
            "occurrences": [
                {"id": "C1", "summary_category": "A",
                 "academic_practice": ["missing quotation locator"]},
                {"id": "C2", "summary_category": "A", "apa": ["italics"]},
            ],
            "references": [{"id": "R1", "category": "HL",
                            "verbatim": "Smith (2020). Work."}],
            "summary": {"category_a": ["missing quotation locator", "italics"],
                        "hyperlinks_missing": [{"work": "R1", "reason": "no link"}]},
        }

    def test_counts_come_from_evidenced_ids(self):
        counts = fc.issue_counts(self._findings())
        self.assertEqual(sorted(counts["A"]["ids"]), ["C1", "C2"])
        self.assertEqual(counts["A"]["issues"], {"italics": 1,
                                                 "missing quotation locator": 1})
        self.assertEqual(counts["HL"]["ids"], ["R1"])
        self.assertEqual(fc.render_counts(self._findings()),
                         {"A": 2, "B": 0, "HL": 1})
        self.assertEqual(fc.reconcile(self._findings()), [])

    def test_summary_count_mismatch_is_reported(self):
        findings = self._findings()
        findings["summary"]["category_a"] = ["one issue"]
        messages = fc.reconcile(findings)
        self.assertTrue(any("category_a" in message for message in messages))

    def test_counts_fall_back_when_no_finding_ids_carry_categories(self):
        findings = {"summary": {"category_a": ["one", "two"], "category_b": ["three"]}}
        self.assertEqual(fc.render_counts(findings), {"A": 2, "B": 1, "HL": 0})

    def test_review_protocol_judgment_rules_are_aligned(self):
        root = Path(__file__).resolve().parents[2]
        for skill in ("sourcefidelity-review", "sourcefidelity-batch",
                      "sourcefidelity-reappraise"):
            text = (root / skill / "REVIEW_PROTOCOL.md").read_text(encoding="utf-8")
            self.assertIn("Running-head renditions", text)
            self.assertIn("Reported viewpoints", text)
            self.assertIn("redirect to a site home", text)

    def test_home_redirect_is_not_a_verified_page(self):
        self.assertTrue(ha._redirected_home("https://example.org/a", "https://example.org/"))
        self.assertFalse(ha._redirected_home("https://example.org/a", "https://example.org/a"))

    def test_hyperlink_rules_are_aligned_across_protocols(self):
        root = Path(__file__).resolve().parents[2]
        rows = []
        for skill in ("sourcefidelity-review", "sourcefidelity-batch",
                      "sourcefidelity-reappraise"):
            text = (root / skill / "REVIEW_PROTOCOL.md").read_text(encoding="utf-8")
            rows.extend(line for line in text.splitlines()
                        if line.startswith("| Hyperlink |"))
            self.assertIn("HL = distinct works missing", text)
            self.assertNotIn("There is no print-only exclusion.", text)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row for row in rows}, {max(rows)})


class AcademicChecksTests(unittest.TestCase):
    def test_quote_positive_negative_and_ambiguous(self):
        pages = [{"printed": "57", "text": "The quick brown fox jumps over the dog."}]
        hit = ac.check_quote("quick brown fox jumps", "The quick\nbrown fox jumps.", pages)
        self.assertEqual(hit["state"], "matched")
        self.assertEqual(hit["page"], "57")
        self.assertTrue(hit["span"])
        self.assertEqual(ac.check_quote("quick brown fox jumps", "Nothing here.")["state"],
                         "not_found")
        twice = ac.check_quote("quick brown fox jumps", "quick brown fox jumps and quick brown fox jumps")
        self.assertEqual(twice["state"], "ambiguous")
        self.assertEqual(twice["occurrences"], 2)

    def test_quote_abstains_on_short_or_empty_input(self):
        self.assertEqual(ac.check_quote("too short", "too short")["state"], "abstained")
        self.assertEqual(ac.check_quote("four word quotation here", "")["state"],
                         "abstained")

    def test_locator_positive_negative_and_abstention(self):
        self.assertEqual(ac.check_locator("pp. 57–82, 90", "63")["state"], "matched")
        self.assertEqual(ac.check_locator("p. 12", "63")["state"], "mismatch")
        self.assertEqual(ac.check_locator("", "63")["state"], "abstained")

    def test_numeric_fields_inspectable(self):
        result = ac.check_numeric_fields(
            {"year": "2008", "volume": "12", "issue": "2", "pages": "57-82",
             "doi": "https://doi.org/10.1/X"},
            {"year": "2008", "volume": "12", "issue": "2", "pages": "57–82",
             "doi": "10.1/x"})
        self.assertEqual(result["state"], "matched")
        mismatch = ac.check_numeric_fields({"year": "2009"}, {"year": "2008"})
        self.assertEqual(mismatch["state"], "mismatch")
        self.assertEqual(mismatch["fields"]["volume"]["state"], "abstained")

    def test_formatting_positive_negative_and_abstention(self):
        good = ac.check_formatting(
            "Smith, J. (2008). Title. Journal, 12(2), 57-82. https://doi.org/10.1/x")
        self.assertNotEqual(good["state"], "mismatch")
        bad = ac.check_formatting("Smith, J. Title. doi:10.1/x")
        self.assertEqual(bad["state"], "mismatch")
        self.assertEqual(ac.check_formatting("")["state"], "abstained")

    def test_wording_is_bounded(self):
        self.assertEqual(ac.check_wording(
            "the policy expands media markets", "the policy expands media markets")["state"],
            "close")
        self.assertEqual(ac.check_wording(
            "the policy expands media markets", "totally different words here")["state"],
            "distinct")
        self.assertEqual(ac.check_wording("short", "short text")["state"], "abstained")


class RetrievalBenchmarkTests(unittest.TestCase):
    def test_benchmark_measures_recall_and_wrong_source(self):
        metrics = bm.benchmark([
            {"name": "a", "text": "alpha beta gamma", "quotes": ["alpha beta"]},
            {"name": "b", "text": "alpha beta delta", "quotes": ["delta"]},
        ])
        self.assertEqual(metrics["quotes"], 2)
        self.assertEqual(metrics["recall"], 1.0)
        self.assertEqual(metrics["wrong_source_windows"], 1)

    def test_benchmark_fixture_reports_coverage(self):
        metrics = bm.benchmark([
            {"name": "a", "text": "nothing", "quotes": ["missing"]},
        ])
        self.assertEqual(metrics["coverage"], 0.0)
        self.assertEqual(metrics["found"], 0)

    def test_exact_quote_windows_are_marked(self):
        text = "=== PDF p1 | printed p57 | native | conf 1.0 ===\nquoted phrase appears here.\n"
        marks = [(0, 1, "57")]
        hits = mt.windows(text, marks, {}, ["quoted phrase"], 40, quotes=["quoted phrase"])
        self.assertTrue(hits)
        self.assertEqual(hits[0]["query_kind"], "quote")
        self.assertTrue(hits[0]["exact_quote"])

    def test_locator_members_preserve_ranges_and_commas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("Smith (2008) argues a point (p. 12; "
                             "Smith, 2008, pp. 57–82, 90).\nReferences\n"
                             "Smith, J. (2008). A work. Journal.\n")
            data = ec.build(path, "P")
        citing = [c for c in data["citations"] if "p. 12" in c["marker"]]
        self.assertTrue(citing)
        self.assertIn("57–82", citing[0]["locator_members"])
        self.assertIn("90", citing[0]["locator_members"])


class ClauseUncitedTests(unittest.TestCase):
    def _build(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body + "\nReferences\nSmith, J. (2020). A work. Journal.\n")
            return ec.build(path, "P")

    def test_clause_continuation_is_not_a_standalone_uncited_claim(self):
        data = self._build(
            "Smith (2020) found that uptake rose; this increase continued through "
            "2021 and reached 40% of households.")
        rows = [row for row in data["uncited_candidates"]
                if row.get("outcome") == "possible_continuation"]
        self.assertTrue(rows)
        self.assertIn("this increase", rows[0]["text"])

    def test_independent_claim_after_a_citation_is_uncited(self):
        data = self._build(
            "Smith (2020) found that uptake rose. The ministry expanded the "
            "programme in 2021, reaching 40% of households.")
        rows = [row for row in data["uncited_candidates"]
                if row.get("outcome") == "uncited"]
        self.assertTrue(rows)
        self.assertIn("ministry expanded", rows[0]["text"])

    def test_fully_cited_clauses_add_nothing(self):
        data = self._build(
            "Smith (2020) found that uptake rose. Smith (2020) also found that "
            "coverage reached 40% of households.")
        self.assertFalse([row for row in data["uncited_candidates"]
                          if row.get("outcome")])


class ReferenceInventoryTests(unittest.TestCase):
    def test_reference_ids_spans_and_text_are_shared(self):
        paragraphs = [
            {"id": "P1", "text": "References", "links": []},
            {"id": "P2", "text": "Smith, J. (2020). A very long title", "links": []},
            {"id": "P3", "text": "that wraps across lines. Journal.", "links": []},
            {"id": "P4", "text": "Jones, K. (2021). Another work.", "links": []},
        ]
        entries = ha.reference_entries(paragraphs)
        self.assertEqual([entry["id"] for entry in entries], ["R1", "R2"])
        self.assertIn("wraps across lines", entries[0]["text"])
        self.assertEqual([segment["paragraph_id"]
                          for segment in entries[0]["segments"]], ["P2", "P3"])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("References\nSmith, J. (2020). A very long title\n"
                             "that wraps across lines. Journal.\n"
                             "Jones, K. (2021). Another work.\n")
            data = ec.build(path, "P")
        self.assertEqual([reference["id"] for reference in data["references"]],
                         ["R1", "R2"])
        self.assertIn("wraps across lines", data["references"][0]["verbatim"])
        self.assertTrue(data["references"][0]["spans"])


class SourceScopeTests(unittest.TestCase):
    def test_cited_page_range(self):
        self.assertEqual(scope.cited_page_range("Journal, 28(2), 501-523."), (501, 523))
        self.assertEqual(scope.cited_page_range("In Book (pp. 57–82)."), (57, 82))
        self.assertIsNone(scope.cited_page_range("Published 1999–2003."))

    def test_adequate_copy_covers_the_cited_range(self):
        pages = [{"printed": str(number), "text": "text"} for number in range(501, 511)]
        observation = scope.assess_reference_scope(
            "Journal, 28(2), 501-510.", pages)
        self.assertEqual(observation["status"], "adequate")
        self.assertTrue(observation["absence_allowed"])

    def test_preview_and_front_matter_do_not_support_absence(self):
        pages = [{"printed": value, "text": "Preview sample"}
                 for value in ("i", "ii", "iii", "iv")]
        observation = scope.assess_reference_scope("In Book (pp. 57-82).", pages)
        self.assertEqual(observation["status"], "inadequate")
        self.assertFalse(observation["absence_allowed"])

    def test_incomplete_scan_is_unresolved(self):
        pages = [{"printed": str(number), "text": "text"}
                 for number in [195, 196, 197, 198, 199, 200, 205, 206]]
        observation = scope.assess_reference_scope("Journal, 195-211.", pages)
        self.assertEqual(observation["status"], "unresolved")
        self.assertFalse(observation["absence_allowed"])

    def test_edition_observation_separates_affirmative_use_from_absence(self):
        pages = [{"printed": "1", "text": "Second edition 2019"}]
        later = scope.observe_edition("Journal, 2008, 1-10.", pages)
        self.assertEqual(later["status"], "later_edition")
        self.assertTrue(later["affirmative_use"])
        self.assertFalse(later["absence_allowed"])
        translation = scope.observe_edition(
            "Journal, 2008, 1-10.", [{"printed": "1", "text": "Translated by A. Translator"}])
        self.assertEqual(translation["status"], "translation")
        self.assertFalse(translation["affirmative_use"])
        same = scope.observe_edition(
            "Journal, 2008, 1-10.", [{"printed": "1", "text": "2008 edition"}])
        self.assertTrue(same["absence_allowed"])

    def test_script_variant_is_unresolved(self):
        pages = [{"printed": "1", "text": "Телевидение без границ " * 20}]
        observation = scope.assess_reference_scope(
            "Television without Frontiers, 1-10.", pages)
        self.assertEqual(observation["status"], "unresolved")
        self.assertIn("script", observation["reason"])


class ReferenceSpeedupTests(unittest.TestCase):
    def test_sourcepack_needs_title_evidence_for_filename_fallback(self):
        refs = [{"text": "Dyer, R. (2004). Heavenly bodies. Routledge."}]
        strong = "/tmp/dyer-2004-heavenly-bodies.pdf"
        weak = "/tmp/dyer-2004.pdf"
        with mock.patch.object(sp, "_first_page", return_value=""):
            self.assertEqual(sp.map_refs(refs, [weak], {}), {})
            self.assertEqual(sp.map_refs(refs, [strong], {})["R1"]["why"], "filename")

    def test_sourcepack_maps_opaque_file_and_rejects_misleading_filename(self):
        refs = [{"text": "Dyer, R. (2004). Heavenly bodies. Routledge."}]
        opaque = "/tmp/8.pdf"
        wrong = "/tmp/dyer-2004-heavenly-bodies.pdf"
        with mock.patch.object(sp.pdf_quality, "native_pages", side_effect=[
                {"pages": [{"text": "Only Entertainment\nRichard Dyer"}]},
                {"pages": [{"text": "Heavenly Bodies\nRichard Dyer"}]}]) as read:
            mapping = sp.map_refs(refs, [wrong, opaque], {})
        self.assertEqual(mapping["R1"]["path"], opaque)
        self.assertEqual(mapping["R1"]["why"], "first page")
        self.assertEqual(read.call_args_list, [mock.call(wrong, only_pages=[1]),
                                               mock.call(opaque, only_pages=[1])])

    def test_sourcepack_leaves_ambiguous_files_unmapped_unless_overridden(self):
        refs = [{"text": "Dyer, R. (2004). Heavenly bodies. Routledge."}]
        paths = ["/tmp/8.pdf", "/tmp/archive.pdf"]
        with mock.patch.object(sp, "_first_page",
                               return_value="heavenly bodies richard dyer"):
            self.assertEqual(sp.index_rows(refs, sp.map_refs(refs, paths, {}), {})
                             [0]["status"], "unmapped")
            self.assertEqual(sp.map_refs(refs, paths, {"R1": paths[1]})
                             ["R1"]["why"], "override")

    def test_openfetch_index_records_core_queries_and_unavailable_response(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(of.user_settings, "get",
                                  side_effect=lambda key: "fake" if key == "core_api_key" else ""), \
                mock.patch.object(of, "_curl_json", side_effect=[{}, {"results": []}, {"results": []}]), \
                redirect_stdout(io.StringIO()):
            of.main(["--ref", "Dyer, R. (2004). Stardom. Routledge.",
                     "--no-openalex", "--out", tmp])
            base = os.path.join(tmp, "_work", "open")
            with open(os.path.join(base, "index.json"), encoding="utf-8") as handle:
                row = json.load(handle)[0]
            self.assertEqual(row["core_queries"], [
                {"query": 'title:"Stardom"', "returned": None},
                {"query": 'title:"Stardom" AND authors:Dyer', "returned": 0},
                {"query": 'title:Stardom AND authors:Dyer', "returned": 0},
            ])
            self.assertEqual(row["status"], "search_incomplete")
            with open(os.path.join(base, "index.md"), encoding="utf-8") as handle:
                self.assertIn("response unavailable", handle.read())

    def test_sourcepack_search_reports_printed_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "_work", "sources", "R1")
            os.makedirs(source)
            open(os.path.join(source, "text.txt"), "w", encoding="utf-8").write(
                "=== PDF p7 | printed p42 | native\n"
                "freedom of reception and retransmission\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                sp.search(tmp, "freedom of reception")
        self.assertIn("R1", buf.getvalue())
        self.assertIn("printed p42", buf.getvalue())

    def test_openfetch_candidates_are_deduped(self):
        openalex = [{"best_oa_location": {"pdf_url": "https://x/a.pdf"},
                     "locations": [{"pdf_url": "https://x/a.pdf"}],
                     "open_access": {"oa_url": "https://x/b"}}]
        core = [{"downloadUrl": "https://x/a.pdf"}, {"downloadUrl": "https://x/c.pdf"}]
        urls = [url for url, _kind, _source in of._candidate_urls(openalex, core)]
        self.assertEqual(urls, ["https://x/a.pdf", "https://x/b", "https://x/c.pdf"])

    def test_openfetch_title_filter(self):
        parsed = {"title": "Television without Frontiers: The European Union's "
                           "Continuing Struggle for Cultural Survival"}
        self.assertTrue(of._title_ok(parsed, "Television Without Frontiers"))
        self.assertFalse(of._title_ok(parsed, "Consequences of EU directive"))

    def test_fetch_known_blocked_host_is_not_retried(self):
        original = http_cache.cache_dir()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                http_cache.set_cache_dir(tmp)
                http_cache.set_disabled(False)
                http_cache.host_note("https://blocked.example/x", "blocked")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = fetch_helper.main(["--url", "https://blocked.example/y",
                                            "--out", tmp])
                self.assertEqual(rc, 0)
                self.assertIn("known_blocked", buf.getvalue())
        finally:
            http_cache.set_cache_dir(original)


class ReviewPipelineRegressionTests(unittest.TestCase):
    """The bugs the public-review run exposed; each test fails on the old code."""

    def test_masker_keeps_doi_url_isbn_and_query_value(self):
        text = ("doi: 10.1108/10595421011080760 ISBN 9780203122747 "
                "https://example.org/x?contentId=8448721")
        self.assertEqual(ri.mask_ranges(text), [])
        text = "12345678"
        self.assertEqual([text[start:end] for start, end in ri.mask_ranges(text)],
                         ["12345678"])

    def test_crossref_by_doi_cached_passes_one_argument(self):
        meta = {"title": "Trade protectionism: reasons and outcomes"}
        with mock.patch.object(rd, "cache_get", return_value=None), \
                mock.patch.object(rd, "cache_put"), \
                mock.patch.object(rd, "crossref_by_doi", return_value=meta) as lookup:
            self.assertEqual(rd.crossref_by_doi_cached("10.1108/x"), meta)
        lookup.assert_called_once_with("10.1108/x")

    def test_a_matching_supplied_doi_is_confirmed(self):
        parsed = rd.parse_reference(
            "Abboushi, S. (2010). Trade protectionism: reasons and outcomes. "
            "Competitiveness Review, 20(5), 384-394. https://doi.org/10.1108/x")
        meta = {"title": "Trade protectionism: reasons and outcomes",
                "authors": ["Abboushi"], "year": 2010,
                "container": "Competitiveness Review", "volume": "20",
                "page": "384-394", "publisher": "Emerald"}
        verdict, _evidence = rd.doi_verdict(parsed, meta, "article")
        self.assertEqual(verdict, "confirmed")

    def test_a_supplied_doi_to_another_work_is_a_conflict(self):
        parsed = rd.parse_reference(
            "Abboushi, S. (2010). Trade protectionism: reasons and outcomes. "
            "Competitiveness Review, 20(5), 384-394. https://doi.org/10.1177/x")
        meta = {"title": "Media imperialism revisited: some findings from the Asian case",
                "authors": ["Chadha", "Kavoori"], "year": 2000,
                "container": "Media, Culture & Society", "volume": "22",
                "page": "415-432", "publisher": "SAGE"}
        verdict, _evidence = rd.doi_verdict(parsed, meta, "article")
        self.assertEqual(verdict, "bibliographic_conflict")

    def test_title_containment_needs_three_tokens(self):
        self.assertEqual(rd.title_score(
            "Global entertainment media",
            "Global entertainment media: Between cultural imperialism and cultural "
            "globalization"), 1.0)
        self.assertEqual(rd.title_score(
            "Trade policy", "Trade policy and development"), 0.7)

    def test_a_url_split_across_paragraphs_is_joined(self):
        records = [
            {"text": "Reference", "links": []},
            {"text": "Cui, B. (2017). A chapter. https://www.pishu.com.cn/x?", "links": []},
            {"text": "contentId=8448721&contentType=literature", "links": []},
        ]
        entries = ha.reference_entries(records)
        self.assertEqual(len(entries), 1)
        self.assertIn("https://www.pishu.com.cn/x?contentId=8448721", entries[0]["text"])

    def test_reconstructed_locator_keeps_raw_fragments_and_native_field(self):
        records = [{"text": "References"},
                   {"text": "Cui, B. (2017). A chapter. https://example.test/item?",
                    "links": ["https://example.test/item?id=1"]},
                   {"text": "id=1"}]
        entry, = ha.reference_entries(records)
        locator, = ha.submitted_locators(entry)
        self.assertEqual(locator["url"], "https://example.test/item?id=1")
        self.assertEqual(locator["observations"], [
            {"origin": "reconstructed", "raw": ["https://example.test/item?", "id=1"]},
            {"origin": "hyperlink", "raw": ["https://example.test/item?id=1"]},
        ])

    def test_archive_does_not_replace_submitted_link_failure(self):
        entry = {"text": "Cui, B. (2017). A chapter. https://example.test/item",
                 "links": ["https://example.test/companion"]}
        with mock.patch.object(ha, "paragraph_records", return_value=[]), \
                mock.patch.object(ha, "reference_entries", return_value=[entry]), \
                mock.patch.object(ha, "fetch_state", side_effect=[
                    {"state": "dead"}, {"state": "ok"}, {"state": "ok"}]), \
                mock.patch.object(ha, "wayback_url", return_value="https://example.test/archive"), \
                mock.patch.object(ha, "http_cache", None):
            result = ha.audit("paper.docx", wayback=True, jobs=1)
        row, = result["rows"]
        self.assertEqual(row["fetch"]["state"], "dead")
        self.assertEqual([link["fetch"]["state"] for link in row["submitted_locators"]],
                         ["dead", "ok"])
        self.assertEqual(row["alternative_locations"][0]["fetch"]["state"], "ok")

    def test_doi_work_mismatch_compares_the_record_title(self):
        with mock.patch.object(ha, "_doi_record",
                               return_value={"title": ["Media imperialism revisited"]}):
            self.assertTrue(ha.doi_work_mismatch(
                "10.1/x", "Abboushi, S. (2010). Trade protectionism: reasons and outcomes."))
        with mock.patch.object(ha, "_doi_record",
                               return_value={"title": ["Trade protectionism: reasons and outcomes"]}):
            self.assertFalse(ha.doi_work_mismatch(
                "10.1/x", "Abboushi, S. (2010). Trade protectionism: reasons and outcomes."))

    def test_scope_line_updates_instead_of_duplicating(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "review.md")
            open(path, "w", encoding="utf-8").write(
                "# Review\n\n## Review scope and counts\n\n"
                "Review time: not recorded; tokens: not recorded.\n\n"
                "## Summary of academic-integrity findings\n\nCounts.\n")
            data = {"runs": [{"phase": "review", "duration_ms": 63000,
                              "usage": {"input_tokens": 10, "output_tokens": 20,
                                        "cache_read_tokens": 90, "total_tokens": 120},
                              "cost_status": "estimated",
                              "estimated_cost_usd": 0.01}]}
            self.assertTrue(rm.update_scope_line(path, data))
            text = open(path, encoding="utf-8").read()
        self.assertEqual(text.count("Review time:"), 1)
        self.assertIn("Review time: 1m 03s", text)
        self.assertIn("100 prompt (90 cached + 10 uncached) / 20 output", text)
        self.assertIn("$0.0100 (estimated)", text)

    def test_cache_dir_is_stable_under_threads(self):
        from concurrent.futures import ThreadPoolExecutor
        original = http_cache.cache_dir()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = os.path.join(tmp, "nested", "cache")
                http_cache.set_disabled(False)
                http_cache.set_cache_dir(target)
                with ThreadPoolExecutor(max_workers=16) as pool:
                    seen = set(pool.map(lambda _i: http_cache.cache_dir(), range(64)))
                self.assertEqual(seen, {target})
        finally:
            http_cache.set_cache_dir(original)

    def test_host_state_remembers_a_blocked_host(self):
        original = http_cache.cache_dir()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                http_cache.set_cache_dir(tmp)
                http_cache.set_disabled(False)
                http_cache.host_note("https://blocked.example/a", "blocked")
                note = http_cache.host_known("https://blocked.example/b")
                self.assertEqual(note["state"], "blocked")
        finally:
            http_cache.set_cache_dir(original)

    def test_dossier_renders_a_row(self):
        row = {"id": "R1", "entry": "Abboushi (2010)", "url": "https://doi.org/x",
               "link_class": "doi", "fetch": {"state": "ok", "detail": "200"},
               "host": None, "identity": "confirmed", "doi": "10.1/x",
               "kind": "article", "notes": []}
        text = dossier.markdown([row], "paper.docx", "cache")
        self.assertIn("Source dossier", text)
        self.assertIn("confirmed", text)



if __name__ == "__main__":
    unittest.main(verbosity=2)

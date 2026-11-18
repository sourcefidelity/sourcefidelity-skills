#!/usr/bin/env python3
"""Offline regression tests for the batch pipeline.

    python3 -I test_pipeline.py

No network, no PDFs, no OCR. Uses unittest so pytest can also collect it.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest import mock

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
import enrich_registry as er  # noqa: E402
import evidence_window as ew  # noqa: E402
import file_digest as fd  # noqa: E402
import extract_candidates as ec  # noqa: E402
import make_task as mt  # noqa: E402
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
import safe_fetch as sf  # noqa: E402
import source_registry as sr  # noqa: E402
import validate_inventory as vi  # noqa: E402
import which_python as wp  # noqa: E402
import word_count as wc  # noqa: E402


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
            "Flew, T. (2011). Media as creative industries. In D. Winseck & "
            "D. Y. Jin (Eds.), The political economies of media (pp. 84-100). "
            "Bloomsbury Academic.")})
        self.assertEqual(parsed["work_kind"], "chapter_edited_collection")
        self.assertEqual(parsed["pages"], [84, 100])
        self.assertEqual(parsed["editors"], "D. Winseck & D. Y. Jin")

    def test_authored_book_chapter(self):
        parsed = sr.parse_source({"source": (
            "Croteau, D., & Hoynes, W. (2006). The new media giants. In The "
            "business of media (pp. 75-116). Pine Forge Press.")})
        self.assertEqual(parsed["work_kind"], "chapter_authored_book")
        self.assertEqual(parsed["pages"], [75, 116])

    def test_press_release_is_web_not_monograph(self):
        parsed = sr.parse_source({"source": (
            "Atlus. (2024, February 2). Reimagined genre-defining RPG Persona 3 "
            "Reload is available now [Press release].")})
        self.assertEqual(parsed["work_kind"], "web")

    def test_authored_book_chapter_without_in(self):
        parsed = sr.parse_source({"source": (
            "Jenkins, H. (2006). Buying into American Idol. Convergence culture: "
            "Where old and new media collide (pp. 59-92). New York University Press.")})
        self.assertEqual(parsed["work_kind"], "chapter_authored_book")
        self.assertEqual(parsed["container_title"],
                         "Convergence culture: Where old and new media collide")
        self.assertEqual(parsed["pages"], [59, 92])

    def test_journal_with_standalone_volume(self):
        parsed = sr.parse_source({"source": (
            "Denison, R. (2011). Anime fandom and the unlicensed text. "
            "Mechademia, 6, 447-464.")})
        self.assertEqual(parsed["work_kind"], "article")

    def test_organizational_author_is_not_a_publisher(self):
        parsed = sr.parse_source({"source": (
            "British Film Institute. (n.d.). British certification and tax "
            "relief FAQ.")})
        self.assertNotEqual(parsed["work_kind"], "monograph")

    def test_publisher_imprint_without_press(self):
        parsed = sr.parse_source({"source": (
            "Hardy, J. (2010). Cross-media promotion. Peter Lang.")})
        self.assertEqual(parsed["work_kind"], "monograph")

    def test_article_with_pp_is_not_a_chapter(self):
        parsed = sr.parse_source({"source": (
            "Smith, J. (2020). An article. Journal of Testing, 53(3), "
            "pp. 325-336.")})
        self.assertEqual(parsed["work_kind"], "article")

    def test_article(self):
        parsed = sr.parse_source({"source": (
            "Enderwick, P. (2011). Understanding the rise of global "
            "protectionism. Thunderbird International Business Review, 53(3), "
            "325-336.")})
        self.assertEqual(parsed["work_kind"], "article")
        self.assertEqual(parsed["year"], "2011")

    def test_web_url_is_extracted_from_entry(self):
        parsed = sr.parse_source({"source": (
            "Armitage, H. (2019, February 7). How Iron Man's first film almost "
            "destroyed Marvel. Digital Spy. "
            "https://www.digitalspy.com/movies/a26232993/iron-man-marvel-loan-bankruptcy/ ")})
        self.assertEqual(parsed["work_kind"], "web")
        self.assertEqual(parsed["url"],
                         "https://www.digitalspy.com/movies/a26232993/"
                         "iron-man-marvel-loan-bankruptcy/")

    def test_web_url_drops_sentence_punctuation(self):
        parsed = sr.parse_source({"source": (
            "Example. (2024). A web page. https://example.org/page).")})
        self.assertEqual(parsed["url"], "https://example.org/page")


class CandidateTests(unittest.TestCase):
    def test_nd_citation_is_found(self):
        text = ("Founded in 1986 ... distribution rights "
                "(Pixar Animation Studios, n.d.-b).")
        found = [m.group(0) for m in ec.ND_RE.finditer(text)]
        self.assertEqual(found, ["(Pixar Animation Studios, n.d.-b)"])


class SourceObjectTests(unittest.TestCase):
    def test_short_container_substring_does_not_link_another_book(self):
        registry = {
            "bryant": {"work_kind": "chapter_edited_collection",
                       "container_title": "The Ashgate research companion to fan cultures",
                       "form_issue": "chapter cited instead of the authored book",
                       "identity": {"author": "Bryant, E", "year": "2014",
                                    "title": "Toy collecting and adult lives"}},
            "hills": {"work_kind": "monograph",
                      "identity": {"author": "Hills, M", "year": "2002",
                                   "title": "Fan Cultures"}},
        }
        sr.resolve_chapters(registry)
        self.assertNotEqual(registry["bryant"].get("source_object_key"), "hills")
        self.assertEqual(registry["bryant"].get("form_issue"), "")

    def test_full_container_title_still_links(self):
        registry = {
            "flew": {"work_kind": "chapter_edited_collection",
                     "container_title": "The political economies of media",
                     "identity": {"author": "Flew, T", "year": "2011",
                                  "title": "Media as creative industries"}},
            "book": {"work_kind": "monograph",
                     "identity": {"author": "Winseck, D", "year": "2011",
                                  "title": "The political economies of media: "
                                           "The transformation of the global media industries"}},
        }
        sr.resolve_chapters(registry)
        self.assertEqual(registry["flew"].get("source_object_key"), "book")


class ScopeTests(unittest.TestCase):
    def rows(self, pages):
        return [{"printed_folio": str(p), "words": "100"} for p in pages]

    def test_adequate_scope(self):
        result = sr.assess_scope([75, 116], self.rows(range(75, 117)), 0.9)
        self.assertEqual(result["status"], "adequate")

    def test_partial_scope(self):
        result = sr.assess_scope([75, 116], self.rows(range(75, 81)), 0.9)
        self.assertEqual(result["status"], "partial")

    def test_absent_scope(self):
        result = sr.assess_scope([41, 65], self.rows(range(117, 140)), 0.9)
        self.assertEqual(result["status"], "absent")

    def test_unreliable_map_without_pages_is_unresolved(self):
        result = sr.assess_scope([41, 65], [], 0.125)
        self.assertEqual(result["status"], "unresolved")

    def test_no_range_is_not_required(self):
        self.assertEqual(sr.assess_scope([], [], None)["status"], "not_required")


class StructureTests(unittest.TestCase):
    def test_flew_style_front_matter(self):
        pages = [page(i, "", "Prelims") for i in range(1, 5)]
        pages.append(page(5, "", "Copyright in the collection and in any "
                               "introductory materials (c) Winseck and Jin 2011. "
                               "Copyright in the individual chapters (c) the Contributors."))
        result = ds.front_matter(pages)
        self.assertTrue(result["edited_collection"])
        self.assertIn("copyright in the collection", result["marker"])

    def test_acknowledgments_edited_by_is_not_a_collection(self):
        pages = [page(i, "", "The author thanks the series editors.") for i in range(1, 8)]
        pages.append(page(8, "", '"Will the Real Wolverine Please Stand Up?" '
                                'from Film and Comics, edited by Janet Smith.'))
        self.assertFalse(ds.front_matter(pages)["edited_collection"])

    def test_repeated_same_editor_line_is_one_collection(self):
        pages = [page(1, "", "The Blackwell Companion to Globalization"),
                 page(2, "", "Edited by George Ritzer"),
                 page(3, "", "The Blackwell Companion to Globalization\n"
                             "Edited by George Ritzer\n© 2007 by Blackwell Publishing")]
        for i in range(4, 7):
            pages.append(page(i, "", "Contents and preface"))
        self.assertTrue(ds.front_matter(pages)["edited_collection"])

    def test_series_page_with_many_editor_lines_is_not_a_collection(self):
        pages = [page(i, "", "Critical Media Studies") for i in range(1, 4)]
        pages.append(page(4, "", "\n".join([
            "Edited by John Durham Peters and Peter Simonson",
            "Edited by Mike Gasher and Greg Elmer",
            "Edited by Anandam P. Kavoori and Todd Fraley"])))
        for i in range(5, 7):
            pages.append(page(i, "", "The Film Studio\nBen Goldsmith and Tom O'Regan"))
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
                 "V. Cicchelli, S. Octobre, The Sociology of Hallyu Pop Culture,",
                 "https://doi.org/10.1007/978-3-030-84296-3_2", "2",
                 "Is Entertainment Capitalism the Ultimate Stage…"]
        self.assertEqual(page_map.folio_from_lines(lines), ("37", "arabic", 37))

    def test_chapter_opening_folio_below_copyright_line(self):
        lines = ["1© The Author(s), under exclusive license … 2021",
                 "V. Cicchelli, S. Octobre, The Sociology of Hallyu Pop Culture,",
                 "https://doi.org/10.1007/978-3-030-84296-3_1", "1",
                 "Introduction: Hallyu as an Alternative Aestheticized…"]
        self.assertEqual(page_map.folio_from_lines(lines), ("1", "arabic", 1))

    def test_even_page_head_leading_folio(self):
        self.assertEqual(page_map.folio_from_lines(
            ["24 V. Cicchelli and S. Octobre"]),
            ("24", "arabic", 24))


class MatchRefsTests(unittest.TestCase):
    def test_close_surname_still_links_reference(self):
        refs = [{"id": "R1", "verbatim": (
            "Croteau, D., & Hoynes, W. (2006). The business of media. "
            "Pine Forge Press.")}]
        citation = {"marker": "(Croteau and Hoyns, 2006)",
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
        result = self.result(type="book-chapter", title="Convergence Culture",
                             year="2017", authors=["Jenkins"])
        ok, why = ru.plausible(self.record("monograph", "2008"), result)
        self.assertFalse(ok)

    def test_doi_query_is_not_downgraded_here(self):
        result = self.result()
        result["query"] = "doi"
        ok, _ = ru.plausible(self.record(), result)
        self.assertTrue(ok)


class DataCiteTests(unittest.TestCase):
    def test_text_with_journal_citeproc_is_article(self):
        fields = ru.datacite_fields({
            "doi": "10.6092/example",
            "titles": [{"title": "Franchising Jurassic Park: Introduction"}],
            "creators": [{"familyName": "Melia"}],
            "publicationYear": 2023,
            "publisher": "Cinergie",
            "types": {"resourceTypeGeneral": "Text", "citeproc": "article-journal"},
            "container": {"title": "Cinergie"},
            "page": None,
        })
        self.assertEqual(fields["type"], "journal-article")
        self.assertEqual(fields["year"], "2023")
        self.assertEqual(fields["authors"], ["Melia"])


class FileMatchTests(unittest.TestCase):
    def test_doi_in_file_confirms(self):
        record = {"identity": {"doi": "10.1002/tie.20410", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Enderwick, P. (2011). Understanding the rise of global "
            "protectionism. Thunderbird International Business Review, 53(3), 325-336.")})
        score, detail = sr.score_file(
            "Understanding the rise of global protectionism\n"
            "doi:10.1002/tie.20410\n(c) 2011 Wiley", record, parsed)
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(detail["doi"])

    def test_later_edition_is_rejected(self):
        record = {"identity": {"doi": "", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Steger, M. (2003). Globalization: A very short introduction. "
            "Oxford University Press.")})
        score, detail = sr.score_file(
            "Globalization: A very short introduction\n"
            "© Manfred B. Steger 2013\nFirst Edition published in 2003", record, parsed)
        self.assertEqual(score, 0.0)
        self.assertTrue(detail["later_edition"])


    def test_chapter_author_matches_container_front_matter(self):
        parsed = sr.parse_source({"source": (
            "Goldsmith, B., & O'Regan, T. (2005). The policy environment of the "
            "contemporary film studio. In G. Elmer & M. Gasher (Eds.), "
            "Contracting out Hollywood (pp. 41-65). Rowman & Littlefield.")})
        match = dict(parsed)
        match["title"] = parsed["container_title"]
        match["author"] = parsed["editors"]
        record = {"work_kind": "chapter_edited_collection",
                  "identity": {"doi": "", "isbn": "", "author": parsed["author"]}}
        score, detail = sr.score_file(
            "Contracting out Hollywood (2005)\nGoldsmith and O'Regan\n"
            "The policy environment of the contemporary film studio",
            record, match)
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(detail["chapter_author"])

    def test_other_chapter_offprint_is_rejected(self):
        parsed = sr.parse_source({"source": (
            "Thussu, D. K. (2007). Mapping global media flow. In D. K. Thussu "
            "(Ed.), Media on the move (pp. 11-20). Routledge.")})
        match = dict(parsed)
        match["title"] = parsed["container_title"]
        match["author"] = parsed["editors"]
        match["chapter_title"] = parsed["title"]
        record = {"work_kind": "chapter_edited_collection",
                  "identity": {"doi": "", "isbn": "", "author": parsed["author"]}}
        score, detail = sr.score_file(
            "Media on the Move\nKim, Y. The rising East Asian wave\n"
            "Korean media go global", record, match)
        self.assertEqual(score, 0.0)
        self.assertLess(detail["chapter_coverage"], 0.5)

    def test_short_surname_does_not_match_inside_a_word(self):
        record = {"identity": {"doi": "", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Ng, J. (2018). Disneytoon Studios shutdown. Animation Magazine.")})
        score, detail = sr.score_file(
            "Disneytoon Studios shutdown", record, parsed,
            path="/tmp/Mihara - understanding animation.pdf")
        self.assertFalse(detail["filename_author"])

    def test_filename_author_and_year_corroborate_title(self):
        record = {"identity": {"doi": "", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Steger, M. B. (2003). Globalization: A very short introduction. "
            "Oxford University Press.")})
        score, detail = sr.score_file(
            "Globalization: A very short introduction\nVERY SHORT INTRODUCTIONS",
            record, parsed,
            path="/tmp/Globalization -- Manfred B Steger, 1961- -- 2003.pdf")
        self.assertGreaterEqual(score, 0.9)
        self.assertTrue(detail["filename_author"])
        self.assertTrue(detail["filename_year"])

    def test_later_edition_in_filename_is_rejected(self):
        record = {"identity": {"doi": "", "isbn": ""}}
        parsed = sr.parse_source({"source": (
            "Croteau, D., & Hoynes, W. (2001). The business of media. "
            "Pine Forge Press.")})
        score, detail = sr.score_file(
            "The business of media: Corporate media and the public interest\n"
            "Croteau, David\nHoynes, William", record, parsed,
            path="/tmp/The business of media -- 2nd ed_ -- 2006.pdf")
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
        self.assertIn("Review time: 1m 5s; tokens: 1,200 in / 300 out.", text)

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


class InstallerTests(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "install.sh")
    SKILLS = ("sourcefidelity-review", "sourcefidelity-batch", "sourcefidelity-reappraise")

    def _run(self, *args, env=None):
        environment = {**os.environ, **(env or {})}
        return subprocess.run(["/bin/bash", self.SCRIPT, *args], capture_output=True,
                              text=True, env=environment)

    def test_dry_run_lists_every_skill_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "skills")
            proc = self._run("--dry-run", "--target", target)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for skill in self.SKILLS:
                self.assertIn(f"would install: {skill}", proc.stdout)
            self.assertFalse(os.path.exists(target))

    def test_a_missing_target_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run(env={"HOME": tmp})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("--target", proc.stderr)

    def test_absent_python_reports_prompt_only_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "skills")
            shim = os.path.join(tmp, "shim-bin")
            os.makedirs(shim)
            for name in ("python3", "python3.12"):
                path = os.path.join(shim, name)
                open(path, "w").write("#!/bin/sh\nexit 1\n")
                os.chmod(path, 0o755)
            proc = self._run("--dry-run", "--target", target,
                             env={"PATH": shim + os.pathsep + "/usr/bin:/bin"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("prompt-only", proc.stdout)


class TaskMatchingTests(unittest.TestCase):
    REFS = [
        {"id": "R2", "verbatim": "Hardy, J. (2010). Dynamics and varieties of cross-media promotion. In Cross-media promotion (pp. 3-32). Peter Lang."},
        {"id": "R4", "verbatim": "York, A.E. (2010). From chick flicks to millennial blockbusters. The Journal of Popular Culture, 43(1), 3-25."},
    ]

    def test_a_shared_year_does_not_bind_another_work(self):
        citation = {"marker": "Hardy’s (2010)", "sentence": "Drawing on Hardy’s (2010) idea.", "year": "2010"}
        self.assertEqual(mt.match_refs(citation, self.REFS), ["R2"])

    def test_a_surname_still_binds(self):
        citation = {"marker": "(York, 2010)", "sentence": "Franchising (York, 2010).", "year": "2010"}
        self.assertEqual(mt.match_refs(citation, self.REFS), ["R4"])


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
            os.makedirs(os.path.join(skill, ".venv", "bin"))
            os.symlink(sys.executable, os.path.join(skill, ".venv", "bin", "python"))
        return skill

    def test_the_skill_venv_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._skill(tmp)
            result = wp.resolve(skill)
        self.assertEqual(result["source"], "venv")
        self.assertTrue(result["python"].endswith(os.path.join(".venv", "bin", "python")))
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


class RedactionTests(unittest.TestCase):
    CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="xml" ContentType="application/xml"/></Types>')
    RELS = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')

    def _docx(self, tmp, paragraphs, creator="Michael High",
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
            source = self._docx(tmp, [["Name: Michael High"], ["Body sentence stays."]])
            out = os.path.join(tmp, "copy.docx")
            report = ri.redact_file(source, out, tier=1)
            core = self._read(out, "docProps/core.xml")
            document = self._read(out, "word/document.xml")
            original = self._read(source, "docProps/core.xml")
        self.assertNotIn("Michael High", core)
        self.assertNotIn("Administrator", core)
        self.assertIn("Body sentence stays.", document)
        self.assertIn("Michael High", original)
        self.assertEqual(report["tier"], 1)

    def test_tier_two_masks_a_labelled_identity_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Name: Michael High"], ["Body stays."]])
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            document = self._read(out, "word/document.xml")
        self.assertNotIn("Michael", document)
        self.assertIn("Name: " + "*" * len("Michael High"), document)
        self.assertIn("Body stays.", document)

    def test_tier_two_masks_a_name_split_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Name: Michael ", "High"]])
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            document = self._read(out, "word/document.xml")
        self.assertNotIn("Michael", document)
        self.assertNotIn(">High<", document)

    def test_tier_two_masks_email_and_long_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._docx(tmp, [["Email: mh@example.com"],
                                      ["Student 2023123456 in the cohort."]])
            out = os.path.join(tmp, "copy.docx")
            ri.redact_file(source, out, tier=2)
            document = self._read(out, "word/document.xml")
        self.assertNotIn("mh@example.com", document)
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
            page.insert_text((72, 72), "Name: Michael High")
            page.insert_text((72, 100), "The argument is clear.")
            doc.save(source)
            doc.close()
            out = os.path.join(tmp, "copy.pdf")
            report = ri.redact_file(source, out, tier=2)
            masked = pymupdf.open(out)
            text = "\n".join(page.get_text() for page in masked)
            pages = masked.page_count
            masked.close()
        self.assertNotIn("Michael", text)
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
                {"source_key": "jenkins-2006", "retrieval": "unverifiable",
                 "identity_outcome": "unverifiable", "search_status": "searched"},
                {"source_key": "other-work", "retrieval": "identity_only",
                 "identity_outcome": "search_incomplete", "search_status": "searched"},
            ],
            "nonexistent_sources": [
                {"source": "Jenkins 2006", "source_key": "jenkins-2006", "r_id": "R1"},
                {"source": "Jenkins 2006 again", "source_key": "jenkins-2006", "r_id": "R2"}],
            "unverifiable_sources": [{"source": "Jenkins 2006", "source_key": "jenkins-2006",
                                      "r_id": "R1"}],
            "misrepresented_sources": [],
            "flags": {"fabrication_risk": True},
        }

    def _task(self, status="supplied_confirmed"):
        return {"sources": {"jenkins-2006": {
                    "supplied_file": "/tmp/jenkins.pdf",
                    "identity": {"title": "Convergence Culture", "year": 2006},
                    "identity_status": status}},
                "entries": [{"id": "C1", "prior": "Not Judged"}]}

    def _judgments(self, **extra):
        change = {"id": "C1", "prior": "Not Judged", "new": "Supports",
                  "source_key": "jenkins-2006", "evidence": "quote"}
        change.update(extra)
        return {"changes": [change]}

    def test_a_confirmed_supplied_copy_clears_the_finding_and_flag(self):
        data = ra.render_summary_data(self._data(), self._task(), self._judgments(), "2026-11-18")
        self.assertEqual(data["nonexistent_sources"], [])
        self.assertEqual(data["unverifiable_sources"], [])
        self.assertFalse(data["flags"]["fabrication_risk"])
        source = next(s for s in data["sources"] if s["source_key"] == "jenkins-2006")
        self.assertEqual(source["retrieval"], "retrieved")
        self.assertEqual(source["identity_outcome"], "confirmed")
        audit = data["reappraisal"]["identity_resolved"]
        self.assertEqual(audit[0]["source_key"], "jenkins-2006")
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
                         "jenkins-2006")

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
            os.symlink(outside, os.path.join(tmp, "P2"))
            with self.assertRaises(SystemExit):
                bp.paper_folder(tmp, "P2")

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


class SourceBindingTests(unittest.TestCase):
    def _write_extraction(self, root, key, digest, text, offset=None):
        out = os.path.join(root, "_sources", key)
        os.makedirs(out, exist_ok=True)
        json.dump({"sha256": digest, "offset": offset or {}},
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
        self.assertIn("Occurrences: 2 inventoried", text)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

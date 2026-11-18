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
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                      "source-use-review", "scripts")
sys.path.insert(0, HERE)
sys.path.insert(0, REVIEW)

import acquisition_list as al  # noqa: E402
import audit_tasks as at  # noqa: E402
import batch_report as br  # noqa: E402
import detect_structure as ds  # noqa: E402
import extract_candidates as ec  # noqa: E402
import make_task as mt  # noqa: E402
import page_map  # noqa: E402
import reappraise_apply as ra  # noqa: E402
import reappraise_prepare as rp  # noqa: E402
import record_metrics as rec  # noqa: E402
import render_review as rr  # noqa: E402
import resolve_unsupplied as ru  # noqa: E402
import review_metrics as rm  # noqa: E402
import source_registry as sr  # noqa: E402


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
        self.assertIn("**Review time:**", text)
        self.assertLess(text.index("**Review time:**"),
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

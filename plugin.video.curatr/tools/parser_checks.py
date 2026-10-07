"""Semantic parser and empty-preview regressions, using no live accounts."""

import copy
import json
import random
import time
import unittest
from unittest.mock import Mock, patch

from release_checks import install_kodi_stubs
import release_checks as fixtures

install_kodi_stubs("")
from lib import core, keyword_confirm
from lib.catalogue_clients import TMDBClient
from lib.keyword_matcher import (
    candidate_matches, confirmation_parts, parse_prompt, validate_filters,
)
from lib.list_request import parse_history, parse_request
from lib.request_text import mask_literals, restore_literals


class ParserChecks(unittest.TestCase):
    def test_short_creator_requests_keep_genres_themes_and_people(self):
        cases = (("horror", "Horror", "genre_labels"), ("crime", "Crime", "genre_labels"),
                 ("thrillers", "Thriller", "genre_labels"), ("comedy", "Comedy", "genre_labels"),
                 ("sci-fi", "Sci-Fi", "genre_labels"), ("dark", "Dark", "theme_labels"),
                 ("psychological", "Psychological", "theme_labels"))
        for prefix, label, field in cases:
            for name in ("stephen king", "Christopher Nolan", "John French", "Stella Dark", '"A. B. Creator"'):
                with self.subTest(prefix=prefix, name=name):
                    rules = parse_prompt(prefix + " by " + name)
                    self.assertEqual(rules["people"], [{"query": name.strip('"'), "role": "crew"}])
                    self.assertIn(label, rules[field])
                    self.assertEqual(rules["strategy"], "exact_people")

    def test_short_creator_requests_combine_with_other_filter_clauses(self):
        rules = parse_prompt("horror by stephen king similar to Chucky from 2010 rated 7")
        self.assertEqual(rules["people"], [{"query": "stephen king", "role": "crew"}])
        self.assertEqual(rules["genre_labels"], ["Horror"])
        self.assertEqual(rules["reference_title"], "Chucky")
        self.assertEqual((rules["year_min"], rules["rating_min"]), (2010, 7))
        self.assertEqual(rules["strategy"], "reference_people")
        rules = parse_prompt("crime starring Tom Hanks by A. B. Creator")
        self.assertEqual(rules["people"], [{"query": "Tom Hanks", "role": "cast"}, {"query": "A. B. Creator", "role": "crew"}])
        self.assertEqual(rules["genre_labels"], ["Crime"])
        rules = parse_prompt("British by A. B. Creator in French")
        self.assertEqual((rules["country"], rules["language"]), ("GB", "fr"))
        self.assertEqual(rules["person_query"], "A. B. Creator")

    def test_short_creator_normalisation_preserves_reference_title_by_words(self):
        for title in ("Stand By Me", "By the Sea", "The Man by the River"):
            for phrase in ("horror similar to ", "crime like "):
                with self.subTest(title=title, phrase=phrase):
                    rules = parse_prompt(phrase + title)
                    self.assertFalse(rules["people"])
                    self.assertEqual(rules["reference_movies"], [{"title": title, "year": 0}])
        rules = parse_prompt('similar to "Horror by Someone"')
        self.assertFalse(rules["people"] or rules["genres"])
        self.assertEqual(rules["reference_title"], "Horror by Someone")

    def test_short_creator_normalisation_preserves_sorting_and_explicit_roles(self):
        for prompt in ("horror sorted by rating", "crime ordered by year", "comedy ranked by popularity", "horror by rating", "horror not by A. B. Creator"):
            with self.subTest(prompt=prompt):
                self.assertFalse(parse_prompt(prompt)["people"])
        for marker, role in (("directed by", "director"), ("written by", "writer")):
            self.assertEqual(parse_prompt("horror " + marker + " A. B. Creator")["people"], [{"query": "A. B. Creator", "role": role}])

    def test_reference_followed_by_ordering_or_release_window_keeps_its_title(self):
        for suffix in ("sorted by release date", "ordered by highest rated", "releasing next month", "released this month"):
            with self.subTest(suffix=suffix):
                rules = parse_prompt("crime similar to Reference " + suffix)
                self.assertFalse(rules["people"])
                self.assertEqual(rules["reference_title"], "Reference")

    def test_writer_upgrade_preserves_manual_filters_and_leaves_explicit_roles_alone(self):
        prompt = "horror written by A Creator"
        old = parse_prompt(prompt)
        old.update(version=15, genres=[80], genre_labels=["Crime"], year_min=2000)
        old["people"][0]["role"] = "crew"
        upgraded = core.Curator._upgrade_keyword_rules(prompt, old)
        self.assertEqual(upgraded["people"][0]["role"], "writer")
        self.assertEqual((upgraded["genres"], upgraded["year_min"]), ([80], 2000))
        self.assertEqual(old["people"][0]["role"], "crew")
        old["people"][0]["role"] = "cast"
        self.assertEqual(core.Curator._upgrade_keyword_rules(prompt, old)["people"][0]["role"], "cast")

    def test_creator_reference_clauses_are_independent(self):
        cases = (
            ("films by Stephen King similar to Despicable Me", "Stephen King", "crew", "Despicable Me"),
            ("films by Stephen King similar to Chucky", "Stephen King", "crew", "Chucky"),
            ("films directed by Christopher Nolan like Inception", "Christopher Nolan", "director", "Inception"),
            ("films starring Tom Hanks similar to Big", "Tom Hanks", "cast", "Big"),
            ("films like Inception directed by Christopher Nolan", "Christopher Nolan", "director", "Inception"),
            ("films similar to Big starring Tom Hanks", "Tom Hanks", "cast", "Big"),
            ("movies like It written by Stephen King", "Stephen King", "writer", "It"),
            ("films similar to Despicable Me by Stephen King", "Stephen King", "crew", "Despicable Me"),
        )
        for prompt, person, role, title in cases:
            with self.subTest(prompt=prompt):
                rules = parse_prompt(prompt)
                self.assertEqual(rules["people"], [{"query": person, "role": role}])
                self.assertEqual(rules["reference_movies"], [{"title": title, "year": 0}])
                self.assertEqual(rules["strategy"], "reference_people")

    def test_cast_and_director_order_does_not_drop_either(self):
        for people in ("directed by Christopher Nolan starring Tom Hardy", "starring Tom Hardy directed by Christopher Nolan"):
            rules = parse_prompt("films " + people + " similar to Inception")
            self.assertEqual({(row["query"], row["role"]) for row in rules["people"]},
                             {("Christopher Nolan", "director"), ("Tom Hardy", "cast")})
            self.assertEqual(rules["reference_title"], "Inception")

    def test_repeated_explicit_people_clauses_are_bounded(self):
        rules = parse_prompt("directed by A One and directed by B Two starring C Three featuring D Four")
        self.assertEqual([row["query"] for row in rules["people"]], ["A One", "B Two", "C Three"])

    def test_names_with_catalogue_words_are_literal(self):
        for name in ("John French", "Jack Black", "Stella Dark", "Michael English", "John Barry"):
            for marker in ("films by", "starring", "directed by"):
                with self.subTest(name=name, marker=marker):
                    rules = parse_prompt(marker + " " + name)
                    self.assertEqual(rules["person_query"], name)
                    self.assertFalse(rules["country"] or rules["language"] or rules["genres"] or rules["themes"])

    def test_unquoted_titles_keep_prepositions_and_catalogue_words(self):
        titles = ("The Dark Knight", "A History of Violence", "Escape From New York",
                  "Me Before You", "One Flew Over the Cuckoo's Nest", "French Kiss",
                  "American History X", "Some Like It Hot", "Stand By Me", "Music and Lyrics")
        for title in titles:
            with self.subTest(title=title):
                rules = parse_prompt("films similar to " + title)
                # 'and' inside an unquoted title is ambiguous; quoted coverage is below.
                if title != "Music and Lyrics":
                    self.assertEqual(rules["reference_movies"], [{"title": title, "year": 0}])
                self.assertFalse(rules["genres"] or rules["themes"] or rules["country"] or rules["year_min"])

    def test_quoted_title_matrix_is_isolated_from_every_filter(self):
        titles = ("War and Peace", "French Kiss", "The Dark Knight", "Never Watched",
                  "Seen Twice", "Next Month", "After 2010", "Rated 80%", "Update Daily",
                  "Like It", "Horror or Comedy", "Music and Lyrics", "Directed by Nobody")
        for title in titles:
            for opening, closing in (("\"", "\""), ("“", "”"), ("'", "'"), ("‘", "’")):
                with self.subTest(title=title, quote=opening):
                    rules = parse_prompt("similar to " + opening + title + closing)
                    self.assertEqual(rules["reference_movies"], [{"title": title, "year": 0}])
                    for field in ("people", "genres", "themes", "country", "language", "history_mode", "rating_min", "year_min"):
                        self.assertFalse(rules[field], field)
                    self.assertFalse(rules["request_rules"]["window"] or rules["request_rules"]["refresh_mode"])

    def test_quotes_and_escaped_punctuation_roundtrip(self):
        text = '"He said \\"Hello\\"" and "D\'Artagnan"'
        masked, literals = mask_literals(text)
        self.assertEqual(restore_literals(masked, literals), 'He said "Hello" and D\'Artagnan')
        rules = parse_prompt('similar to "He said \\"Hello\\"" and "D\'Artagnan"')
        self.assertEqual([row["title"] for row in rules["reference_movies"]], ['He said "Hello"', "D'Artagnan"])

    def test_reference_lists_deduplicate_after_restoring_quotes(self):
        rules = parse_prompt('like "Arrival" and Arrival, "Up", "War and Peace", "More"')
        self.assertEqual([row["title"] for row in rules["reference_movies"]], ["Arrival", "Up", "War and Peace"])
        self.assertNotIn("\ue000", json.dumps(rules, ensure_ascii=False))

    def test_reference_years_are_separate_from_release_filters(self):
        rules = parse_prompt('like "The Thing" (1982) and "The Thing" (2011)')
        self.assertEqual(rules["reference_movies"], [{"title": "The Thing", "year": 1982}, {"title": "The Thing", "year": 2011}])
        self.assertEqual((rules["year_min"], rules["year_max"]), (0, 0))

    def test_independent_filters_survive_entity_masking(self):
        rules = parse_prompt('crime films directed by "John French" similar to "The Dark Knight" from 2010 rated 8 under 2 hours')
        self.assertEqual(rules["person_query"], "John French")
        self.assertEqual(rules["reference_title"], "The Dark Knight")
        self.assertEqual(rules["genre_labels"], ["Crime"])
        self.assertFalse(rules["country"] or rules["themes"])
        self.assertEqual((rules["year_min"], rules["rating_min"], rules["runtime_max"]), (2010, 8, 120))

    def test_collection_names_are_not_filters_or_prompt_prefixes(self):
        for prompt in ('films from the Harry Potter collection', 'the Harry Potter collection', 'all Harry Potter films'):
            self.assertEqual(parse_prompt(prompt)["collection_query"], "Harry Potter")
        self.assertEqual(parse_prompt('the "American Horror" collection')["collection_query"], "American Horror")
        for prompt in ("all French films", "all crime films", "all dark films"):
            self.assertEqual(parse_prompt(prompt)["collection_query"], "")

    def test_grouped_negative_genres_and_or_scope(self):
        cases = (
            ("crime and thriller without horror or romance", {"Crime", "Thriller"}, {"Horror", "Romance"}, "all"),
            ("horror or thriller without comedy and romance", {"Horror", "Thriller"}, {"Comedy", "Romance"}, "any"),
            ("without horror, romance and comedy", set(), {"Horror", "Romance", "Comedy"}, "all"),
            ("crime and thriller like \"Up or Down\"", {"Crime", "Thriller"}, set(), "all"),
            ("horror without horror", set(), {"Horror"}, "all"),
        )
        for prompt, wanted, excluded, mode in cases:
            with self.subTest(prompt=prompt):
                rules = parse_prompt(prompt)
                self.assertEqual(set(rules["genre_labels"]), wanted)
                self.assertEqual(set(rules["excluded_genre_labels"]), excluded)
                self.assertEqual(rules["genre_match"], mode)
                self.assertFalse(set(rules["genres"]) & set(rules["excluded_genres"]))

    def test_year_boundaries_and_display_roundtrip(self):
        for prompt, bounds in (("after 2010 before 2020", (2011, 2019)), ("from 2010 until 2020", (2010, 2020)), ("90s", (1990, 1999)), ("past 2 years", (2025, 2026))):
            with self.subTest(prompt=prompt):
                rules = parse_prompt(prompt, 2026)
                self.assertEqual((rules["year_min"], rules["year_max"]), bounds)
                part = next(p for p in confirmation_parts(rules) if p["field"] == "year")
                edited = parse_prompt(part["connector"] + " " + part["text"], 2026)
                self.assertEqual((edited["year_min"], edited["year_max"]), bounds)
        self.assertEqual(parse_prompt("before 2020")["year_max"], parse_prompt("released up to 2019")["year_max"])

    def test_runtime_ranges_are_visible_and_roundtrip(self):
        for prompt, bounds in (("between 90 and 120 minutes", (90, 120)), ("between 1.5 and 2 hours", (90, 120)), ("over 90 minutes under 2 hours", (90, 120))):
            rules = parse_prompt(prompt)
            self.assertEqual((rules["runtime_min"], rules["runtime_max"]), bounds)
            part = next(p for p in confirmation_parts(rules) if p["field"] == "runtime")
            edited = parse_prompt(part["connector"] + " " + part["text"])
            self.assertEqual((edited["runtime_min"], edited["runtime_max"]), bounds)

    def test_rating_units_use_the_source_scale(self):
        for prompt, key, expected in (("IMDb 80%", "external_rating_min", 8), ("rotten tomatoes 8/10", "external_rating_min", 80), ("popcornmeter 80%", "external_rating_min", 80), ("rated 80%", "rating_min", 8), ("rated 8/10", "rating_min", 8), ("IMDb 8 and rated 7", "rating_min", 7)):
            with self.subTest(prompt=prompt):
                self.assertEqual(parse_prompt(prompt)[key], expected)

    def test_country_and_language_are_both_visible(self):
        rules = parse_prompt("British films in French")
        self.assertEqual((rules["country"], rules["language"]), ("GB", "fr"))
        self.assertTrue({"country", "language"}.issubset({p["field"] for p in confirmation_parts(rules)}))
        self.assertEqual(parse_prompt("films in French")["country"], "")
        self.assertEqual(parse_prompt("show us crime films")["country"], "")
        self.assertEqual(parse_prompt("US films")["country"], "US")
        self.assertEqual(parse_prompt("movies from the US")["country"], "US")

    def test_collective_people_keep_trailing_genres_separate(self):
        rules = parse_prompt("films by the Coen brothers thrillers and crime")
        self.assertEqual(rules["person_query"], "the Coen brothers")
        self.assertEqual(set(rules["genre_labels"]), {"Thriller", "Crime"})

    def test_zero_plays_and_zero_schedules_are_not_silently_changed(self):
        for prompt in ("seen 0 times", "seen exactly 0 times", "seen at most 0 times", "seen fewer than 1 time"):
            self.assertEqual(parse_history(prompt)["history_mode"], "never")
        self.assertEqual(parse_history("seen at least 0 times")["history_mode"], "include")
        self.assertEqual(parse_request("refresh every 0 hours")["refresh_mode"], "")

    def test_history_dates_schedules_and_entities_stay_independent(self):
        rules = parse_prompt('films by Stephen King similar to "Up" not watched in 2 years update daily')
        self.assertEqual(rules["person_query"], "Stephen King")
        self.assertEqual(rules["reference_title"], "Up")
        self.assertEqual((rules["history_mode"], rules["history_days"]), ("stale", 730))
        self.assertEqual((rules["year_min"], rules["year_max"]), (0, 0))
        self.assertEqual(rules["request_rules"]["refresh_hours"], 24)
        self.assertNotIn("refresh", {p["field"] for p in confirmation_parts(rules)})

    def test_conflicting_ranges_are_rejected_before_catalogue_calls(self):
        for prompt in ("after 2020 before 2010", "over 2 hours under 90 minutes"):
            with self.assertRaisesRegex(ValueError, "minimum"):
                validate_filters(parse_prompt(prompt))
        curator = fixtures.InterfaceChecks.curator()
        with self.assertRaises(ValueError):
            curator._generate_keyword_and_write("Test", "after 2020 before 2010", 20)
        curator._require_keyword_catalogue.assert_not_called()
        curator._store_managed_record.assert_not_called()

    def test_candidate_genre_logic_obeys_parsed_exclusions(self):
        rules = parse_prompt("crime or thriller without horror or romance")
        self.assertTrue(candidate_matches({"genre_ids": [80]}, rules))
        self.assertTrue(candidate_matches({"genre_ids": [53]}, rules))
        self.assertFalse(candidate_matches({"genre_ids": [80, 27]}, rules))
        self.assertFalse(candidate_matches({"genre_ids": [35]}, rules))

    def test_empty_literals_cannot_create_a_person_or_reference(self):
        for prompt in ('films with ""', 'films like ""', 'films by ""'):
            rules = parse_prompt(prompt)
            self.assertFalse(rules["people"] or rules["reference_movies"])
            self.assertEqual(rules["confidence"], 0)

    def test_punctuation_unicode_and_large_numbers_do_not_crash(self):
        samples = (None, "", "\n\t", "\"", "’", "I haven't", "under " + "9" * 2000 + " hours", "rated " + "9" * 2000, "similar to “日本の映画”", 'starring "D\'Arcy Wretzky"', "crime " + "and " * 300)
        rng = random.Random(40)
        samples += tuple(" ".join(rng.choices(("crime", "by", "like", "and", "from", "seen", "%", "\"", "Tom", "2010", "次"), k=30)) for _ in range(100))
        started = time.monotonic()
        for sample in samples:
            with self.subTest(sample=str(sample)[:80]):
                rules = parse_prompt(sample)
                self.assertLessEqual(len(rules["people"]), 3)
                self.assertLessEqual(len(rules["reference_movies"]), 3)
                self.assertNotIn("\ue000", json.dumps(rules, ensure_ascii=False))
        self.assertLess(time.monotonic() - started, 10)


class EditorAndPreviewChecks(unittest.TestCase):
    def test_invalid_tag_edits_keep_the_existing_filter(self):
        for prompt, field in (("crime", "genres"), ("from 2010", "year"), ("rated 8", "rating"), ("under 120 minutes", "runtime"), ("in French", "language"), ("British", "country"), ("without horror", "excluded_genres")):
            obj = object.__new__(keyword_confirm.KeywordConfirmWindow)
            obj.rules = parse_prompt(prompt)
            before = copy.deepcopy(obj.rules)
            part = next(p for p in confirmation_parts(obj.rules) if p["field"] == field)
            with patch.object(keyword_confirm.xbmcgui, "Dialog") as dialog:
                dialog.return_value.input.return_value = "not a filter"
                obj._edit_part(part)
                self.assertEqual(obj.rules, before, field)
                dialog.return_value.ok.assert_called_once()

    def test_reference_person_strategy_survives_manual_add_and_remove(self):
        obj = object.__new__(keyword_confirm.KeywordConfirmWindow)
        obj.rules = parse_prompt("films by Stephen King")
        with patch.object(keyword_confirm.xbmcgui, "Dialog") as dialog:
            def choose(_heading, options):
                return options.index("Reference")
            dialog.return_value.select.side_effect = choose
            dialog.return_value.input.return_value = "Chucky"
            obj._add_filter()
        self.assertEqual(obj.rules["strategy"], "reference_people")
        obj._remove_part({"field": "reference", "index": 0})
        self.assertEqual(obj.rules["strategy"], "exact_people")
        obj.rules = parse_prompt("films like Chucky")
        with patch.object(keyword_confirm.xbmcgui, "Dialog") as dialog:
            dialog.return_value.select.side_effect = lambda _heading, options: options.index("Director")
            dialog.return_value.input.return_value = "Tom Holland"
            obj._add_filter()
        self.assertEqual(obj.rules["strategy"], "reference_people")

    def test_empty_preview_returns_to_the_same_draft_then_retries(self):
        curator = fixtures.InterfaceChecks.curator()
        curator._generate_keyword_and_write = Mock(side_effect=[core.NoKeywordMatches("No films matched"), {"name": "Keep me", "movies": [{"title": "Match"}]}])
        initial = {"name": "Keep me", "prompt": "films by Stephen King similar to Chucky", "description": "My description", "generation_method": "keyword", "regeneration_interval_hours": 37, "regeneration_enabled": True, "count": 15}
        calls = []
        def edit(_path, draft, *_args):
            calls.append(copy.deepcopy(draft))
            if len(calls) == 2:
                self.assertEqual(draft["name"], initial["name"])
                self.assertEqual(draft["description"], initial["description"])
                self.assertEqual(draft["regeneration_interval_hours"], 37)
                self.assertEqual(draft["count"], 15)
                draft = curator._edit_list_draft_field("prompt", draft)
            return "preview", draft
        decisions = iter(("create", "edit", "create"))
        with patch.object(core, "edit_list_settings", side_effect=edit), patch.object(core, "confirm_keyword_rules", side_effect=lambda *_args, **_kw: next(decisions)), patch.object(core.xbmcgui, "Dialog") as dialog:
            dialog.return_value.input.return_value = "horror films"
            result = curator.create_list_interactive(initial=initial)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["draft"]["prompt"], "horror films")
        self.assertEqual(result["draft"]["regeneration_interval_hours"], 37)
        self.assertEqual(curator._generate_keyword_and_write.call_count, 2)
        self.assertTrue(all(not call.kwargs["persist"] for call in curator._generate_keyword_and_write.call_args_list))
        self.assertEqual(initial["prompt"], "films by Stephen King similar to Chucky")
        curator._store_managed_record.assert_not_called()
        curator._save_state.assert_not_called()

    def test_empty_create_can_be_cancelled_without_saving(self):
        curator = fixtures.InterfaceChecks.curator()
        curator._generate_keyword_and_write = Mock(side_effect=core.NoKeywordMatches("No items"))
        actions = iter(("create", "cancel"))
        with patch.object(core, "edit_list_settings", side_effect=lambda _p, draft, *_a: (next(actions), draft)), patch.object(core, "confirm_keyword_rules", return_value="create"), patch.object(core.xbmcgui, "Dialog"):
            self.assertIsNone(curator.create_list_interactive(initial={"name": "Test", "prompt": "horror", "generation_method": "keyword"}))
        curator._store_managed_record.assert_not_called()
        curator._save_state.assert_not_called()

    def test_empty_combination_explains_the_intersection(self):
        curator = fixtures.InterfaceChecks.curator()
        curator._profile_source_available = lambda: False
        curator.tmdb = Mock()
        curator._keyword_candidate_pool = Mock(return_value=([], {}))
        with self.assertRaisesRegex(core.NoKeywordMatches, "both.*credits.*recommendations"):
            curator._generate_keyword_and_write("Test", "films by Stephen King similar to Chucky", 20, persist=False)
        curator._store_managed_record.assert_not_called()


class CatalogueChecks(unittest.TestCase):
    def test_multiple_references_share_the_bounded_result_budget(self):
        client = TMDBClient("test")
        client.search_movie = Mock(side_effect=[{"id": 1}, {"id": 2}])
        client._get = Mock(side_effect=[{"results": [{"id": i, "title": "First %d" % i} for i in range(10, 20)], "total_pages": 5}, {"results": [{"id": i, "title": "Second %d" % i} for i in range(20, 30)], "total_pages": 5}])
        rows = client.recommendation_pool([{"title": "A"}, {"title": "B"}], limit=4)
        self.assertEqual([row["tmdb_id"] for row in rows], [10, 11, 20, 21])
        self.assertEqual(client._get.call_count, 2)

    def test_recommendations_reach_later_pages_and_stop_at_the_limit(self):
        client = TMDBClient("test")
        client.search_movie = Mock(return_value={"id": 1})
        client._get = Mock(side_effect=[{"results": [{"id": 10, "title": "First"}], "total_pages": 3}, {"results": [{"id": 20, "title": "Second"}], "total_pages": 3}])
        rows = client.recommendation_pool([{"title": "Reference"}], limit=2)
        self.assertEqual([row["tmdb_id"] for row in rows], [10, 20])
        self.assertEqual([call.args[1]["page"] for call in client._get.call_args_list], [1, 2])

    def test_recommendations_deduplicate_and_cap_network_pages(self):
        client = TMDBClient("test")
        client.search_movie = Mock(return_value={"id": 1})
        client._get = Mock(return_value={"results": [{"id": 10, "title": "Same"}], "total_pages": 99999})
        self.assertEqual(len(client.recommendation_pool([{"title": "Reference"}], limit=100000)), 1)
        self.assertEqual(client._get.call_count, 5)
        client._get.reset_mock()
        client._get.return_value = {"results": [], "total_pages": 99999}
        self.assertEqual(client.recommendation_pool([{"title": "Reference"}]), [])
        client._get.assert_called_once()

    def test_creator_reference_candidate_pool_keeps_the_intersection(self):
        curator = fixtures.InterfaceChecks.curator()
        curator.tmdb = Mock()
        curator.tmdb.resolve_people.return_value = [{"id": 1, "name": "Stephen King", "role": "crew"}]
        curator.tmdb.credited_movies.return_value = [{"tmdb_id": 10, "title": "Creator only"}, {"tmdb_id": 20, "title": "Both"}]
        curator.tmdb.matches_filters.return_value = True
        curator.tmdb.recommendation_pool.side_effect = lambda _refs, **kw: [row for row in [{"tmdb_id": 30, "title": "Reference only"}, {"tmdb_id": 20, "title": "Both"}] if kw["accept"](row)]
        pool, _analysis = curator._keyword_candidate_pool(parse_prompt("films by Stephen King similar to Chucky"), 60)
        self.assertEqual([row["title"] for row in pool], ["Both"])

    def test_one_person_can_have_two_distinct_roles(self):
        client = TMDBClient("test")
        client.search_people = Mock(return_value=[{"id": 1, "name": "Person", "known_for_department": "Directing"}])
        refs = [{"query": "Person", "role": "director"}, {"query": "Person", "role": "cast"}, {"query": "Person", "role": "cast"}]
        self.assertEqual([row["role"] for row in client.resolve_people(refs)], ["director", "cast"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

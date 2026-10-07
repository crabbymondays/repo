"""Exercise real keyword generation against TMDB-shaped, account-free responses."""

import copy
from datetime import date
import unittest
from unittest.mock import Mock, patch

import release_checks as fixtures

fixtures.install_kodi_stubs("")
import requests

from lib import core, keyword_confirm
from lib.catalogue_clients import CatalogueError, TMDBClient
from lib.keyword_matcher import NoKeywordMatches, confirmation_parts, parse_prompt


def movie(number, genres=(80,), **fields):
    row = {"id": number, "title": "Film %d" % number, "release_date": "2018-01-01",
           "genre_ids": list(genres), "vote_average": 7, "vote_count": 100,
           "original_language": "en", "popularity": 10}
    row.update(fields)
    return row


class Provider:
    """Fixture responses preserve provider IDs, pagination and missing-detail fields."""

    def __init__(self):
        self.pages = {}
        self.credits = {}
        self.details = {}
        self.calls = []

    def get(self, path, params=None):
        params = dict(params or {})
        self.calls.append((path, params))
        if path == "/search/movie":
            return {"results": [movie(1, title=params["query"])]}
        if path == "/search/person":
            return {"results": [{"id": 2, "name": params["query"], "known_for_department": "Directing"}]}
        if path.startswith("/person/"):
            return copy.deepcopy(self.credits.get(int(path.split("/")[2]), {"cast": [], "crew": []}))
        if path.endswith("/recommendations") or path.startswith("/discover/"):
            pages = self.pages.get(path, [])
            index = int(params.get("page", 1)) - 1
            return {"results": copy.deepcopy(pages[index]) if index < len(pages) else [],
                    "total_pages": len(pages)}
        if path.startswith("/movie/"):
            return copy.deepcopy(self.details[int(path.split("/")[2])])
        raise AssertionError("Unexpected provider request: " + path)

    def curator(self, profile=None, hidden=None):
        curator = fixtures.InterfaceChecks.curator()
        curator.tmdb = TMDBClient("fixture")
        curator.tmdb._get = self.get
        curator.trakt = Mock()
        curator._profile_source_available = lambda: False
        curator.state["profile"] = copy.deepcopy(profile or {})
        curator.state["hidden_movies"] = copy.deepcopy(hidden or [])
        return curator


class GenerationChecks(unittest.TestCase):
    def build(self, provider, prompt, **options):
        curator = options.pop("curator", None) or provider.curator()
        result = curator._generate_keyword_and_write("Test", prompt, options.pop("count", 5),
                                                    persist=False, sync_to_trakt=False, **options)
        curator._store_managed_record.assert_not_called()
        curator._require_ai.assert_not_called()
        curator.trakt.search_movies.assert_not_called()
        return result

    def test_reported_genres_and_reference_use_all_or_any_consistently(self):
        for connector, expected in ((",", {12}), ("and", {12}), ("or", {10, 11, 12})):
            with self.subTest(connector=connector):
                provider = Provider()
                provider.pages["/movie/1/recommendations"] = [[movie(10, [80]), movie(11, [27]), movie(12, [80, 27])]]
                result = self.build(provider, "Crime %s Horror similar to Reference" % connector)
                self.assertEqual({row["ids"]["tmdb"] for row in result["movies"]}, expected)

    def test_all_genres_are_labelled_with_and(self):
        part = next(p for p in confirmation_parts(parse_prompt("crime, horror")) if p["field"] == "genres")
        self.assertIn(" and ", part["text"])

    def test_recommendation_filters_do_not_spend_the_result_budget(self):
        provider = Provider()
        provider.pages["/movie/1/recommendations"] = [[movie(i, [35]) for i in range(p * 20 + 10, p * 20 + 30)] for p in range(3)] + [[movie(99, [80, 27])]]
        result = self.build(provider, "crime, horror similar to Reference")
        self.assertEqual(result["movies"][0]["ids"]["tmdb"], 99)

    def test_watched_exclusions_do_not_spend_the_result_budget(self):
        provider = Provider()
        provider.pages["/movie/1/recommendations"] = [[movie(i) for i in range(p * 20 + 10, p * 20 + 30)] for p in range(3)] + [[movie(99)]]
        profile = {"watched": [{"tmdb_id": i} for i in range(10, 70)]}
        result = self.build(provider, "crime similar to Reference", curator=provider.curator(profile))
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [99])

    def test_discovery_reaches_fresh_items_after_local_exclusions(self):
        provider = Provider()
        provider.pages["/discover/movie"] = [[movie(i) for i in range(p * 20 + 10, p * 20 + 30)] for p in range(3)] + [[movie(99)]]
        profile = {"watched": [{"tmdb_id": i} for i in range(10, 70)]}
        result = self.build(provider, "crime", curator=provider.curator(profile))
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [99])

    def test_creator_reference_checks_complete_credits_not_two_top_lists(self):
        provider = Provider()
        provider.credits[2] = {"crew": [movie(i, job="Writer", department="Writing") for i in range(10, 180)], "cast": []}
        provider.pages["/discover/movie"] = [[movie(i) for i in range(10, 30)]]
        provider.pages["/movie/1/recommendations"] = [[movie(179)]]
        result = self.build(provider, "crime by A Creator similar to Reference")
        self.assertEqual(result["movies"][0]["ids"]["tmdb"], 179)
        self.assertFalse(any(path == "/discover/movie" for path, _ in provider.calls))

    def test_director_filter_rejects_non_directing_credits(self):
        provider = Provider()
        provider.credits[2] = {"crew": [movie(10, job="Producer"), movie(11, job="Director")], "cast": []}
        provider.pages["/discover/movie"] = [[movie(10), movie(11)]]
        result = self.build(provider, "films directed by A Creator")
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [11])

    def test_writer_filter_rejects_non_writing_credits(self):
        provider = Provider()
        provider.credits[2] = {"crew": [movie(10, job="Producer", department="Production"), movie(11, job="Novel", department="Writing")], "cast": []}
        provider.pages["/discover/movie"] = [[movie(10), movie(11)]]
        result = self.build(provider, "films written by A Creator")
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [11])

    def test_runtime_and_country_are_checked_for_recommendations(self):
        provider = Provider()
        provider.pages["/movie/1/recommendations"] = [[movie(10), movie(11), movie(12)]]
        provider.details = {10: movie(10, runtime=140, origin_country=["GB"]), 11: movie(11, runtime=90, origin_country=["US"]), 12: movie(12, runtime=90, origin_country=["GB"])}
        result = self.build(provider, "British crime similar to Reference under 2 hours")
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [12])

    def test_recommendations_obey_release_window(self):
        provider = Provider()
        provider.pages["/movie/1/recommendations"] = [[movie(10), movie(11, release_date="2026-10-10")]]
        with patch("lib.list_request.date_window", return_value=(date(2026, 10, 6), date(2026, 11, 5))):
            result = self.build(provider, "crime similar to Reference releasing next month")
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [11])

    def test_hidden_tmdb_only_titles_stay_hidden_even_when_watched_allowed(self):
        provider = Provider()
        provider.pages["/discover/movie"] = [[movie(10), movie(11)]]
        hidden = [{"title": "Film 10", "year": 2018, "media_type": "movie", "marker": "title:film 10:2018"}]
        result = self.build(provider, "crime including watched items", curator=provider.curator(hidden=hidden))
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [11])

    def test_known_ids_override_a_coincidental_identical_title_and_year(self):
        provider = Provider()
        provider.pages["/discover/movie"] = [[movie(10, title="Same"), movie(11, title="Same")]]
        profile = {"watched": [{"title": "Same", "year": 2018, "tmdb_id": 10}]}
        result = self.build(provider, "crime", curator=provider.curator(profile))
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [11])

    def test_title_fallback_still_excludes_history_without_comparable_ids(self):
        provider = Provider()
        provider.pages["/discover/movie"] = [[movie(10), movie(11)]]
        profile = {"watched": [{"title": "Film 10", "year": 2018, "trakt_id": 100}]}
        result = self.build(provider, "crime", curator=provider.curator(profile))
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [11])

    def test_refresh_reuses_previous_matches_after_preferring_new_ones(self):
        provider = Provider()
        rows = [movie(i) for i in range(10, 16)]
        provider.pages["/discover/movie"] = [rows]
        previous = {"movies": [{"title": row["title"], "year": 2018, "ids": {"tmdb": row["id"]}} for row in rows]}
        result = self.build(provider, "crime", managed_record=previous)
        self.assertEqual(len(result["movies"]), 5)

    def test_history_combination_keeps_reference_filter_and_needs_no_trakt(self):
        provider = Provider()
        provider.pages["/movie/1/recommendations"] = [[movie(10)]]
        profile = {"watched": [{"title": "Film %d" % i, "year": 2018, "tmdb_id": i, "playcount": 2} for i in (10, 11)]}
        provider.details = {i: movie(i) for i in (10, 11)}
        result = self.build(provider, "crime similar to Reference seen twice", curator=provider.curator(profile))
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [10])

    def test_history_country_language_and_runtime_are_all_applied(self):
        provider = Provider()
        profile = {"watched": [{"title": "Film %d" % i, "year": 2018, "tmdb_id": i, "playcount": 2} for i in (10, 11, 12, 13)]}
        provider.details = {10: movie(10, runtime=90, origin_country=["GB"], original_language="fr"), 11: movie(11, runtime=90, origin_country=["US"], original_language="fr"), 12: movie(12, runtime=90, origin_country=["GB"], original_language="en"), 13: movie(13, runtime=150, origin_country=["GB"], original_language="fr")}
        result = self.build(provider, "British crime in French under 2 hours seen twice", curator=provider.curator(profile))
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [10])

    def test_empty_genre_intersection_explains_how_to_request_either_genre(self):
        provider = Provider()
        provider.pages["/movie/1/recommendations"] = [[movie(10, [80]), movie(11, [27])]]
        with self.assertRaisesRegex(NoKeywordMatches, "(?i)all.*genres.*or"):
            self.build(provider, "crime, horror similar to Reference")

    def test_missing_reference_is_not_reported_as_overly_narrow_filters(self):
        provider = Provider()
        original = provider.get
        provider.get = lambda path, params=None: {"results": []} if path == "/search/movie" else original(path, params)
        with self.assertRaisesRegex(CatalogueError, "(?i)reference.*find|find.*reference"):
            self.build(provider, "crime similar to Unknown Reference")

    def test_pipeline_exposes_safe_counts_for_empty_result_diagnosis(self):
        provider = Provider()
        provider.pages["/movie/1/recommendations"] = [[movie(10, [35]), movie(11)]]
        result = self.build(provider, "crime similar to Reference")
        self.assertEqual(result["keyword_diagnostics"]["scanned"], 2)
        self.assertEqual(result["keyword_diagnostics"]["filter_rejected"], 1)

    def test_network_failure_keeps_creator_draft_for_retry_or_cancel(self):
        curator = fixtures.InterfaceChecks.curator()
        curator._generate_keyword_and_write = Mock(side_effect=CatalogueError("TMDB temporarily unavailable"))
        drafts = []
        def edit(_path, draft, *_args):
            drafts.append(copy.deepcopy(draft))
            return ("preview" if len(drafts) == 1 else "cancel"), draft
        initial = {"name": "Keep me", "prompt": "crime", "generation_method": "keyword", "description": "Keep this", "regeneration_interval_hours": 37}
        with patch.object(core, "edit_list_settings", side_effect=edit), patch.object(core, "confirm_keyword_rules", return_value="create"), patch.object(core.xbmcgui, "Dialog"):
            self.assertIsNone(curator.create_list_interactive(initial=initial))
        self.assertEqual(len(drafts), 2)
        self.assertEqual(drafts[1]["description"], "Keep this")
        self.assertEqual(drafts[1]["regeneration_interval_hours"], 37)
        curator._store_managed_record.assert_not_called()

    def test_missing_configuration_also_preserves_the_draft(self):
        curator = fixtures.InterfaceChecks.curator()
        curator._require_keyword_catalogue.side_effect = CatalogueError("Add a TMDB key")
        drafts = []
        def edit(_path, draft, *_args):
            drafts.append(copy.deepcopy(draft))
            return ("preview" if len(drafts) == 1 else "cancel"), draft
        with patch.object(core, "edit_list_settings", side_effect=edit), patch.object(core.xbmcgui, "Dialog"):
            self.assertIsNone(curator.create_list_interactive(initial={"name": "Keep", "prompt": "crime", "generation_method": "keyword"}))
        self.assertEqual(len(drafts), 2)
        self.assertEqual(drafts[1]["name"], "Keep")

    def test_new_items_are_preferred_but_previous_items_fill_remaining_spaces(self):
        provider = Provider()
        provider.pages["/discover/movie"] = [[movie(10), movie(11, vote_average=9), movie(12, vote_average=5)]]
        previous = {"movies": [{"title": "Film %d" % i, "year": 2018} for i in (10, 11)]}
        result = self.build(provider, "crime", managed_record=previous)
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [12, 11, 10])

    def test_explicit_sorting_survives_local_ranking_and_is_editable(self):
        for sort, expected in (("release date", [12, 11, 10]), ("rating", [10, 11, 12]), ("popularity", [11, 12, 10])):
            with self.subTest(sort=sort):
                provider = Provider()
                provider.pages["/movie/1/recommendations"] = [[movie(10, release_date="2010-01-01", vote_average=9, popularity=1), movie(11, release_date="2020-01-01", vote_average=7, popularity=30), movie(12, release_date="2025-01-01", vote_average=5, popularity=10)]]
                prompt = "crime similar to Reference sorted by " + sort
                result = self.build(provider, prompt)
                self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], expected)
                part = next(p for p in confirmation_parts(parse_prompt(prompt)) if p["field"] == "sort")
                obj = object.__new__(keyword_confirm.KeywordConfirmWindow)
                obj.rules = parse_prompt(prompt)
                obj._remove_part(part)
                self.assertEqual(obj.rules["sort"], "balanced")

    def test_rating_genre_year_and_language_filters_are_enforced_together(self):
        provider = Provider()
        provider.pages["/movie/1/recommendations"] = [[movie(10, release_date="2015-01-01", vote_average=8), movie(11, release_date="2005-01-01", vote_average=8), movie(12, vote_average=6), movie(13, vote_average=8, original_language="fr"), movie(14, [35], vote_average=8)]]
        result = self.build(provider, "crime similar to Reference from 2010 rated 8 in English")
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [10])

    def test_tv_and_movie_id_spaces_and_exclusions_remain_separate(self):
        provider = Provider()
        provider.pages["/discover/movie"] = [[movie(10)]]
        provider.pages["/discover/tv"] = [[dict(movie(10), name="Film 10", first_air_date="2018-01-01")]]
        profile = {"watched": [{"tmdb_id": 10, "title": "Film 10", "year": 2018}]}
        result = self.build(provider, "crime", content_type="both", curator=provider.curator(profile))
        self.assertEqual([row["media_type"] for row in result["movies"]], ["show"])

    def test_mixed_content_does_not_silently_drop_unsupported_tv_constraints(self):
        provider = Provider()
        for kind in ("shows", "both"):
            with self.subTest(kind=kind), self.assertRaisesRegex(NoKeywordMatches, "currently need Movies"):
                self.build(provider, "crime similar to Reference", content_type=kind)
        self.assertEqual(provider.calls, [])

    def test_tv_runtime_is_sent_as_a_provider_filter(self):
        provider = Provider()
        provider.pages["/discover/tv"] = [[dict(movie(10), name="Show", first_air_date="2018-01-01")]]
        self.build(provider, "crime over 20 minutes under 45 minutes", content_type="shows")
        params = provider.calls[0][1]
        self.assertEqual((params["with_runtime.gte"], params["with_runtime.lte"]), (20, 45))

    def test_mdblist_candidates_resolve_tmdb_ids_and_obey_local_filters(self):
        provider = Provider()
        provider.details = {10: movie(10, [35]), 11: movie(11, [80])}
        curator = provider.curator()
        curator.mdblist = Mock(api_key="fixture")
        curator.mdblist.catalog_movies.return_value = [{"title": "Film %d" % i, "year": 2018, "ids": {"tmdb": i}, "external_rating": 8} for i in (10, 11)]
        result = self.build(provider, "crime IMDb 7", curator=curator)
        self.assertEqual([row["ids"]["tmdb"] for row in result["movies"]], [11])


class ProviderBoundaryChecks(unittest.TestCase):
    def test_reference_budget_is_reused_when_another_reference_is_empty(self):
        client = TMDBClient("fixture")
        client.search_movie = Mock(side_effect=[{"id": 1}, {"id": 2}])
        client._get = Mock(side_effect=[{"results": [movie(i) for i in range(10, 16)], "total_pages": 1}, {"results": [], "total_pages": 1}])
        self.assertEqual(len(client.recommendation_pool([{"title": "A"}, {"title": "B"}], limit=6)), 6)
        self.assertEqual(client._get.call_count, 2)

    def test_identical_titles_do_not_merge_distinct_provider_ids(self):
        client = TMDBClient("fixture")
        client.search_movie = Mock(return_value={"id": 1})
        client._get = Mock(return_value={"results": [movie(10, title="Same"), movie(11, title="Same"), movie(10, title="Alias")], "total_pages": 1})
        self.assertEqual([row["tmdb_id"] for row in client.recommendation_pool([{"title": "Ref"}])], [10, 11])

    def test_exact_reference_title_is_preferred_and_wrong_year_is_not_substituted(self):
        client = TMDBClient("fixture")
        client._get = Mock(return_value={"results": [movie(10, title="Reference Part II"), movie(11, title="Reference") ]})
        self.assertEqual(client.search_movie("Reference")["id"], 11)
        with self.assertRaisesRegex(CatalogueError, "reference film.*1982"):
            client.recommendation_pool([{"title": "Reference", "year": 1982}], diagnostics={})

    def test_one_unresolved_person_cannot_drop_a_constraint(self):
        client = TMDBClient("fixture")
        client.search_people = Mock(side_effect=[[{"id": 1, "name": "Known"}], []])
        with self.assertRaisesRegex(CatalogueError, "Unknown"):
            client.resolve_people([{"query": "Known", "role": "cast"}, {"query": "Unknown", "role": "cast"}], maximum=6, strict=True)

    def test_multiple_people_require_each_credit_group(self):
        client = TMDBClient("fixture")
        client._get = Mock(side_effect=[{"crew": [movie(10, job="Director"), movie(11, job="Director")]}, {"cast": [movie(11), movie(12)]}])
        pool = client.credited_movies([{"id": 1, "role": "director", "group": 0}, {"id": 2, "role": "cast", "group": 1}])
        self.assertEqual([row["tmdb_id"] for row in pool], [11])

    def test_collective_members_are_combined_with_other_people_by_group(self):
        client = TMDBClient("fixture")
        client._get = Mock(side_effect=[{"crew": [movie(10, job="Director")]}, {"crew": [movie(11, job="Director")]}, {"cast": [movie(10), movie(11), movie(12)]}])
        pool = client.credited_movies([{"id": 1, "role": "director", "group": 0}, {"id": 2, "role": "director", "group": 0}, {"id": 3, "role": "cast", "group": 1}])
        self.assertEqual({row["tmdb_id"] for row in pool}, {10, 11})
        # Naming one collective member separately adds a real constraint,
        # even when TMDB returns the same person ID in both searches.
        client.search_people = Mock(side_effect=[[{"id": 1, "name": "A Family"}, {"id": 2, "name": "B Family"}], [{"id": 2, "name": "B Family"}]])
        resolved = client.resolve_people([{"query": "Family brothers", "role": "director"}, {"query": "B Family", "role": "director"}], maximum=6, strict=True)
        self.assertEqual([row["group"] for row in resolved], [0, 0, 1])
        self.assertEqual([row["tmdb_id"] for row in client.credited_movies(resolved)], [11])

    def test_required_details_are_reused_on_preview_and_creation(self):
        provider = Provider()
        provider.details[10] = movie(10, runtime=90, origin_country=["GB"])
        curator = provider.curator()
        for _ in range(2):
            curator.tmdb.begin_keyword_search()
            self.assertTrue(curator.tmdb.matches_filters(curator.tmdb._compact(movie(10)), parse_prompt("British crime under 2 hours")))
        self.assertEqual(len(provider.calls), 1)

    def test_metadata_requests_stop_with_an_explicit_limit(self):
        client = TMDBClient("fixture")
        client._get = lambda path, _params=None: movie(int(path.rsplit("/", 1)[1]), runtime=180)
        rules = parse_prompt("under 2 hours")
        for i in range(100):
            self.assertFalse(client.matches_filters(client._compact(movie(i + 1)), rules))
        with self.assertRaisesRegex(CatalogueError, "100 metadata-check limit"):
            client.matches_filters(client._compact(movie(101)), rules)

    def test_http_failures_are_distinct_from_empty_matches_and_hide_credentials(self):
        for code in (401, 429, 503):
            with self.subTest(code=code):
                session = Mock()
                session.get.return_value.status_code = code
                with self.assertRaises(CatalogueError):
                    TMDBClient("fixture-secret", session=session)._get("/discover/movie")
        session.get.side_effect = requests.RequestException("https://example/?api_key=fixture-secret")
        with self.assertRaises(CatalogueError) as error:
            TMDBClient("fixture-secret", session=session)._get("/discover/movie")
        self.assertNotIn("fixture-secret", str(error.exception))

    def test_connector_reservations_have_equal_gaps_on_both_sides(self):
        obj = object.__new__(keyword_confirm.KeywordConfirmWindow)
        obj.rules = parse_prompt("crime similar to Reference")
        obj.edit_mode = False
        obj._clear_flow = Mock()
        obj._add_label = Mock()
        obj._add_chip = Mock()
        obj.getControl = lambda _id: fixtures.FakeControl()
        obj._build_flow()
        first = obj._add_chip.call_args_list[0].args
        second = obj._add_chip.call_args_list[1].args
        label = obj._add_label.call_args_list[0].args
        self.assertEqual(label[3], "similar to")
        self.assertEqual(label[0] - (first[0] + first[2]), second[0] - (label[0] + label[2]))
        self.assertLessEqual(second[0] - (label[0] + label[2]), 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)

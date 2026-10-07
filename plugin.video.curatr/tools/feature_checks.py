"""Focused regressions for swatches, folder actions and Dynamic Lists (no live providers)."""

import copy
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit

from release_checks import ROOT, FakeAddon, FakeControl, install_kodi_stubs
import release_checks as fixtures

fixture_curator = fixtures.InterfaceChecks.curator

install_kodi_stubs("")
from lib import dynamic_lists as dynamic
from lib import dynamic_settings, core, colour_picker, ui_theme
from lib import dynamic_display, metadata_cache, view_refresh
from lib.catalogue_clients import CatalogueError, MDBListClient, RatingResults


def window(cls, *args):
    obj = object.__new__(cls)
    cls.__init__(obj, *args)
    controls = {cid: FakeControl() for cid in (10, 11, 20, 21, 22, 23, 24, 25, 26, 27, 28, 30, 31, 32, 33, 34, 100, 101, 102, 200, 201, 202, 203, 204, 300, 301, 302, 1020, 1022, 1023, 1024, 1025, 1026, 1027, 1028, 1100, 1101, 1102)}
    for cid, ctrl in controls.items():
        ctrl.control_id = cid
    obj.getControl = controls.__getitem__
    obj.setFocus = lambda ctrl: setattr(obj, "focus", ctrl)
    obj.getFocusId = lambda: obj.focus.control_id
    obj.close = Mock()
    obj.onInit()
    return obj, controls


def item(title, kind="movie", ident=1, played="", added="", **kwargs):
    data = {"title": title, "label": title, "type": kind, "uniqueid": {"tmdb": str(ident)},
            "file": "plugin://source/?item=" + title, "filetype": "directory" if kind == "tvshow" else "file",
            "lastplayed": played, "dateadded": added}
    data.update(kwargs)
    return {"kind": "native", "data": data}


class ColourChecks(unittest.TestCase):
    def test_old_presets_migrate_without_colour_changes(self):
        for old in ("violet", "ocean", "emerald", "amber"):
            addon = FakeAddon("", {"interface_theme": old})
            before = ui_theme.theme_palette(addon)
            config = ui_theme.theme_config(addon)
            self.assertTrue(config["custom"])
            ui_theme.save_theme(addon, config)
            self.assertEqual(before, ui_theme.theme_palette(addon))
        addon = FakeAddon("", {"interface_theme": "ocean", "interface_custom_colours": "true", "interface_primary_colour": "red"})
        before = ui_theme.theme_palette(addon)
        ui_theme.save_theme(addon, ui_theme.theme_config(addon))
        self.assertEqual(before, ui_theme.theme_palette(addon))

    def test_theme_swatches_live_preview_save_and_cancel(self):
        addon = FakeAddon("")
        original = dict(addon.settings)
        with patch.object(ui_theme, "_publish_palette"), patch.object(colour_picker, "_publish_palette"):
            obj, controls = window(colour_picker.ColourPickerWindow, addon)
            self.assertEqual(len(controls[100].items), 25)
            controls[100].selectItem(obj.keys.index("red"))
            obj.onClick(100)
            self.assertFalse(obj.config["custom"])
            self.assertEqual(obj.config["tint"], "red")
            self.assertFalse(controls[22].enabled)
            self.assertEqual(addon.settings, original)
            obj.onClick(21)
            obj.onClick(23)
            controls[100].selectItem(obj.keys.index("deep_blue"))
            obj.onClick(100)
            self.assertEqual(obj.config["secondary"], "deep_blue")
            obj.onClick(301)
            self.assertIsNone(obj.result)
            self.assertEqual(addon.settings, original)
            obj.onClick(300)
            self.assertEqual(ui_theme.theme_config(addon), obj.config)

    def test_background_swatches_keep_custom_cancel_and_preserve_state(self):
        addon = FakeAddon("")
        state = {"menu_background_style": "blue", "ai_lists": [{"local_id": "keep"}]}
        obj, controls = window(colour_picker.ColourPickerWindow, addon, state, True)
        self.assertEqual(controls[100].position, obj.keys.index("blue"))
        self.assertTrue(controls[22].visible)
        self.assertTrue(controls[25].enabled)
        self.assertTrue(controls[31].image.endswith("blue.png"))
        with patch.object(colour_picker.xbmcgui, "Dialog") as dialog:
            dialog.return_value.browseSingle.return_value = ""
            obj.onClick(27)
        self.assertEqual(obj.choice, "blue")
        obj.close.assert_not_called()
        controls[100].selectItem(obj.keys.index("amber"))
        obj.onClick(100)
        obj.onClick(300)
        self.assertEqual(obj.result["background"]["key"], "amber")
        self.assertEqual(state["menu_background_style"], "blue")

    def test_swatch_geometry_navigation_and_fixed_tint_absence(self):
        root = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-colour-picker.xml")
        panel = root.find(".//control[@id='100']")
        self.assertEqual(panel.findtext("onleft"), "300")
        self.assertEqual(panel.findtext("onright"), "300")
        for width, height in ((1280, 720), (1920, 1080), (3840, 2160), (2400, 1080), (1440, 1080)):
            # Kodi maps the complete 1080i coordinate system, with no device-pixel offsets.
            sx, sy = width / 1920, height / 1080
            for control in root.findall("./controls/control"):
                right = (float(control.findtext("left")) + float(control.findtext("width"))) * sx
                bottom = (float(control.findtext("top")) + float(control.findtext("height"))) * sy
                self.assertLessEqual(right, width)
                self.assertLessEqual(bottom, height)
        for path in (ROOT / "resources/skins/Default/1080i").glob("*.xml"):
            for control in ET.parse(path).iter("control"):
                if control.get("id", "").isdigit() and 1100 <= int(control.get("id")) <= 1400 and control.get("type") == "image":
                    self.assertIsNone(control.find("texture").get("colordiffuse"))


class DisplayChecks(unittest.TestCase):
    def test_dynamic_artwork_enrichment_preserves_sources_and_episode_identity(self):
        curator = Mock(addon=FakeAddon(""), tmdb=Mock(), mdblist=Mock())
        rows = [
            item("Movie", art={"poster": "image://source-poster"},
                 ratings={"imdb": {"rating": 7.1}}),
            item("Show", "tvshow", 2, art={"tvshow.poster": "/local/show.png"}),
            item("Episode", "episode", 2, season=1, episode=3,
                 thumbnail="/local/episode.jpg", resume={"position": 80, "total": 900}),
            {"kind": "curatr", "data": {"title": "Local", "ids": {"tmdb": 3}}},
        ]
        original = copy.deepcopy(rows)
        def enrich(movies, *_args, **_kwargs):
            for movie in movies:
                movie["images"] = {"poster": {"full": "cached-poster"}}
                movie["ratings"] = {"imdb": {"rating": 8.2}, "metacritic": {"rating": 7.0}}
        with patch.object(dynamic_display, "MetadataCache") as metadata, \
             patch.object(dynamic_display, "ArtworkCache") as artwork:
            metadata.return_value.enrich.side_effect = enrich
            artwork.return_value.art_for_movie.side_effect = lambda _: {"poster": "cached-poster", "fanart": "cached-fanart"}
            result = dynamic_display.prepare_items(curator, rows)
            movies = metadata.return_value.enrich.call_args.args[0]
        self.assertEqual(rows, original)
        self.assertEqual([m.get("media_type", "movie") for m in movies], ["movie", "show", "movie"])
        self.assertEqual(result[0]["art"]["poster"], "image://source-poster")
        self.assertEqual(result[0]["art"]["fanart"], "cached-fanart")
        self.assertEqual(result[0]["data"]["ratings"]["imdb"]["rating"], 7.1)
        self.assertEqual(result[0]["data"]["ratings"]["metacritic"]["rating"], 7.0)
        self.assertEqual(result[1]["art"]["poster"], "/local/show.png")
        self.assertEqual(result[2]["data"], original[2]["data"])
        self.assertEqual(result[2]["art"]["thumb"], "/local/episode.jpg")
        self.assertEqual(result[3]["art"]["poster"], "cached-poster")
        for before, after in zip(original[:3], result[:3]):
            self.assertEqual(after["data"]["file"], before["data"]["file"])

    def test_provider_failure_keeps_supplied_artwork(self):
        rows = [item("Offline", thumbnail="image://still-available", art={"fanart": "/local/fanart.jpg"})]
        with patch.object(dynamic_display, "MetadataCache") as metadata, \
             patch.object(dynamic_display, "ArtworkCache") as artwork:
            metadata.return_value.enrich.side_effect = CatalogueError("offline")
            artwork.return_value.prefetch_movies.side_effect = OSError("offline")
            artwork.return_value.art_for_movie.side_effect = OSError("offline")
            result = dynamic_display.prepare_items(Mock(addon=FakeAddon("")), rows)
        self.assertEqual(result[0]["art"]["thumb"], "image://still-available")
        self.assertEqual(result[0]["art"]["fanart"], "/local/fanart.jpg")


class RatingChecks(unittest.TestCase):
    def test_bulk_ratings_identify_tmdb_ids_in_the_request_body(self):
        session = Mock(headers={})
        session.post.return_value = Mock(status_code=200, json=lambda: {"ratings": [{"id": 123, "rating": 8.0}]})
        result = MDBListClient("test-key", session=session).ratings_for_ids("movie", [123, 123])
        self.assertTrue(result.complete)
        self.assertEqual(session.post.call_count, 4)
        for call in session.post.call_args_list:
            self.assertEqual(call.kwargs["params"], {"apikey": "test-key"})
            self.assertEqual(call.kwargs["json"]["ids"], [123])
            self.assertEqual(call.kwargs["json"]["provider"], "tmdb")

    def test_partial_rate_limit_retains_completed_scores_and_backs_off(self):
        session = Mock(headers={})
        session.post.side_effect = [
            Mock(status_code=200, json=lambda: {"ratings": [{"id": 1, "rating": 8.0}]}),
            Mock(status_code=429, headers={"Retry-After": "900"}),
        ]
        result = MDBListClient("test-key", session=session).ratings_for_ids("movie", [1])
        self.assertEqual(result[1]["imdb"]["rating"], 8.0)
        self.assertFalse(result.complete)
        self.assertEqual(result.retry_after, 900)
        now = int(time.time())
        with tempfile.TemporaryDirectory() as profile, patch.object(metadata_cache.time, "time", return_value=now):
            cache = metadata_cache.MetadataCache(FakeAddon(profile))
            provider = Mock(api_key="test-key", ratings_for_ids=Mock(return_value=result))
            rows = [{"ids": {"tmdb": 1}}, {"media_type": "show", "ids": {"tmdb": 2}}]
            cache.enrich(rows, None, provider)
            self.assertEqual(rows[0]["ratings"]["imdb"]["rating"], 8.0)
            self.assertEqual(provider.ratings_for_ids.call_count, 1)
            reopened = metadata_cache.MetadataCache(FakeAddon(profile))
            reopened.enrich(rows, None, provider)
            self.assertEqual(provider.ratings_for_ids.call_count, 1)
            self.assertEqual(reopened._load()["ratings_retry_at"], now + 900)

    def test_empty_scores_retry_and_successful_scores_expire_without_refreshing_tmdb_age(self):
        now = [int(time.time())]
        with tempfile.TemporaryDirectory() as profile, patch.object(metadata_cache.time, "time", side_effect=lambda: now[0]):
            cache = metadata_cache.MetadataCache(FakeAddon(profile))
            cache._load()["items"]["movie:1"] = {"cached_at": now[0] - 86400,
                "metadata": {"cast": [], "mdblist_ratings_checked": True}}
            provider = Mock(api_key="test-key", ratings_for_ids=Mock(return_value=RatingResults({1: {}})))
            rows = [{"ids": {"tmdb": 1}}]
            cache.enrich(rows, None, provider)
            cache.enrich(rows, None, provider)
            self.assertEqual(provider.ratings_for_ids.call_count, 1)
            now[0] += metadata_cache.RATINGS_RETRY_SECONDS + 1
            result = RatingResults({1: {"imdb": {"rating": 8.0}}})
            result.complete = True
            provider.ratings_for_ids.return_value = result
            cache.enrich(rows, None, provider)
            self.assertEqual(provider.ratings_for_ids.call_count, 2)
            self.assertEqual(rows[0]["ratings"]["imdb"]["rating"], 8.0)
            cached_at = cache._load()["items"]["movie:1"]["cached_at"]
            now[0] += metadata_cache.RATINGS_TTL_SECONDS + 1
            cache.enrich(rows, None, provider)
            self.assertEqual(provider.ratings_for_ids.call_count, 3)
            self.assertEqual(cache._load()["items"]["movie:1"]["cached_at"], cached_at)
            self.assertNotIn("mdblist_ratings_checked", cache.get("movie", 1))

    def test_new_api_key_retries_after_rejected_key(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = metadata_cache.MetadataCache(FakeAddon(profile))
            bad = Mock(api_key="old-key", ratings_for_ids=Mock(side_effect=CatalogueError("rejected")))
            rows = [{"ids": {"tmdb": 1}}]
            cache.enrich(rows, None, bad)
            result = RatingResults({1: {"imdb": {"rating": 8.0}}})
            result.complete = True
            good = Mock(api_key="new-key", ratings_for_ids=Mock(return_value=result))
            reopened = metadata_cache.MetadataCache(FakeAddon(profile))
            reopened.enrich(rows, None, good)
            good.ratings_for_ids.assert_called_once()
            self.assertEqual(rows[0]["ratings"]["imdb"]["rating"], 8.0)

    def test_ratings_only_cache_does_not_block_later_tmdb_metadata(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = metadata_cache.MetadataCache(FakeAddon(profile))
            cache._load()["items"]["movie:1"] = {"cached_at": int(time.time()),
                "metadata": {"ratings": {"imdb": {"rating": 8.0}}}}
            tmdb = Mock(api_key="test", list_item_details=Mock(return_value={}), image_url=Mock(return_value=""))
            rows = [{"ids": {"tmdb": 1}}]
            cache.enrich(rows, tmdb)
            tmdb.list_item_details.assert_called_once_with(1, "movie")
            self.assertEqual(rows[0]["ratings"]["imdb"]["rating"], 8.0)
            self.assertIn("cast", cache.get("movie", 1))


class MergeChecks(unittest.TestCase):
    def test_global_sort_dedup_keeps_shows_and_episodes_separate(self):
        movie = item("Movie", ident=1, played="2026-09-10")
        series = item("Show", "tvshow", 1, played="2026-09-12")
        episode = item("Episode", "episode", 1, played="2026-09-13", season=1, episode=2)
        next_episode = item("Next Episode", "episode", 1, played="2026-09-11", season=1, episode=3)
        duplicate = copy.deepcopy(movie)
        duplicate["data"].update(file="plugin://other/movie", lastplayed="2026-09-14")
        rows, missing = dynamic.merge_items([[movie, series], [episode, duplicate, next_episode]], count=20)
        self.assertEqual([r["data"]["title"] for r in rows], ["Movie", "Episode", "Show", "Next Episode"])
        self.assertEqual(rows[0]["data"]["file"], movie["data"]["file"])
        self.assertEqual(missing, 0)
        self.assertNotIn("lastplayed", movie)

    def test_date_order_unknowns_and_stable_fallback(self):
        unknown = item("Unknown", ident=4)
        older = item("Older", ident=2, played="2026-01-01T12:00:00Z", added="2024-01-01")
        newer = item("Newer", ident=3, played="2026-02-01 12:00:00", added="2025-01-01")
        for descending, expected in ((True, ["Newer", "Older", "Unknown"]), (False, ["Older", "Newer", "Unknown"])):
            rows, missing = dynamic.merge_items([[unknown, older], [newer]], descending=descending)
            self.assertEqual([r["data"]["title"] for r in rows], expected)
            self.assertEqual(missing, 1)
        rows, _ = dynamic.merge_items([[newer, older]], sort="dateadded", descending=False)
        self.assertEqual(rows[0]["data"]["title"], "Older")
        rows, _ = dynamic.merge_items([[newer], [older]], sort="title", descending=False, count=1)
        self.assertEqual(rows[0]["data"]["title"], "Newer")
        rows, _ = dynamic.merge_items([[newer], [older]], sort="source")
        self.assertEqual(rows[0]["data"]["title"], "Newer")

    def test_distinct_unknown_items_are_not_deduped_by_title(self):
        a = item("Same", ident=1); a["data"]["uniqueid"] = {}
        b = copy.deepcopy(a); b["data"]["file"] = "plugin://other/path"
        rows, _ = dynamic.merge_items([[a, b]])
        self.assertEqual(len(rows), 2)

    def test_alternation_cycles_sources_and_skips_empty_or_exhausted_sources(self):
        groups = [[], [item("1a", ident=1), item("1b", ident=2), item("1c", ident=3)],
                  [item("2a", ident=4)], [item("3a", ident=5), item("3b", ident=6)]]
        original = copy.deepcopy(groups)
        for sort in ("source", "lastplayed", "dateadded"):
            rows, missing = dynamic.merge_items(groups, sort=sort, alternate_sources=True)
            self.assertEqual([r["data"]["title"] for r in rows], ["1a", "2a", "3a", "1b", "3b", "1c"])
            self.assertEqual(missing, 0 if sort == "source" else 6)
        rows, _ = dynamic.merge_items(groups, sort="source", alternate_sources=True, count=4)
        self.assertEqual([r["data"]["title"] for r in rows], ["1a", "2a", "3a", "1b"])
        self.assertEqual(groups, original)

    def test_date_sort_only_alternates_unknown_dates_after_sorted_dated_items(self):
        groups = [[item("Older", ident=1, played="2026-01-01", added="2026-01-01"),
                   item("1a", ident=2), item("1b", ident=3)],
                  [item("2a", ident=4), item("Newer", ident=5, played="2026-02-01", added="2026-02-01")],
                  [item("3a", ident=6), item("3b", ident=7)]]
        for sort in ("lastplayed", "dateadded"):
            for descending in (False, True):
                rows, missing = dynamic.merge_items(groups, sort=sort, descending=descending, alternate_sources=True)
                dated = ["Newer", "Older"] if descending else ["Older", "Newer"]
                self.assertEqual([r["data"]["title"] for r in rows], dated + ["1a", "2a", "3a", "1b", "3b"])
                self.assertEqual(missing, 5)

    def test_dedup_precedes_alternation_and_keeps_first_source_playback(self):
        first = item("First", ident=1)
        duplicate = item("Duplicate", ident=1, file="plugin://other/duplicate")
        groups = [[first, item("Second", ident=2)], [duplicate, item("Third", ident=3), item("Fourth", ident=4)],
                  [duplicate], [item("Fifth", ident=5)]]
        rows, _ = dynamic.merge_items(groups, sort="source", alternate_sources=True)
        self.assertEqual([r["data"]["title"] for r in rows], ["First", "Third", "Fifth", "Second", "Fourth"])
        self.assertEqual(rows[0]["data"]["file"], first["data"]["file"])
        self.assertEqual(rows[0]["source_index"], 0)

    def test_alphabetical_sort_ignores_alternation_in_both_directions(self):
        groups = [[item("Zulu", ident=1), item("Beta", ident=2)], [item("alpha", ident=3), item("Charlie", ident=4)]]
        for descending in (False, True):
            rows, missing = dynamic.merge_items(groups, sort="title", descending=descending, alternate_sources=True)
            expected = ["alpha", "Beta", "Charlie", "Zulu"]
            self.assertEqual([r["data"]["title"] for r in rows], expected[::-1] if descending else expected)
            self.assertEqual(missing, 0)

    def test_source_validation_blocks_recursion_and_deduplicates_sources(self):
        for path in ("RunPlugin(plugin://foo)", "plugin://plugin.video.curatr/?action=dynamic_list", "plugin://other/\n", "http://example.com"):
            self.assertFalse(dynamic.valid_source({"type": "external_path", "path": path}))
        row = {"type": "external_path", "path": "plugin://source/path"}
        record = dynamic.normalise({"sources": [row, dict(row, name="Duplicate"), {"type": "curatr_action", "shortcut": "quick_pick"}]})
        self.assertEqual(len(record["sources"]), 1)
        restored = dynamic.normalise({"sources": [{"type": "curatr_list", "list_id": "old"}]}, {"old": "new"})
        self.assertEqual(restored["sources"][0]["list_id"], "new")

    def test_shared_cache_expiry_invalidation_and_offline_retention(self):
        with tempfile.TemporaryDirectory() as profile:
            now = [1000.0]
            cache = dynamic.SourceCache(profile, clock=lambda: now[0])
            fetch = Mock(return_value=[item("Saved")])
            cache.get("source", fetch)
            cache.get("source", fetch)
            self.assertEqual(fetch.call_count, 1)
            dynamic.invalidate(profile)
            cache.get("source", fetch)
            self.assertEqual(fetch.call_count, 2)
            now[0] += 61
            fetch.side_effect = OSError("offline")
            rows, stale = cache.get("source", fetch)
            self.assertTrue(stale)
            self.assertEqual(rows[0]["data"]["title"], "Saved")
            cache.get("source", fetch)
            self.assertEqual(fetch.call_count, 3)
            now[0] += 21
            fetch.side_effect = None
            rows, stale = cache.get("source", fetch)
            self.assertFalse(stale)
            self.assertEqual(fetch.call_count, 4)

    def test_concurrent_widgets_share_one_source_request(self):
        with tempfile.TemporaryDirectory() as profile, ThreadPoolExecutor(max_workers=2) as pool:
            entered, release = threading.Event(), threading.Event()
            def fetch():
                entered.set()
                release.wait(2)
                return [item("Shared")]
            fetch = Mock(side_effect=fetch)
            cache = dynamic.SourceCache(profile)
            first = pool.submit(cache.get, "same", fetch)
            self.assertTrue(entered.wait(1))
            second = pool.submit(cache.get, "same", fetch)
            release.set()
            self.assertEqual(first.result()[0], second.result()[0])
            self.assertEqual(fetch.call_count, 1)

    def test_local_list_changes_are_visible_without_cache_wait(self):
        curator = fixture_curator()
        with tempfile.TemporaryDirectory() as profile:
            curator.profile_dir = profile
            curator.state["ai_lists"] = [{"local_id": "a", "movies": [{"title": "Old", "media_type": "movie"}]}]
            record = {"sources": [{"type": "curatr_list", "list_id": "a"}]}
            self.assertEqual(dynamic.load(curator, record)["items"][0]["data"]["title"], "Old")
            curator.state["ai_lists"][0]["movies"][0]["title"] = "New"
            self.assertEqual(dynamic.load(curator, record)["items"][0]["data"]["title"], "New")
            self.assertEqual(list(Path(profile).rglob("*")), [])

    def test_load_applies_alternation_and_explains_missing_dates(self):
        curator = fixture_curator()
        with tempfile.TemporaryDirectory() as profile:
            curator.profile_dir = profile
            curator.state["ai_lists"] = [{"local_id": str(i), "movies": [
                {"title": "%da" % i, "media_type": "movie"}, {"title": "%db" % i, "media_type": "movie"}]} for i in range(3)]
            record = {"sources": [{"type": "curatr_list", "list_id": str(i)} for i in range(3)], "alternate_sources": True}
            result = dynamic.load(curator, record)
            self.assertEqual([r["data"]["title"] for r in result["items"]], ["0a", "1a", "2a", "0b", "1b", "2b"])
            self.assertIn("alternating between sources", result["warnings"][0])
            record["alternate_sources"] = False
            result = dynamic.load(curator, record)
            self.assertEqual([r["data"]["title"] for r in result["items"]], ["0a", "0b", "1a", "1b", "2a", "2b"])
            self.assertIn("in source order", result["warnings"][0])
            self.assertEqual(list(Path(profile).rglob("*")), [])

    def test_native_fetch_preserves_urls_resume_and_skips_navigation(self):
        resume = {"position": 100, "total": 2500}
        native = item("Continue", "episode", resume=resume, season=1, episode=3)["data"]
        curator = Mock()
        curator._kodi_json_rpc.return_value = {"files": [native, {"label": "Next page", "file": "plugin://source/next"},
                                                        {"label": "Recursive", "file": "plugin://plugin.video.curatr/?action=run"}]}
        rows = dynamic._native_rows(curator, {"path": "plugin://source/continue"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data"], native)
        self.assertEqual(rows[0]["data"]["resume"], resume)


class PersistenceChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addon = FakeAddon(self.tmp.name)
        class File:
            def __init__(self, path, mode): self.handle = open(path, mode)
            def read(self): return self.handle.read()
            def write(self, value): return self.handle.write(value)
            def close(self): self.handle.close()
        def remove(path):
            try: os.remove(path)
            except FileNotFoundError: pass
            return True
        self.patches = [patch.object(core.xbmcvfs, "File", File, create=True),
                        patch.object(core.xbmcvfs, "delete", remove, create=True),
                        patch.object(core.xbmcvfs, "rename", lambda a, b: os.replace(a, b) or True, create=True)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def curator(self):
        return core.Curator(self.addon, update_status=False, init_clients=False)

    def test_concurrent_saves_keep_dynamic_local_and_folder_changes(self):
        first = self.curator()
        dynamic.store(first, {"id": "a", "name": "A"})
        left, right = self.curator(), self.curator()
        dynamic.store(left, {"id": "b", "name": "B"})
        right.state["ai_lists"] = [{"local_id": "local", "name": "Keep"}]
        right._store_widget_folder({"id": "folder", "name": "Folder", "entries": [{"id": "shortcut", "type": "dynamic_list", "list_id": "a"}]})
        restored = self.curator()
        self.assertEqual({r["id"] for r in dynamic.records(restored)}, {"a", "b"})
        self.assertEqual(restored.state["ai_lists"][0]["local_id"], "local")
        self.assertEqual(restored.state["widget_folders"][0]["id"], "folder")
        self.assertEqual({r["id"] for r in json.loads(Path(restored.recovery_state_path).read_text())["dynamic_lists"]}, {"a", "b"})

    def test_delete_last_dynamic_list_does_not_resurrect_from_backup(self):
        curator = self.curator()
        dynamic.store(curator, {"id": "a", "name": "A"})
        curator.state["dynamic_lists"] = []
        curator._save_state()
        self.assertEqual(dynamic.records(self.curator()), [])
        self.assertTrue(json.loads(Path(curator.recovery_state_path).read_text())["dynamic_lists"])

    def test_backup_round_trip_preserves_definitions_and_folder_actions(self):
        curator = self.curator()
        self.assertFalse(dynamic.normalise({})["alternate_sources"])
        dynamic.store(curator, {"id": "dynamic", "name": "Merged", "alternate_sources": True,
                                "sources": [{"type": "external_path", "path": "plugin://source/path"}]})
        self.assertTrue(dynamic.by_id(self.curator(), "dynamic")["alternate_sources"])
        curator._store_widget_folder({"id": "folder", "name": "Folder", "entries": [
            {"id": "action", "type": "curatr_action", "shortcut": "quick_pick"},
            {"id": "list", "type": "dynamic_list", "list_id": "dynamic"}]})
        curator.record_activity = Mock()
        with patch.object(core.xbmcgui, "Dialog") as dialog:
            dialog.return_value.browseSingle.return_value = self.tmp.name
            backup = curator.export_backup()
            payload = json.loads(Path(backup).read_text())
            self.assertEqual(payload["dynamic_lists"][0]["id"], "dynamic")
            self.assertTrue(payload["dynamic_lists"][0]["alternate_sources"])
            self.assertNotIn("dynamic_cache", payload)
            curator.state["dynamic_lists"] = []
            curator.state["widget_folders"] = []
            curator._save_state()
            dialog.return_value.browseSingle.return_value = backup
            dialog.return_value.yesno.return_value = True
            self.assertTrue(curator.import_backup())
        restored = self.curator()
        self.assertEqual(dynamic.by_id(restored, "dynamic")["sources"][0]["path"], "plugin://source/path")
        self.assertTrue(dynamic.by_id(restored, "dynamic")["alternate_sources"])
        self.assertEqual([e["type"] for e in restored.widget_folders()[0]["entries"]], ["curatr_action", "dynamic_list"])


class NavigationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, "argv", ["plugin://plugin.video.curatr/", "1", ""]):
            cls.plugin = importlib.import_module("plugin")

    def test_home_folder_create_action_dispatches_from_its_serialized_path(self):
        p = self.plugin
        entry = {"id": "create & one", "type": "curatr_action", "shortcut": "create_list"}
        folder = {"id": "folder & one", "entries": [entry]}
        curator = Mock(state={})
        curator.widget_folder_by_id.return_value = folder
        curator.create_list_interactive.return_value = None
        with patch.object(p.xbmcgui, "getCurrentWindowId", return_value=10000), \
             patch.object(p.xbmc, "getCondVisibility", return_value=False), \
             patch.object(p.xbmcplugin, "addDirectoryItem", return_value=True) as add, \
             patch.object(p.xbmc, "executebuiltin") as execute:
            p._add_curatr_shortcut(curator, folder, entry)
            _handle, path, li = add.call_args.args
            self.assertFalse(add.call_args.kwargs["isFolder"])
            # A rebuilt widget only needs its path; no custom properties or tags.
            li.properties.clear()
            li.setInfo("video", {"title": "Create a New List"})
            self.assertTrue(path.startswith("favourites://"))
            command = unquote(path.removeprefix("favourites://"))
            self.assertTrue(command.startswith("RunPlugin("))
            target = command[len("RunPlugin("):-1]
            self.assertTrue(target.startswith("plugin://plugin.video.curatr/"))
            argv = ["plugin://plugin.video.curatr/", "-1", "?" + urlsplit(target).query]
            with patch.object(sys, "argv", argv), patch.object(p, "HANDLE", -1), \
                 patch.object(p, "Curator", return_value=curator), \
                 patch.object(p, "background_source", return_value=""):
                p.main()
            curator.widget_folder_by_id.assert_called_once_with(folder["id"])
            curator.create_list_interactive.assert_called_once_with()
            execute.assert_not_called()

    def test_home_dialog_routes_use_executable_paths_but_directories_keep_plugin_urls(self):
        p = self.plugin
        curator = Mock(state={})
        with patch.object(p.xbmcgui, "getCurrentWindowId", return_value=10000), \
             patch.object(p.xbmc, "getCondVisibility", return_value=False), \
             patch.object(p.xbmcplugin, "addDirectoryItem", return_value=True) as add:
            p._add_route_action("Settings", "edit_list", list_id="list & one")
            self.assertTrue(add.call_args.args[1].startswith("favourites://"))
            entry = {"id": "lists", "shortcut": "my_lists"}
            p._add_curatr_shortcut(curator, {"id": "folder"}, entry)
            self.assertTrue(add.call_args.args[1].startswith("plugin://"))
            self.assertTrue(add.call_args.kwargs["isFolder"])

    def test_action_probe_and_selected_click_take_distinct_paths(self):
        p = self.plugin
        with patch.object(p, "Curator", return_value=Mock(state={})), \
             patch.object(p, "background_source", return_value=""), \
             patch.object(p, "_run_command") as run:
            for command in ("create", "quick", "customise_theme", "dynamic_create"):
                with patch.object(p, "_params", return_value={"action": "run", "command": command}):
                    with patch.object(p, "HANDLE", 12):
                        p.main()
                    run.assert_not_called()
                    with patch.object(p, "HANDLE", -1):
                        p.main()
                    run.assert_called_once()
                    self.assertEqual(run.call_args.args[1], command)
                    run.reset_mock()

    def test_find_similar_uses_the_window_at_click_time(self):
        p = self.plugin
        params = {"action": "open_similar", "title": "Arrival", "tmdb_id": "329865"}
        with patch.object(p, "Curator", return_value=Mock(state={})), \
             patch.object(p, "background_source", return_value=""), \
             patch.object(p, "_params", return_value=params), \
             patch.object(view_refresh.xbmc, "executebuiltin") as execute, \
             patch.object(view_refresh.xbmc, "getCondVisibility", return_value=False):
            with patch.object(p, "HANDLE", 12):
                p.main()
            execute.assert_not_called()
            for window_id, prefix in ((10000, "ActivateWindow(Videos,"), (10025, "Container.Update(")):
                with patch.object(p, "HANDLE", -1), \
                     patch.object(view_refresh.xbmcgui, "getCurrentWindowId", return_value=window_id):
                    p.main()
                command = execute.call_args.args[0]
                self.assertTrue(command.startswith(prefix))
                self.assertIn("action=similar_preview", command)
                self.assertIn("tmdb_id=329865", command)

    def test_preview_edits_replace_history_and_home_refresh_preserves_focus(self):
        url = "plugin://plugin.video.curatr/?action=list_preview&token=example"
        with patch.object(view_refresh.xbmcgui, "getCurrentWindowId", return_value=10025):
            self.assertEqual(view_refresh.directory_command(url), 'Container.Update("%s")' % url)
            self.assertEqual(view_refresh.directory_command(url, replace=True), 'Container.Update("%s",replace)' % url)
        with patch.object(view_refresh.xbmcgui, "getCurrentWindowId", return_value=10000), \
             patch.object(view_refresh.xbmc, "getCondVisibility", return_value=False), \
             patch.object(view_refresh.xbmc, "getInfoLabel", return_value="plugin.video.curatr"), \
             patch.object(view_refresh.xbmc, "executebuiltin") as execute:
            view_refresh.refresh_if_changed("changed", {"ai_lists": []})
            execute.assert_not_called()
            self.assertEqual(view_refresh.directory_command(url, replace=True), 'ActivateWindow(Videos,"%s",return)' % url)

    def test_action_shortcuts_never_execute_during_directory_probes(self):
        p = self.plugin
        curator = Mock(state={})
        curator.widget_folder_by_id.return_value = {"id": "folder", "entries": [{"id": "quick", "type": "curatr_action", "shortcut": "quick_pick"}]}
        with patch.object(p, "_run_command") as run:
            p.HANDLE = 12
            p._folder_shortcut(curator, {"folder_id": "folder", "entry_id": "quick"})
            run.assert_not_called()
            curator.widget_folder_by_id.assert_not_called()
            p.HANDLE = -1
            p._folder_shortcut(curator, {"folder_id": "folder", "entry_id": "quick"})
            run.assert_called_once_with(curator, "quick")
            run.reset_mock()
            curator.widget_folder_by_id.return_value["entries"] = []
            p._folder_shortcut(curator, {"folder_id": "folder", "entry_id": "quick"})
            run.assert_not_called()
        p.HANDLE = 1

    def test_native_render_preserves_playback_metadata_and_context(self):
        p = self.plugin
        data = item("Episode", "episode", season=1, episode=2, resume={"position": 80, "total": 1500})["data"]
        with patch.object(p.xbmcplugin, "addDirectoryItem") as add:
            p._add_native_dynamic(data, p._folder_context("folder"))
            args, kwargs = add.call_args
            self.assertEqual(args[1], data["file"])
            li = args[2]
            self.assertFalse(kwargs["isFolder"])
            self.assertEqual(li.info["video"]["mediatype"], "episode")
            self.assertEqual(li.properties["ResumeTime"], "80")
            self.assertEqual(li.context[0][0], "Folder Settings")
            self.assertIn("folder_id=folder", li.context[0][1])
        data.update(filetype="directory", type="tvshow")
        with patch.object(p.xbmcplugin, "addDirectoryItem") as add:
            p._add_native_dynamic(data, [])
            self.assertTrue(add.call_args.kwargs["isFolder"])

    def test_dynamic_settings_have_only_appearance_content_and_no_ai(self):
        draft = dynamic.normalise({"name": "Combined"})
        obj, controls = window(dynamic_settings.DynamicSettingsWindow, str(ROOT), draft, lambda field, d: d, lambda f, d: f)
        self.assertFalse(controls[102].visible)
        self.assertFalse(controls[1102].visible)
        self.assertFalse(controls[204].visible)
        self.assertFalse(controls[20].visible)
        self.assertEqual(controls[20].label, "")
        obj.onClick(101)
        self.assertEqual([controls[cid].label for cid in (200, 201, 202, 203, 204)], ["sources", "sort", "direction", "alternate_sources", "count"])
        self.assertTrue(controls[204].visible)
        obj.draft["sort"] = "source"
        obj._show_tab("content")
        self.assertFalse(controls[202].enabled)
        self.assertTrue(controls[203].enabled)
        obj.draft["sort"] = "title"
        obj._show_tab("content")
        self.assertTrue(controls[202].enabled)
        self.assertFalse(controls[203].enabled)
        obj._show_tab("appearance")
        self.assertFalse(controls[204].visible)
        obj.onClick(301)
        self.assertEqual(obj.result, "create")

    def test_sources_manager_moves_and_removes_without_changing_saved_list(self):
        curator = fixture_curator()
        draft = dynamic.normalise({"sources": [{"id": str(i), "type": "external_path", "path": "plugin://source/%d" % i, "name": str(i)} for i in range(3)]})
        original = copy.deepcopy(draft)
        def manage(*args):
            entries, actions, execute, add = args[4:]
            self.assertEqual(entries()[0]["key"], "0")
            execute("2", "move_front")
            self.assertEqual(entries()[0]["key"], "2")
            execute("1", "remove")
            return None
        def sources_window(*args):
            obj = Mock(failed=False)
            obj.doModal.side_effect = lambda: manage(*args)
            return obj
        with patch.object(dynamic_settings, "SourcesWindow", side_effect=sources_window):
            updated = dynamic_settings._edit_sources(curator, draft)
        self.assertEqual([s["id"] for s in updated["sources"]], ["2", "0"])
        self.assertEqual([s["id"] for s in original["sources"]], ["0", "1", "2"])
        curator._save_state.assert_not_called()


class RequestAndEpisodeChecks(unittest.TestCase):
    def test_history_and_schedules_do_not_pollute_catalogue_filters(self):
        from lib.keyword_matcher import parse_prompt
        from lib.list_request import parse_request
        for phrase in ("crime films I haven't watched in the last 2 years", "crime films not watched in the last 2 months"):
            rules = parse_prompt(phrase)
            self.assertEqual(rules["history_mode"], "stale")
            self.assertEqual((rules["year_min"], rules["year_max"]), (0, 0))
            self.assertEqual(rules["request_rules"]["window"], "")
        rules = parse_prompt("films similar to Up, update daily and hide watched items")
        self.assertEqual(rules["reference_movies"], [{"title": "Up", "year": 0}])
        self.assertEqual(rules["request_rules"]["refresh_hours"], 24)
        self.assertEqual(parse_prompt("films by Stephen King I've seen twice")["person_query"], "Stephen King")
        self.assertEqual(parse_request("films from my Kodi library")["source"], "")

    def test_episode_source_cycles_and_date_editor_uses_an_editable_window(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import parse_prompt, confirmation_parts
        obj = object.__new__(KeywordConfirmWindow)
        obj.rules = parse_prompt("my episodes in next 45 days")
        with patch("lib.keyword_confirm.xbmcgui.Dialog") as dialog:
            for expected in ("kodi", "all", "trakt"):
                obj._edit_part(next(p for p in confirmation_parts(obj.rules) if p["field"] == "episode_source"))
                self.assertEqual(obj.rules["request_rules"]["source"], expected)
            dialog.return_value.input.assert_not_called()
            part = next(p for p in confirmation_parts(obj.rules) if p["field"] == "airing_window")
            dialog.return_value.input.return_value = "next 45 days"
            obj._edit_part(part)
            self.assertEqual(dialog.return_value.input.call_args.kwargs["defaultt"], "next 45 days")
            self.assertEqual(obj.rules["request_rules"]["window_days"], 45)

    def test_episode_tags_can_be_removed_and_restored_from_plus(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import parse_prompt, confirmation_parts
        from lib.list_request import validate_request
        obj = object.__new__(KeywordConfirmWindow)
        obj.rules = parse_prompt("my episodes in next 45 days refreshed every day")
        original = copy.deepcopy(obj.rules["request_rules"])
        for field in ("episode_source", "airing_window"):
            part = next(p for p in confirmation_parts(obj.rules) if p["field"] == field)
            obj._remove_part(part)
            self.assertNotIn(field, [p["field"] for p in confirmation_parts(obj.rules)])
            self.assertEqual(validate_request(obj.rules["request_rules"]), obj.rules["request_rules"])
        with patch("lib.keyword_confirm.xbmcgui.Dialog") as dialog:
            for label, value in (("Episode source", None), ("Airing window", "next 45 days")):
                dialog.return_value.select.side_effect = lambda title, rows, label=label: rows.index(label)
                dialog.return_value.input.return_value = value
                obj._add_filter()
            self.assertEqual(obj.rules["request_rules"], original)
            self.assertEqual(dialog.return_value.input.call_count, 1)
            dialog.return_value.select.side_effect = None
            dialog.return_value.select.return_value = -1
            obj._add_filter()
            for label in ("Episode source", "Airing window", "Refresh schedule"):
                self.assertNotIn(label, dialog.return_value.select.call_args.args[1])
            self.assertNotIn("Actor", dialog.return_value.select.call_args.args[1])

    def test_removing_all_episode_tags_survives_save_and_reopening(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import parse_prompt, confirmation_parts
        prompt = "my episodes airing next 45 days refreshed every day"
        rules = parse_prompt(prompt)
        draft = {"prompt": prompt, "content_type": "episodes", "generation_method": "keyword",
                 "request_rules": copy.deepcopy(rules["request_rules"]), "keyword_rules": rules,
                 "regeneration_enabled": True, "regeneration_interval_hours": 24}
        before = copy.deepcopy(draft)
        def remove_tags(_path, _prompt, edited, **_kwargs):
            obj = object.__new__(KeywordConfirmWindow)
            obj.rules = edited
            for part in confirmation_parts(edited): obj._remove_part(part)
            return "create"
        curator = fixture_curator()
        with patch.object(core, "confirm_keyword_rules", side_effect=remove_tags):
            updated = curator._edit_keyword_list_draft(draft)
        self.assertIsNotNone(updated)
        self.assertEqual(draft, before)
        self.assertTrue(updated["regeneration_enabled"])
        self.assertEqual(updated["regeneration_interval_hours"], 24)
        curator._prepare_request_draft(updated)
        reopened = curator._draft_keyword_rules(updated)
        self.assertEqual(confirmation_parts(reopened), [])
        self.assertGreater(reopened["confidence"], 0)
        self.assertEqual(reopened["request_rules"]["content_type"], "episodes")
        for field in ("source", "window"):
            self.assertEqual(reopened["request_rules"][field], "")
        self.assertEqual(reopened["request_rules"]["refresh_mode"], "on")

    def test_cancelled_episode_tag_edits_keep_the_original_draft(self):
        prompt = "my episodes airing next 45 days update daily"
        rules = core.parse_prompt(prompt)
        draft = {"prompt": prompt, "content_type": "episodes", "generation_method": "keyword",
                 "request_rules": copy.deepcopy(rules["request_rules"]), "keyword_rules": rules,
                 "regeneration_enabled": True}
        before = copy.deepcopy(draft)
        def cancel(_path, _prompt, edited, **_kwargs):
            edited["request_rules"].update(source="", window="", window_days=0, refresh_mode="", refresh_hours=0)
            return None
        with patch.object(core, "confirm_keyword_rules", side_effect=cancel):
            self.assertIsNone(fixture_curator()._edit_keyword_list_draft(draft))
        self.assertEqual(draft, before)

    def test_airing_window_inputs_preserve_existing_rules_on_invalid_or_cancelled_input(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import parse_prompt, confirmation_parts
        obj = object.__new__(KeywordConfirmWindow)
        obj.rules = parse_prompt("episodes in next 45 days update daily")
        with patch("lib.keyword_confirm.xbmcgui.Dialog") as dialog:
            part = next(p for p in confirmation_parts(obj.rules) if p["field"] == "airing_window")
            for value in (None, "", "nonsense"):
                before = copy.deepcopy(obj.rules)
                dialog.return_value.input.return_value = value
                obj._edit_part(part)
                self.assertEqual(obj.rules, before)
            dialog.return_value.input.return_value = "next calendar month"
            obj._edit_part(next(p for p in confirmation_parts(obj.rules) if p["field"] == "airing_window"))
            self.assertEqual(obj.rules["request_rules"]["window"], "next_calendar_month")

    def test_inflected_refresh_wording_is_recognised_without_polluting_reference_titles(self):
        from lib.keyword_matcher import parse_prompt
        from lib.list_request import operational_request
        examples = {"refreshed every day": 24, "refreshing daily": 24, "updated weekly": 168,
                    "updating every two days": 48, "regenerated every 12 hours": 12}
        for phrase, hours in examples.items():
            rules = parse_prompt("films similar to Up, " + phrase)
            self.assertEqual(rules["reference_movies"], [{"title": "Up", "year": 0}], phrase)
            self.assertEqual(rules["request_rules"]["refresh_hours"], hours, phrase)
            self.assertEqual((rules["year_min"], rules["year_max"]), (0, 0))
            self.assertTrue(operational_request(phrase))

    def test_episode_tags_use_the_standard_minus_plus_clicks_and_focus_navigation(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import parse_prompt, confirmation_parts
        obj = object.__new__(KeywordConfirmWindow)
        prompt = "My episodes releasing next refreshed every day"
        KeywordConfirmWindow.__init__(obj, str(ROOT), prompt, parse_prompt(prompt), "", True, "Use Filters", True)
        xml = ET.parse(ROOT / "resources/skins/Default/1080i/curatr-keyword-confirm.xml")
        controls = {int(c.get("id")): FakeControl() for c in xml.iter("control") if c.get("id")}
        obj.getControl = controls.__getitem__
        obj.addControl = Mock()
        obj.addControls = Mock()
        obj.removeControls = Mock()
        obj.setFocus = Mock()
        obj.close = Mock()
        obj.onInit()
        self.assertNotEqual(obj.result, "fallback")
        self.assertEqual(len(obj.filter_groups), 2)
        for field in ("episode_source", "airing_window"):
            parts = confirmation_parts(obj.rules)
            index = next(i for i, p in enumerate(parts) if p["field"] == field)
            minus = next(ident for ident, action in obj.control_actions.items() if action == ("remove", index))
            obj.onClick(minus)
            self.assertNotIn(field, [p["field"] for p in confirmation_parts(obj.rules)])
        self.assertIn("all shows", controls[12].label)
        self.assertIn("next 30 days", controls[12].label)
        obj.onClick(obj.CREATE_ID)
        self.assertEqual(obj.result, "create")
        with patch("lib.keyword_confirm.xbmcgui.Dialog") as dialog:
            dialog.return_value.select.side_effect = lambda title, rows: rows.index("Episode source")
            plus = next(ident for ident, action in obj.control_actions.items() if action == ("add", -1))
            obj.onClick(plus)
        self.assertEqual(obj.rules["request_rules"]["source"], "trakt")
        minus, label = obj.filter_groups[0]["controls"]
        self.assertIs(minus.navigation[3], label)
        self.assertIs(label.navigation[2], minus)
        self.assertEqual(controls[100].xy[1] - (140 + controls[14].height), 40)

    def test_refresh_schedule_is_not_a_keyword_filter_or_plus_option(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import confirmation_parts, format_rules, parse_prompt
        for prompt in ("crime films", "drama TV shows", "my episodes"):
            for schedule in ("", "update daily", "manual refresh"):
                obj = object.__new__(KeywordConfirmWindow)
                obj.rules = parse_prompt(prompt + " " + schedule)
                self.assertNotIn("refresh", [p["field"] for p in confirmation_parts(obj.rules)])
                self.assertNotIn("refresh", format_rules(obj.rules).casefold())
                with patch("lib.keyword_confirm.xbmcgui.Dialog") as dialog:
                    dialog.return_value.select.return_value = -1
                    obj._add_filter()
                    self.assertNotIn("Refresh schedule", dialog.return_value.select.call_args.args[1])
        self.assertEqual(parse_prompt("update daily")["confidence"], 0)

    def test_keyword_filter_edits_preserve_custom_behavior_refresh_settings(self):
        from lib.keyword_matcher import parse_prompt
        prompt = "crime episodes next 45 days update daily"
        for enabled in (True, False):
            rules = parse_prompt(prompt)
            draft = {"prompt": prompt, "content_type": "episodes", "generation_method": "keyword",
                     "keyword_rules": rules, "request_rules": copy.deepcopy(rules["request_rules"]),
                     "regeneration_enabled": enabled, "regeneration_interval_hours": 37}
            before = copy.deepcopy(draft)
            def edit_filters(_path, _prompt, edited, **_kwargs):
                edited["request_rules"].update(window="next_days", window_days=7)
                return "create"
            with patch.object(core, "confirm_keyword_rules", side_effect=edit_filters):
                updated = fixture_curator()._edit_keyword_list_draft(draft)
            self.assertEqual(draft, before)
            self.assertIs(updated["regeneration_enabled"], enabled)
            self.assertEqual(updated["regeneration_interval_hours"], 37)
            self.assertEqual(updated["request_rules"]["window_days"], 7)

    def test_new_keyword_request_can_seed_the_behavior_refresh_schedule(self):
        draft = {"prompt": "crime episodes", "generation_method": "keyword", "content_type": "episodes",
                 "regeneration_enabled": False, "regeneration_interval_hours": 37}
        with patch.object(core, "confirm_keyword_rules", side_effect=("edit", "create")), patch.object(core.xbmcgui, "Dialog") as dialog:
            dialog.return_value.input.return_value = "crime episodes refreshed every two days"
            updated = fixture_curator()._edit_keyword_list_draft(draft)
        self.assertTrue(updated["regeneration_enabled"])
        self.assertEqual(updated["regeneration_interval_hours"], 48)

    def test_removed_episode_constraints_use_the_default_public_window(self):
        from datetime import date
        from lib import episode_lists
        from lib.list_request import date_window, validate_request
        with tempfile.TemporaryDirectory() as profile:
            curator = self.episode_curator(profile)
            curator._has_oauth = Mock(return_value=False)
            curator.trakt.calendar_shows.return_value = [self.episode()]
            request = validate_request({"content_type": "episodes", "hide_watched": False})
            with patch.object(episode_lists, "date_window", side_effect=lambda value: date_window(value, date(2026, 10, 5))):
                rows, window = episode_lists.generate(curator, request, {}, 20)
            self.assertEqual([row["episode"] for row in rows], [1])
            curator.trakt.calendar_shows.assert_called_once()
            self.assertFalse(curator.trakt.calendar_shows.call_args.kwargs["personal"])
            self.assertLessEqual(curator.trakt.calendar_shows.call_args.args[1], 33)
            self.assertEqual(window["start"], "2026-10-05")
            self.assertEqual(window["end"], "2026-11-03")
            self.assertEqual(request["source"], "")
            self.assertEqual(request["window"], "")

    def test_stale_history_uses_timezone_aware_elapsed_time(self):
        from datetime import datetime, timezone
        from lib.episode_lists import history_matches
        now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
        rules = {"history_mode": "stale", "history_days": 1}
        self.assertTrue(history_matches(1, "2026-10-04T14:00:00+02:00", rules, now))
        self.assertFalse(history_matches(1, "2026-10-04T12:01:00Z", rules, now))
        self.assertFalse(history_matches(0, "2020-01-01T00:00:00Z", rules, now))
        self.assertFalse(history_matches(1, "invalid", rules, now))

    def test_history_language_and_display_roundtrip(self):
        from lib.keyword_matcher import parse_prompt, confirmation_parts
        examples = {"unwatched": "never", "not watched yet": "never", "not watched before": "never",
                    "not watched in the last two years": "stale", "last seen 6 weeks ago": "stale",
                    "seen twice": "plays", "watched at most 3 times": "plays", "watched before": "watched",
                    "including watched items": "include"}
        for phrase, mode in examples.items():
            rules = parse_prompt(phrase)
            self.assertEqual(rules["history_mode"], mode, phrase)
            part = next(p for p in confirmation_parts(rules) if p["field"] == "history")
            edited = parse_prompt(part["connector"] + " " + part["text"])
            for key in ("history_mode", "history_days", "history_plays", "history_comparison", "exclude_watched"):
                self.assertEqual(rules[key], edited[key], (phrase, key))

    def test_history_add_keeps_false_and_invalid_input_keeps_existing_rules(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import parse_prompt
        for phrase in ("not watched in the last 2 years", "seen twice", "nonsense"):
            obj = object.__new__(KeywordConfirmWindow)
            obj.rules = parse_prompt("crime")
            original = copy.deepcopy(obj.rules)
            with patch("lib.keyword_confirm.xbmcgui.Dialog") as dialog:
                dialog.return_value.select.side_effect = lambda title, rows: rows.index("Viewing history")
                dialog.return_value.input.return_value = phrase
                obj._add_filter()
                if phrase == "nonsense":
                    self.assertEqual(obj.rules, original)
                    dialog.return_value.ok.assert_called_once()
                else:
                    self.assertFalse(obj.rules["exclude_watched"])
                    self.assertTrue(obj.rules["history_mode"])

    def test_history_edit_does_not_erase_never_watched(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import parse_prompt, confirmation_parts
        obj = object.__new__(KeywordConfirmWindow)
        obj.rules = parse_prompt("never watched")
        part = next(p for p in confirmation_parts(obj.rules) if p["field"] == "history")
        with patch("lib.keyword_confirm.xbmcgui.Dialog") as dialog:
            dialog.return_value.input.return_value = part["text"]
            obj._edit_part(part)
        self.assertEqual(obj.rules["history_mode"], "never")

    def test_request_interpretation_is_cached_and_manual_schedule_wins(self):
        from lib.list_request import parse_request
        curator = fixture_curator()
        curator.ai = Mock()
        prompt = "make me a list of my episodes airing in the next month, update daily and hide watched items"
        curator.ai.interpret_list_request.return_value = parse_request(prompt)
        draft = {"prompt": prompt, "generation_method": "ai", "content_type": "movies"}
        curator._prepare_request_draft(draft)
        self.assertEqual(draft["content_type"], "episodes")
        self.assertEqual(draft["regeneration_interval_hours"], 24)
        draft["regeneration_interval_hours"] = 37
        curator._prepare_request_draft(draft)
        self.assertEqual(draft["regeneration_interval_hours"], 37)
        curator.ai.interpret_list_request.assert_called_once_with(prompt)

    def test_episode_date_windows_cross_years_and_leap_months(self):
        from datetime import date
        from lib.list_request import date_window, parse_request, validate_request
        self.assertEqual(date_window(parse_request("episodes during next month"), date(2026, 12, 20)), (date(2027, 1, 1), date(2027, 2, 1)))
        self.assertEqual(date_window(parse_request("episodes during next calendar month"), date(2028, 1, 10)), (date(2028, 2, 1), date(2028, 3, 1)))
        self.assertEqual(date_window(parse_request("episodes in the next month"), date(2026, 12, 20)), (date(2026, 12, 20), date(2027, 1, 19)))
        with self.assertRaises(ValueError): validate_request({"window_days": 1000})

    def episode_curator(self, profile):
        curator = fixture_curator()
        curator.profile_dir = profile
        curator.addon = FakeAddon(profile)
        curator.state.update(access_token="test", trakt_username="fixture")
        curator._preference_history_mode = Mock(return_value="trakt")
        curator.trakt = Mock(token_store={"created_at": 1})
        curator.trakt.watched_shows.return_value = []
        return curator

    @staticmethod
    def episode(number=1, aired="2026-10-06T12:00:00Z", ident=100):
        return {"first_aired": aired, "episode": {"title": "Episode", "season": 1, "number": number, "ids": {"trakt": ident, "tmdb": 900 + ident}},
                "show": {"title": "Series", "year": 2020, "ids": {"trakt": 1, "tmdb": 20}, "genres": ["science-fiction"]}}

    def test_calendar_chunks_dedup_and_episode_watch_identity(self):
        from datetime import date
        from lib import episode_lists
        from lib.list_request import parse_request
        with tempfile.TemporaryDirectory() as profile:
            curator = self.episode_curator(profile)
            curator.trakt.calendar_shows.return_value = [self.episode(), self.episode(), self.episode(2, ident=101), self.episode(3, "2027-01-01T12:00:00Z", 102)]
            curator.trakt.watched_shows.return_value = [{"show": {"title": "Series", "year": 2020, "ids": {"trakt": 1}}, "seasons": [{"number": 1, "episodes": [{"number": 1, "plays": 1}]}]}]
            with patch.object(episode_lists, "date_window", return_value=(date(2026, 10, 5), date(2026, 12, 1))):
                items, _ = episode_lists.generate(curator, parse_request("my episodes in next 60 days hide watched"), {"genre_labels": ["Sci-Fi"]}, 50)
            self.assertEqual([row["episode"] for row in items], [2])
            self.assertEqual(items[0]["ids"]["tmdb"], 1001)
            self.assertEqual(items[0]["show_ids"]["tmdb"], 20)
            self.assertGreaterEqual(curator.trakt.calendar_shows.call_count, 2)
            self.assertTrue(all(call.args[1] <= 33 for call in curator.trakt.calendar_shows.call_args_list))
            curator.trakt.watched_shows.assert_called_once()

    def test_calendar_failure_keeps_saved_list_and_empty_window_is_valid(self):
        from datetime import date
        from lib import episode_lists
        from lib.list_request import parse_request
        with tempfile.TemporaryDirectory() as profile:
            curator = self.episode_curator(profile)
            curator.trakt.calendar_shows.side_effect = RuntimeError("Offline")
            original = {"local_id": "keep", "name": "Episodes", "movies": [{"title": "Keep"}], "content_type": "episodes"}
            with patch.object(episode_lists, "date_window", return_value=(date(2026, 10, 5), date(2026, 10, 10))):
                with self.assertRaises(RuntimeError):
                    curator._generate_episode_and_write("Episodes", "my episodes", 20, parse_request("my episodes"), {}, "keyword", managed_record=original, sync_to_trakt=False)
            self.assertEqual(original["movies"], [{"title": "Keep"}])
            curator._store_managed_record.assert_not_called()
        with tempfile.TemporaryDirectory() as profile:
            curator = self.episode_curator(profile)
            curator.trakt.calendar_shows.return_value = []
            record = curator._generate_episode_and_write("Episodes", "my episodes", 20, parse_request("my episodes update daily"), {}, "keyword", sync_to_trakt=False, persist=False)
            self.assertEqual(record["movies"], [])
            curator.save_list_preview({"record": record})
            curator._store_managed_record.assert_called_once()

    def test_episode_refresh_never_reinterprets_with_ai(self):
        from lib.list_request import parse_request
        curator = fixture_curator()
        curator.ai = Mock()
        record = {"local_id": "keep", "name": "Episodes", "prompt": "my episodes update daily", "content_type": "episodes", "generation_method": "ai", "request_rules": parse_request("my episodes update daily"), "count": 20}
        curator._managed_record_by_id = Mock(return_value=record)
        curator._generate_episode_and_write = Mock(return_value=dict(record, movies=[]))
        curator.refresh_list("keep", silent=True)
        curator._generate_episode_and_write.assert_called_once()
        curator.ai.interpret_list_request.assert_not_called()
        curator.ai.recommend.assert_not_called()

    def test_library_episodes_use_series_aliases_and_episode_specific_exclusions(self):
        from datetime import date
        from lib import episode_lists
        from lib.list_request import parse_request
        with tempfile.TemporaryDirectory() as profile:
            curator = self.episode_curator(profile)
            curator._preference_history_mode.return_value = "kodi"
            curator._kodi_json_rpc = Mock(side_effect=lambda method, params: {
                "tvshows": [{"tvshowid": 7, "title": "Series", "year": 2020, "uniqueid": {"tmdb": "20"}}]
            } if method.endswith("GetTVShows") else {"episodes": [{"tvshowid": 7, "season": 1, "episode": 1, "playcount": 3, "lastplayed": "2024-01-01 12:00:00"}]})
            curator.trakt.calendar_shows.return_value = [self.episode(1, "2026-10-03T12:00:00Z", 100), self.episode(2, "2026-10-03T13:00:00Z", 101), self.episode(3, "2026-10-03T14:00:00Z", 102)]
            curator.state["hidden_movies"] = [{"media_type": "movie", "trakt_id": 100}, {"media_type": "episode", "trakt_id": 101}]
            with patch.object(episode_lists, "date_window", return_value=(date(2026, 10, 1), date(2026, 10, 5))):
                watched, _ = episode_lists.generate(curator, parse_request("episodes from my Kodi library include watched"), {"history_mode": "plays", "history_comparison": "exact", "history_plays": 3}, 20)
                self.assertEqual([row["episode"] for row in watched], [1])
                unseen, _ = episode_lists.generate(curator, parse_request("episodes from my Kodi library hide watched"), {}, 20)
                self.assertEqual([row["episode"] for row in unseen], [3])
            curator.trakt.watched_shows.assert_not_called()
            self.assertTrue(all(not call.kwargs["personal"] for call in curator.trakt.calendar_shows.call_args_list))

    def test_explicit_manual_episode_refresh_overrides_the_global_default(self):
        from lib.list_request import parse_request
        curator = fixture_curator()
        curator._default_regeneration_enabled = Mock(return_value=True)
        with patch.object(core, "generate_episodes", return_value=([], {})):
            record = curator._generate_episode_and_write("Episodes", "episodes manual refresh", 20, parse_request("episodes manual refresh"), {}, "keyword", sync_to_trakt=False, persist=False)
        self.assertFalse(record["regeneration_enabled"])

    def test_missing_trakt_history_is_not_treated_as_unwatched(self):
        from lib.episode_lists import watch_history
        curator = fixture_curator()
        curator._preference_history_mode = Mock(return_value="trakt")
        curator._has_oauth = Mock(return_value=False)
        curator._public_username = Mock(return_value="")
        with self.assertRaisesRegex(RuntimeError, "watch history is not connected"):
            watch_history(curator)

    def test_dynamic_sources_hide_newly_watched_episodes_without_mutating_saved_items(self):
        from lib.episode_lists import show_tokens
        curator = fixture_curator()
        curator.profile_dir = fixtures.TEST_PROFILE.name
        episode = {"title": "Pilot", "ids": {"trakt": 100}, "show_ids": {"trakt": 1}, "showtitle": "Series", "season": 1, "episode": 1, "media_type": "episode"}
        saved = {"local_id": "source", "request_rules": {"hide_watched": True}, "movies": [episode]}
        before = copy.deepcopy(saved)
        curator._managed_record_by_id = Mock(return_value=saved)
        history = {(token, 1, 1): (1, "") for token in show_tokens({"ids": {"trakt": 1}})}
        with patch("lib.episode_lists.watch_history", return_value=history):
            result = dynamic.load(curator, {"sources": [{"type": "curatr_list", "list_id": "source", "name": "Episodes"}]})
        self.assertEqual(result["items"], [])
        self.assertEqual(saved, before)

    def test_episode_library_playback_and_empty_lists_preserve_episode_identity(self):
        with patch.object(sys, "argv", ["plugin://plugin.video.curatr/", "1", ""]):
            p = importlib.import_module("plugin")
        episode = {"title": "Pilot", "ids": {"trakt": 100, "tmdb": 200}, "show_ids": {"tmdb": 20}, "showtitle": "Series", "season": 1, "episode": 2, "media_type": "episode"}
        with patch.object(p, "_library_items", side_effect=lambda kind: [{"tvshowid": 7, "title": "Series", "uniqueid": {"tmdb": "20"}}] if kind == "show" else [{"tvshowid": 7, "season": 1, "episode": 1, "file": "/wrong.mkv"}, {"tvshowid": 7, "season": 1, "episode": 2, "file": "/right.mkv"}]):
            self.assertEqual(p._library_target(episode, "episode"), "/right.mkv")
        with patch.object(p, "_library_target") as library:
            self.assertEqual(p._player_target(dict(episode, first_aired="2099-01-01T12:00:00Z")), ("", False, None))
            library.assert_not_called()
        curator = fixture_curator()
        curator.trakt = Mock()
        curator._managed_record_by_id = Mock(return_value={"content_type": "episodes", "movies": [], "trakt_id": 3})
        self.assertEqual(p._movie_rows_for_list(curator, "empty"), [])
        curator.trakt.list_items.assert_not_called()

    def test_episode_sync_uses_episode_ids_and_clears_old_episodes(self):
        curator = fixture_curator()
        from lib.trakt import TraktClient
        curator.trakt = Mock()
        curator.trakt._unique_int_ids = TraktClient._unique_int_ids
        curator.trakt.list_items.return_value = [{"movie": {"ids": {"trakt": 10}}}, {"episode": {"ids": {"trakt": 100}}}]
        curator._sync_list_items("remote", [10], [], [101])
        curator.trakt.add_episodes.assert_called_once_with("remote", {101})
        curator.trakt.remove_episodes.assert_called_once_with("remote", {100})
        curator.trakt.add_movies.assert_not_called()
        curator.trakt.remove_movies.assert_not_called()

    def test_episode_player_uses_series_ids_and_preserves_coordinates(self):
        from lib.player_registry import PlayerRegistry
        registry = object.__new__(PlayerRegistry)
        player = {"plugin": "plugin.video.example", "play_episode": "plugin://plugin.video.example/?id={tmdb_id}&season={season}&episode={episode}", "open_show": "plugin://plugin.video.example/?show={tmdb_id}"}
        movie = {"media_type": "episode", "title": "Episode", "showtitle": "Series", "ids": {"tmdb": 999}, "show_ids": {"tmdb": 20}, "season": 0, "episode": 2}
        self.assertEqual(registry.build_url(player, movie), "plugin://plugin.video.example/?id=20&season=0&episode=2")
        player.pop("play_episode")
        self.assertEqual(registry.build_url(player, movie), "plugin://plugin.video.example/?show=20")

    def test_episode_metadata_never_queries_movie_details(self):
        from lib.metadata_cache import MetadataCache
        with tempfile.TemporaryDirectory() as profile:
            cache = MetadataCache(FakeAddon(profile))
            tmdb = Mock(api_key="test")
            item = {"media_type": "episode", "title": "Episode", "ids": {"tmdb": 999}, "season": 1, "episode": 2}
            before = copy.deepcopy(item)
            cache.enrich([item], tmdb)
            tmdb.list_item_details.assert_not_called()
            self.assertEqual(item, before)

    def test_dynamic_preview_loads_two_sources_and_back_always_closes(self):
        with tempfile.TemporaryDirectory() as profile:
            curator = fixture_curator()
            curator.profile_dir = profile
            curator._managed_record_by_id = Mock(side_effect=lambda ident: {"movies": [{"title": ident, "ids": {"tmdb": ident}}]})
            draft = dynamic.normalise({"name": "Combined", "sources": [{"type": "curatr_list", "list_id": str(i)} for i in (1, 2)]})
            result = dynamic.load(curator, draft)
            self.assertEqual(len(result["items"]), 2)
            rows = [dict(row, art={}) for row in result["items"]]
            with patch.object(dynamic_settings, "prepare_items", return_value=rows), patch.object(dynamic_settings, "PreviewWindow") as preview:
                preview.return_value.failed = False
                dynamic_settings._preview(curator, draft)
                self.assertEqual(len(preview.call_args.args[4]()), 2)
                preview.return_value.doModal.assert_called_once()
                preview.return_value.close.assert_called_once()
            obj = object.__new__(dynamic_settings.PreviewWindow)
            obj.close = Mock()
            obj.onAction(Mock(getId=lambda: 92))
            obj.close.assert_called_once()
            self.assertNotEqual(obj.XML_FILENAME, dynamic_settings.CollectionManagerWindow.XML_FILENAME)

    def test_empty_dynamic_preview_is_explained_without_opening_a_modal(self):
        curator = fixture_curator()
        with patch.object(dynamic_settings.xbmcgui, "Dialog") as dialog, patch.object(dynamic_settings, "PreviewWindow") as preview:
            dynamic_settings._preview(curator, {"name": "Empty", "sources": []})
            dialog.return_value.ok.assert_called_once()
            preview.assert_not_called()
        with patch.object(dynamic_settings.xbmcgui, "Dialog") as dialog, patch.object(dynamic_settings.dynamic, "load", return_value={"items": [], "warnings": ["Source unavailable"]}), patch.object(dynamic_settings, "PreviewWindow") as preview:
            dynamic_settings._preview(curator, {"name": "Empty", "sources": [{"type": "curatr_list", "list_id": "1"}]})
            self.assertIn("Source unavailable", dialog.return_value.ok.call_args.args[1])
            preview.assert_not_called()

    def test_watch_snapshot_preserves_more_than_dynamic_source_limit(self):
        with tempfile.TemporaryDirectory() as profile:
            cache = dynamic.SourceCache(profile, max_items=20000)
            rows = [{"id": index} for index in range(2500)]
            self.assertEqual(len(cache.get("episodes", lambda: rows)[0]), 2500)
            self.assertEqual(len(cache.get("episodes", Mock(side_effect=RuntimeError))[0]), 2500)


if __name__ == "__main__":
    unittest.main(verbosity=2)

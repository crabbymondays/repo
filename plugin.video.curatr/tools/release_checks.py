import ast
import copy
import time
from unittest.mock import Mock, patch
from PIL import Image, ImageChops

import importlib
import os
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FakeAddon:
    def __init__(self, profile, settings=None):
        self.profile = profile
        self.settings = dict(settings or {})

    def getAddonInfo(self, key):
        return {
            "name": "curatr",
            "path": str(ROOT),
            "profile": self.profile,
            "version": "1.0.19",
            "id": "plugin.video.curatr",
        }.get(key, "")

    def getSetting(self, key):
        return self.settings.get(key, "")

    def getLocalizedString(self, _key):
        return ""


class FakeControl:
    def __init__(self, navigation_error=False, visibility_error=False):
        self.navigation_error = navigation_error
        self.visibility_error = visibility_error
        self.visible = True
        self.enabled = True
        self.label = ""
        self.items = []
        self.position = 0

    def setNavigation(self, *_args):
        if self.navigation_error:
            raise RuntimeError("unsupported on this platform")
        self.navigation = _args

    def setImage(self, value):
        self.image = value

    def setVisible(self, value):
        if self.visibility_error:
            raise RuntimeError("visibility unsupported on this platform")
        self.visible = bool(value)

    def setEnabled(self, value):
        self.enabled = bool(value)

    def setLabel(self, value, **kwargs):
        self.label = str(value)
        self.text_attributes = kwargs

    def setPosition(self, x, y):
        self.xy = (x, y)

    def setHeight(self, height):
        self.height = height

    def setText(self, value):
        self.label = value

    def getId(self):
        return self.control_id

    def setVisibleCondition(self, condition):
        self.condition = condition

    def reset(self):
        self.items = []

    def addItems(self, items):
        self.items.extend(items)

    def getSelectedPosition(self):
        return self.position

    def selectItem(self, position):
        self.position = int(position)


class FakeTag:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
        return call


class FakeListItem:
    def __init__(self, label="", offscreen=False):
        self.label = label
        self.offscreen = offscreen
        self.properties = {}
        self.info = {}
        self.art = {}
        self.context = []
        self.tag = FakeTag()

    def setProperty(self, key, value):
        self.properties[key] = value

    def setInfo(self, kind, value):
        self.info[kind] = value

    def setArt(self, value):
        self.art.update(value)

    def addContextMenuItems(self, value):
        self.context.extend(value)

    def getVideoInfoTag(self):
        return self.tag


class FakeWindow:
    def setProperty(self, key, value):
        if not hasattr(self, "properties"):
            self.properties = {}
        self.properties[key] = value


class FakeDynamicControl(FakeControl):
    next_id = 2000

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.args, self.kwargs = args, kwargs
        self.control_id = FakeDynamicControl.next_id
        FakeDynamicControl.next_id += 1


def install_kodi_stubs(profile):
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGDEBUG = 0
    xbmc.LOGINFO = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGERROR = 3
    xbmc.log = lambda *_args, **_kwargs: None
    xbmc.getCondVisibility = lambda _condition: False
    xbmc.executeJSONRPC = lambda _request: "{}"
    xbmc.executebuiltin = lambda *_args: None
    xbmc.sleep = lambda *_args: None
    xbmc.Actor = lambda *args: args
    xbmc.Monitor = type("Monitor", (), {})

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.WindowXMLDialog = FakeWindow
    xbmcgui.WindowDialog = type("WindowDialog", (), {})
    xbmcgui.ListItem = FakeListItem
    xbmcgui.Dialog = type("Dialog", (), {})
    xbmcgui.DialogProgress = type("DialogProgress", (), {})
    xbmcgui.ControlLabel = FakeDynamicControl
    xbmcgui.ControlImage = FakeDynamicControl
    xbmcgui.ControlButton = FakeDynamicControl
    xbmcgui.Window = lambda _id: types.SimpleNamespace(setProperty=lambda *_args: None)
    xbmcgui.NOTIFICATION_INFO = "info"
    xbmcgui.NOTIFICATION_WARNING = "warning"
    xbmcgui.NOTIFICATION_ERROR = "error"
    xbmcgui.getCurrentWindowId = lambda: 10025

    addon = FakeAddon(profile)
    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = lambda *_args, **_kwargs: addon

    added = []
    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.addDirectoryItem = (
        lambda handle, url, item, isFolder=False: added.append(
            (handle, url, item, isFolder)
        ) or True
    )
    xbmcplugin.setPluginCategory = lambda *_args: None
    xbmcplugin.setContent = lambda *_args: None
    xbmcplugin.setResolvedUrl = lambda *_args: None
    xbmcplugin.endOfDirectory = lambda *_args, **_kwargs: None

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda value: value
    xbmcvfs.exists = os.path.exists
    xbmcvfs.mkdirs = lambda value: os.makedirs(value, exist_ok=True)

    requests = types.ModuleType("requests")
    requests.RequestException = type("RequestException", (Exception,), {})
    requests.exceptions = types.SimpleNamespace(RequestException=requests.RequestException)
    class Session:
        def __init__(self):
            self.headers = {}
    requests.Session = Session

    for name, module in {
        "xbmc": xbmc,
        "xbmcgui": xbmcgui,
        "xbmcaddon": xbmcaddon,
        "xbmcplugin": xbmcplugin,
        "xbmcvfs": xbmcvfs,
        "requests": requests,
    }.items():
        sys.modules[name] = module
    return xbmc, xbmcgui, addon, added


class ReleaseChecks(unittest.TestCase):
    def test_themes_keep_distinct_light_tints(self):
        from lib.ui_theme import theme_palette

        palettes = {}
        for name in ("violet", "ocean", "emerald", "amber"):
            addon = FakeAddon("", {
                "interface_theme": name,
                "interface_light_mode": "true",
                "interface_custom_colours": "false",
            })
            palettes[name] = theme_palette(addon)
            self.assertNotEqual(palettes[name]["CuratrBackdrop"][-6:], "FFFFFF")
        self.assertEqual(len({row["CuratrBackdrop"] for row in palettes.values()}), 4)

    def test_mdblist_rating_normalisation(self):
        from lib.catalogue_clients import CatalogueError, MDBListClient

        client = MDBListClient("key")
        values = {"imdb": 8.2, "tomatoes": 75, "audience": 10, "metacritic": 64}
        client._api_post = lambda path, _payload: {
            "ratings": [{"id": 12, "rating": values[path.rsplit("/", 1)[-1]]}]
        }
        ratings = client.ratings_for_ids("movie", [12, 12, "bad"])[12]
        self.assertEqual(ratings["imdb"]["rating"], 8.2)
        self.assertEqual(ratings["tomatometerallcritics"]["percent"], 75)
        self.assertEqual(ratings["tomatometerallaudience"]["rating"], 1.0)
        self.assertEqual(ratings["metacritic"]["rating"], 6.4)

        calls = []
        def partial(path, _payload):
            calls.append(path)
            if len(calls) == 2:
                raise CatalogueError("temporary")
            return {"ratings": [{"id": 12, "rating": 7.1}]}
        client._api_post = partial
        self.assertEqual(client.ratings_for_ids("movie", [12])[12]["imdb"]["rating"], 7.1)
        self.assertEqual(len(calls), 2)

    def test_metadata_is_cached_and_merged(self):
        from lib.metadata_cache import MetadataCache

        with tempfile.TemporaryDirectory() as folder:
            cache = MetadataCache(FakeAddon(folder))

            class TMDB:
                api_key = "key"
                calls = 0

                @staticmethod
                def image_url(_path, _size):
                    return ""

                def list_item_details(self, tmdb_id, _media_type):
                    self.calls += 1
                    return {
                        "id": tmdb_id,
                        "vote_average": 7.4,
                        "vote_count": 250,
                        "credits": {},
                        "external_ids": {"imdb_id": "tt12"},
                    }

            class MDBList:
                api_key = "key"
                calls = 0

                def ratings_for_ids(self, _media_type, ids):
                    self.calls += 1
                    return {value: {"imdb": {"rating": 8.0, "votes": 0, "percent": 80}} for value in ids}

            tmdb, mdblist = TMDB(), MDBList()
            first = [{"title": "Test", "ids": {"tmdb": 12, "trakt": 99}}]
            cache.enrich(first, tmdb, mdblist)
            self.assertEqual(first[0]["ids"]["imdb"], "tt12")
            self.assertEqual(first[0]["ratings"]["tmdb"]["rating"], 7.4)
            self.assertEqual(first[0]["ratings"]["imdb"]["rating"], 8.0)
            second = [{"title": "Test", "ids": {"tmdb": 12}}]
            cache.enrich(second, tmdb, mdblist)
            self.assertEqual(tmdb.calls, 1)
            self.assertEqual(mdblist.calls, 1)

    def test_folder_settings_survives_navigation_api_failure(self):
        from lib.folder_settings import FolderSettingsWindow

        window = object.__new__(FolderSettingsWindow)
        FolderSettingsWindow.__init__(
            window, str(ROOT), {"name": "New Folder", "description": ""},
            lambda _field, draft: draft,
            lambda field, _draft: field.title(),
            content_rows=lambda _draft: [],
            content_actions=lambda _entry: [],
            content_handler=lambda draft, _key, _action: draft,
            content_add_handler=lambda draft: draft,
        )
        controls = {key: FakeControl(navigation_error=True) for key in (
            20, 29, 30, 100, 101, 200, 201, 202, 300, 301, 400, 410, 420
        )}
        controls[420].visibility_error = True
        window.getControl = controls.__getitem__
        window.setProperty = lambda *_args: None
        window.setFocus = lambda _control: (_ for _ in ()).throw(RuntimeError("focus"))
        closed = []
        window.close = lambda: closed.append(True)
        window.onInit()
        self.assertFalse(window.failed)
        self.assertFalse(closed)
        window._show_tab("contents")
        self.assertTrue(controls[400].visible)
        self.assertEqual(len(controls[400].items), 1)

    def test_collection_manager_keeps_actions_visible_and_in_sync(self):
        from lib.collection_manager import CollectionManagerWindow

        entries = [
            {"key": "one", "label": "One"},
            {"key": "two", "label": "Two"},
        ]
        window = object.__new__(CollectionManagerWindow)
        CollectionManagerWindow.__init__(
            window, str(ROOT), "Manage", "", "Create",
            lambda: entries,
            lambda entry: [{"key": "edit", "label": "Edit " + entry["label"]}],
            lambda _key, _action: None,
            lambda: None,
        )
        controls = {key: FakeControl() for key in (
            10, 11, 20, 21, 22, 100, 200, 300, 301
        )}
        focus = [100]
        window.getControl = controls.__getitem__
        window.setFocus = lambda control: focus.__setitem__(
            0, next(key for key, value in controls.items() if value is control)
        )
        window.getFocusId = lambda: focus[0]
        window.close = lambda: None
        window.onInit()
        self.assertTrue(controls[200].visible)
        self.assertEqual(window.active_key, "one")
        self.assertEqual(controls[200].items[0].label, "Edit One")

        controls[100].position = 1
        window.onAction(types.SimpleNamespace(getId=lambda: 4))
        self.assertEqual(window.active_key, "two")
        self.assertEqual(controls[200].items[0].label, "Edit Two")

        focus[0] = 200
        window.onAction(types.SimpleNamespace(getId=lambda: 92))
        self.assertEqual(focus[0], 100)

    def test_widget_gate_and_rating_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            xbmc, xbmcgui, _addon, added = install_kodi_stubs(folder)
            sys.argv = ["plugin://plugin.video.curatr/", "1", ""]
            sys.modules.pop("plugin", None)
            module = importlib.import_module("plugin")

            xbmcgui.getCurrentWindowId = lambda: 10000
            module._add_action("Quick Pick", "quick", widget_fallback="explore")
            self.assertTrue(added[-1][3])
            self.assertIn("action=explore", added[-1][1])

            xbmcgui.getCurrentWindowId = lambda: 10025
            module._add_action("Quick Pick", "quick", widget_fallback="explore")
            self.assertFalse(added[-1][3])
            self.assertIn("command=quick", added[-1][1])

            item = FakeListItem()
            module._set_movie_info(item, {
                "title": "Test",
                "ids": {"trakt": 1, "tmdb": 2},
                "rating": 6.5,
                "votes": 10,
                "ratings": {
                    "tmdb": {"rating": 7.4, "votes": 250, "percent": 74},
                    "imdb": {"rating": 8.0, "votes": 0, "percent": 80},
                    "tomatometerallcritics": {"rating": 7.5, "percent": 75},
                    "tomatometerallaudience": {"rating": 8.2, "percent": 82},
                    "metacritic": {"rating": 6.4, "percent": 64},
                },
            })
            rating_sources = {
                args[2] for name, args in item.tag.calls
                if name == "setRating" and len(args) >= 3
            }
            self.assertTrue({
                "trakt", "tmdb", "themoviedb", "imdb",
                "tomatometerallcritics", "tomatometerallaudience", "metacritic",
            }.issubset(rating_sources))
            self.assertEqual(item.properties["Rating.Tomatoes.Percent"], "75")
            self.assertEqual(item.properties["Rating.Popcorn.Percent"], "82")

    def test_xml_navigation_and_artwork_geometry(self):
        for skin in ("Default", "Light"):
            base = ROOT / "resources" / "skins" / skin / "1080i"
            collection = ET.parse(base / "curatr-collection-manager.xml").getroot()
            folder = ET.parse(base / "curatr-folder-contents.xml").getroot()
            self.assertEqual(collection.find(".//control[@id='100']/onright").text, "200")
            self.assertEqual(collection.find(".//control[@id='200']/onleft").text, "100")
            self.assertEqual(folder.find(".//control[@id='100']/onright").text, "200")
            self.assertEqual(folder.find(".//control[@id='200']/onleft").text, "100")

            editor = ET.parse(base / "curatr-artwork-editor.xml").getroot()
            match = editor.find(".//control[@id='201']")
            self.assertEqual(match.find("label").text, "Match Fanart")
            self.assertGreaterEqual(int(match.find("width").text), 270)
            style = editor.find(".//control[@id='301']")
            self.assertGreaterEqual(int(style.find("width").text), 280)
            preview = editor.find(".//control[@id='21']")
            self.assertEqual(
                tuple(int(preview.find(name).text) for name in ("left", "top", "width", "height")),
                (294, 484, 172, 172),
            )
            for texture in editor.findall(".//texture"):
                if "artwork_card_" in (texture.text or "") or "artwork_mask_" in (texture.text or ""):
                    self.assertIn("border", texture.attrib)

            folder_settings = ET.parse(base / "curatr-folder-settings.xml").getroot()
            for control_id in ("29", "30", "400", "410", "420"):
                visibility = folder_settings.find(
                    ".//control[@id='%s']/visible" % control_id
                )
                self.assertIsNotNone(visibility)
                self.assertIn("CuratrFolderView", visibility.text or "")

    def test_context_scope_and_versioned_create_asset(self):
        addon = ET.parse(ROOT / "addon.xml").getroot()
        self.assertEqual(addon.attrib["version"], "1.0.19")
        visibility = [node.text or "" for node in addon.findall(".//item/visible")]
        self.assertEqual(len(visibility), 3)
        self.assertTrue(all("CuratrItem" in value for value in visibility))
        self.assertFalse((ROOT / "resources/media/menu_v5/menu_create.png").exists())
        self.assertFalse((ROOT / "resources/media/menu_landscape_v1/menu_create.png").exists())
        self.assertTrue((ROOT / "resources/media/menu/v6/square/menu_create_v2.png").exists())
        self.assertTrue((ROOT / "resources/media/menu/v6/landscape/menu_create_v2.png").exists())


class InterfaceChecks(unittest.TestCase):
    @staticmethod
    def curator():
        from lib.core import Curator
        curator = object.__new__(Curator)
        curator.name = "curatr"
        curator.addon = FakeAddon("")
        curator.state = {"ai_lists": [], "widget_folders": []}
        curator._require_keyword_catalogue = Mock()
        curator._require_ai = Mock()
        curator._notify = Mock()
        curator._save_state = Mock()
        curator._store_managed_record = Mock()
        curator.record_activity = Mock()
        return curator

    def test_request_routes_by_method_and_preserves_manual_filters(self):
        from lib import core
        curator = self.curator()
        draft = {"prompt": "crime", "generation_method": "keyword", "content_type": "movies"}
        original = copy.deepcopy(draft)

        def confirm(_path, _prompt, rules, **kwargs):
            self.assertEqual(kwargs["confirm_label"], "Use Filters")
            self.assertTrue(kwargs["start_editing"])
            rules["year_min"] = 2005
            return "create"

        with patch.object(core, "confirm_keyword_rules", side_effect=confirm) as editor, patch.object(core.xbmcgui, "Dialog") as keyboard:
            edited = curator._edit_list_draft_field("prompt", draft)
            keyboard.return_value.input.assert_not_called()
            for action in ("Preview List", "Create List", "Save Changes"):
                ready = curator._prepare_keyword_list_draft(edited, action)
                self.assertEqual(ready["keyword_rules"]["year_min"], 2005)
            self.assertEqual(editor.call_count, 1)
        self.assertEqual(draft, original)
        with patch.object(core, "confirm_keyword_rules") as editor, patch.object(core.xbmcgui, "Dialog") as keyboard:
            keyboard.return_value.input.return_value = "romance"
            edited["generation_method"] = "ai"
            edited = curator._edit_list_draft_field("prompt", edited)
            editor.assert_not_called()
            self.assertEqual(curator._draft_keyword_rules(edited)["genre_labels"], ["Romance"])

    def test_keyword_cancel_does_not_mutate_nested_rules(self):
        from lib import core
        curator = self.curator()
        draft = {"prompt": "similar to Arrival", "generation_method": "keyword"}
        draft["keyword_rules"] = core.parse_prompt(draft["prompt"])
        before = copy.deepcopy(draft)
        def cancel(_path, _prompt, rules, **_kwargs):
            rules["reference_movies"][0]["title"] = "Changed"
            return None
        with patch.object(core, "confirm_keyword_rules", side_effect=cancel):
            result = curator._edit_list_draft_field("prompt", draft)
        self.assertEqual(result, before)
        self.assertEqual(draft, before)
        curator._save_state.assert_not_called()

    def test_preview_and_create_share_keyword_confirmation(self):
        from lib import core
        for action in ("preview", "create"):
            curator = self.curator()
            curator._generate_keyword_and_write = Mock(return_value={"name": "Test", "movies": [{"title": "Matched"}]})
            def edit(_path, draft, *_args):
                return action, draft
            def confirm(_path, _prompt, rules, **kwargs):
                self.assertEqual(kwargs["confirm_label"], "Preview List" if action == "preview" else "Create List")
                rules["year_min"] = 2005
                return "create"
            with patch.object(core, "edit_list_settings", side_effect=edit), patch.object(core, "confirm_keyword_rules", side_effect=confirm) as editor:
                result = curator.create_list_interactive(initial={"name": "Test", "prompt": "crime", "generation_method": "keyword"})
            call = curator._generate_keyword_and_write.call_args
            self.assertEqual(call.args[3]["year_min"], 2005)
            self.assertEqual(call.kwargs["persist"], action == "create")
            curator._require_ai.assert_not_called()
            editor.assert_called_once()
            if action == "preview":
                self.assertEqual(result["kind"], "list_preview")
                self.assertEqual(result["draft"]["keyword_rules"]["year_min"], 2005)
                curator._save_state.assert_not_called()
            else:
                curator._save_state.assert_called_once()

    def test_keyword_modal_only_commits_confirmed_edits(self):
        from lib import keyword_confirm
        from lib.keyword_matcher import parse_prompt
        for result in (None, "create"):
            rules = parse_prompt("similar to Arrival")
            window = Mock(result=result, rules=copy.deepcopy(rules))
            window.rules["reference_movies"][0]["title"] = "Changed"
            with patch.object(keyword_confirm, "KeywordConfirmWindow", return_value=window):
                decision = keyword_confirm.confirm_keyword_rules(str(ROOT), "similar to Arrival", rules)
            self.assertEqual(decision, result)
            self.assertEqual(rules["reference_movies"][0]["title"], "Changed" if result == "create" else "Arrival")
            window.close.assert_called_once()

    def test_cancel_confirmation_returns_to_settings_without_generating(self):
        from lib import core
        curator = self.curator()
        curator._generate_keyword_and_write = Mock()
        actions = iter(("create", "cancel"))
        with patch.object(core, "edit_list_settings", side_effect=lambda _p, draft, *_args: (next(actions), draft)), patch.object(core, "confirm_keyword_rules", return_value=None):
            self.assertIsNone(curator.create_list_interactive(initial={"name": "Test", "prompt": "crime", "generation_method": "keyword"}))
        curator._generate_keyword_and_write.assert_not_called()
        curator._save_state.assert_not_called()

    def test_blank_keyword_request_can_start_from_chips(self):
        from lib import core
        curator = self.curator()
        def add_filter(_path, prompt, rules, **_kwargs):
            self.assertEqual(prompt, "")
            rules.update(core.parse_prompt("comedy"))
            return "create"
        with patch.object(core, "confirm_keyword_rules", side_effect=add_filter):
            draft = curator._edit_keyword_list_draft({"prompt": "", "content_type": "movies"})
        self.assertEqual(draft["prompt"], "Comedy")
        self.assertTrue(draft["_keyword_confirmed"])

    def test_tv_restriction_is_checked_after_chip_edits(self):
        from lib import core
        curator = self.curator()
        draft = {"prompt": "similar to Arrival", "content_type": "shows"}
        with patch.object(core, "confirm_keyword_rules", side_effect=("create", None)) as editor, patch.object(core.xbmcgui, "Dialog") as dialog:
            self.assertIsNone(curator._prepare_keyword_list_draft(draft, "Preview List"))
            self.assertEqual(editor.call_count, 2)
            dialog.return_value.ok.assert_called_once()

    def test_saved_keyword_filters_survive_unrelated_settings_edit(self):
        from lib import core
        curator = self.curator()
        rules = core.parse_prompt("crime")
        rules["year_min"] = 2005
        record = {"local_id": "keep", "name": "Test", "prompt": "crime", "generation_method": "keyword", "content_type": "movies", "count": 20, "keyword_rules": rules, "movies": [{"title": "Keep"}]}
        curator.state["ai_lists"] = [record]
        before = copy.deepcopy(record)
        with patch.object(core, "edit_list_settings", side_effect=lambda _p, draft, *_args, **_kw: ("save", dict(draft, description="Edited"))), patch.object(core, "confirm_keyword_rules") as editor, patch.object(core.xbmcgui, "Dialog") as dialog:
            updated = curator.list_settings_interactive("keep")
        self.assertEqual(updated["keyword_rules"]["year_min"], 2005)
        self.assertEqual(updated["movies"], record["movies"])
        self.assertEqual(record, before)
        self.assertFalse(any(key.startswith("_keyword") for key in updated))
        editor.assert_not_called()
        dialog.return_value.yesno.assert_not_called()

    def test_changed_chips_trigger_refresh_prompt_without_changing_request(self):
        from lib import core
        curator = self.curator()
        record = {"local_id": "keep", "name": "Test", "prompt": "crime", "generation_method": "keyword", "content_type": "movies", "count": 20, "keyword_rules": core.parse_prompt("crime")}
        curator.state["ai_lists"] = [record]
        def edit(_p, draft, *_args, **_kwargs):
            draft["keyword_rules"]["year_min"] = 2005
            return "save", draft
        with patch.object(core, "edit_list_settings", side_effect=edit), patch.object(core.xbmcgui, "Dialog") as dialog:
            dialog.return_value.yesno.return_value = False
            updated = curator.list_settings_interactive("keep")
            dialog.return_value.yesno.assert_called_once()
        self.assertEqual(updated["keyword_rules"]["year_min"], 2005)
        self.assertEqual(record["keyword_rules"]["year_min"], 0)

    def test_active_tabs_remain_coloured_in_light_and_dark_modes(self):
        from lib import ui_theme
        from lib.list_settings import ListSettingsWindow
        for light in (False, True):
            addon = FakeAddon("", {"interface_light_mode": str(light).lower(), "interface_theme": "amber"})
            with patch("xbmcaddon.Addon", return_value=addon):
                window = object.__new__(ListSettingsWindow)
                ListSettingsWindow.__init__(window, str(ROOT), {}, lambda _f, d: d, lambda f, _d: f)
                controls = {key: FakeControl() for key in (100, 101, 102, 200, 201, 202, 203, 300, 301, 302)}
                window.getControl = controls.__getitem__
                window.setFocus = lambda control: setattr(window, "focused", control)
                window._show_tab("content")
                self.assertIs(window.focused, controls[200])
                self.assertEqual(window.properties["CuratrTab101"], ui_theme.theme_palette(addon)["CuratrPrimary"])
                self.assertEqual(controls[101].text_attributes["textColor"], "0xFFFFFFFF")
                window._show_tab("behaviour")
                self.assertEqual(window.properties["CuratrTab101"], ui_theme.theme_palette(addon)["CuratrButtonFaint"])
                self.assertEqual(window.properties["CuratrTab102"], ui_theme.theme_palette(addon)["CuratrPrimary"])
        for skin in ("Default", "Light"):
            for filename, ids in (("list-settings", (100, 101, 102)), ("folder-settings", (100, 101)), ("artwork-editor", (100, 101, 200, 201, 202, 203, 204, 300, 301))):
                root = ET.parse(ROOT / "resources/skins" / skin / "1080i" / ("curatr-" + filename + ".xml"))
                for control_id in ids:
                    texture = root.find(".//control[@id='%s']/texturenofocus" % control_id)
                    self.assertEqual(texture.get("colordiffuse"), "$INFO[Window.Property(CuratrTab%d)]" % control_id)

    def test_keyword_chip_focus_and_controller_navigation_use_current_theme(self):
        from lib.keyword_confirm import KeywordConfirmWindow
        from lib.keyword_matcher import parse_prompt
        for light in (False, True):
            with patch("xbmcaddon.Addon", return_value=FakeAddon("", {"interface_light_mode": str(light).lower(), "interface_theme": "amber"})):
                window = object.__new__(KeywordConfirmWindow)
                KeywordConfirmWindow.__init__(window, str(ROOT), "crime after 2000", parse_prompt("crime after 2000"), "", True, "Use Filters", True)
                controls = {key: FakeControl() for key in (11, 12, 13, 14, 100, 101, 102)}
                window.getControl = controls.__getitem__
                window.addControl = lambda _control: None
                window.addControls = lambda _controls: None
                window.removeControls = lambda _controls: None
                window.setFocus = lambda _control: None
                window.close = lambda: None
                window.onInit()
                self.assertNotEqual(window.result, "fallback")
                self.assertEqual(controls[101].label, "USE FILTERS")
                self.assertGreaterEqual(len(window.action_controls), 5)
                buttons = list(window.action_controls.values())
                for index, button in enumerate(buttons):
                    self.assertIs(button.navigation[2], buttons[index - 1])
                    self.assertIs(button.navigation[3], buttons[(index + 1) % len(buttons)])
                    focus = [image for image in window.dynamic_controls if getattr(image, "condition", "") == "Control.HasFocus(%d)" % button.getId()]
                    self.assertEqual(len(focus), 3)
                    self.assertTrue(all(image.kwargs["colorDiffuse"] == "0x" + window.palette["CuratrPrimary"] for image in focus))
                window.onClick(buttons[0].getId())  # Remove a filter by the same click route used by touch/Select.
                self.assertEqual(len(window.rules["display_parts"]), 1)

    def test_menu_assets_are_complete_padded_and_resolve_legacy_paths(self):
        from lib.menu_art import current_menu_source, menu_source
        plugin_tree = ast.parse((ROOT / "plugin.py").read_text())
        names = {node.value for node in ast.walk(plugin_tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("menu_") and node.value.endswith(".png")}
        for name in names:
            for landscape, size in ((False, (512, 512)), (True, (960, 540))):
                path = Path(menu_source(str(ROOT), name, landscape))
                with Image.open(path) as image:
                    self.assertEqual(image.size, size)
                    bbox = image.getchannel("A").getbbox()
                    self.assertGreaterEqual(bbox[0], 80)
                    self.assertGreaterEqual(bbox[1], 80)
                    self.assertLessEqual(bbox[2], size[0] - 80)
                    self.assertLessEqual(bbox[3], size[1] - 80)
                    self.assertEqual(image.convert("RGB").getextrema(), ((255, 255),) * 3)
                old = "special://home/addons/plugin.video.curatr/resources/media/%s/%s" % ("menu_landscape_v1" if landscape else "menu_v5", name)
                self.assertEqual(current_menu_source(str(ROOT), old), str(path))
        remote = "https://example.com/plugin.video.curatr/resources/media/menu_v5/menu_list.png"
        self.assertEqual(current_menu_source(str(ROOT), remote), remote)

    def test_release_payload_excludes_superseded_artwork(self):
        from tools.build_release import release_files
        files = [path.relative_to(ROOT).as_posix() for path in release_files()]
        for folder in ("menu_v5", "menu_landscape_v1", "keyword_controls_v5"):
            self.assertFalse(any(path.startswith("resources/media/" + folder + "/") for path in files))
        self.assertIn("resources/media/menu/v6/square/menu_list.png", files)
        self.assertIn("resources/media/menu/v6/landscape/menu_list.png", files)


class ArtworkChecks(unittest.TestCase):
    def test_source_and_xml_parse(self):
        for path in ROOT.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in ROOT.rglob("*.xml"):
            ET.parse(path)

    def test_saved_choices_and_legacy_paths(self):
        from lib.list_art import CHOICES, bundled_source, resolved_sources
        addon = FakeAddon("")
        for key, _label in CHOICES:
            for kind, style in (("icon", "white"), ("icon", "genre_colours"), ("fanart", "colour"), ("fanart", "monochrome")):
                expected = bundled_source(addon, key, kind, style)
                self.assertTrue(Path(expected).is_file())
                self.assertIn("/list_art/v5/", expected)
                record = {"local_id": "keep", "movies": [{"ids": {"tmdb": 12}}], "artwork": {kind + "_mode": "bundled", kind + "_key": key, kind + "_style": style}}
                before = copy.deepcopy(record)
                self.assertEqual(resolved_sources(addon, record)[kind], expected)
                self.assertEqual(record, before)
        for folder, kind, style in (("icons_v3", "icon", "white"), ("icons_colour_v4", "icon", "genre_colours"), ("fanart_v2", "fanart", "colour"), ("fanart_mono_v2", "fanart", "monochrome")):
            self.assertFalse((ROOT / "resources/media/list_art" / folder).exists())
            extension = ".png" if kind == "icon" else ".jpg"
            source = "special://home/addons/plugin.video.curatr/resources/media/list_art/" + folder + "/crime" + extension
            record = {"artwork": {kind + "_mode": "custom", kind + "_source": source}}
            self.assertEqual(resolved_sources(addon, record)[kind], bundled_source(addon, "crime", kind, style))
            self.assertEqual(record["artwork"][kind + "_source"], source)
        for source in ("/storage/Pictures/custom.png", "https://example.org/plugin.video.curatr/resources/media/list_art/icons_v3/crime.png"):
            self.assertEqual(resolved_sources(addon, {"artwork": {"icon_mode": "custom", "icon_source": source}})["icon"], source)

    def test_asset_geometry_transparency_and_grey(self):
        from lib.list_art import CHOICES
        base = ROOT / "resources/media/list_art/v5"
        self.assertEqual(len([p for p in base.rglob("*") if p.is_file()]), 68)
        for key, _label in CHOICES:
            with Image.open(base / "white" / (key + ".png")) as icon:
                self.assertEqual(icon.size, (512, 512))
                self.assertEqual(icon.mode, "RGBA")
                bounds = icon.getchannel("A").getbbox()
                self.assertGreaterEqual(min(bounds[0], bounds[1], 512 - bounds[2], 512 - bounds[3]), 84)
                self.assertLessEqual(abs(bounds[0] + bounds[2] - 512), 2)
                self.assertLessEqual(abs(bounds[1] + bounds[3] - 512), 2)
                self.assertTrue(all(low == high == 255 for low, high in icon.convert("RGB").getextrema()))
                self.assertEqual(icon.getpixel((0, 0))[3], 0)
            with Image.open(base / "colour" / (key + ".png")) as colour:
                self.assertEqual(colour.size, (512, 512))
                self.assertEqual(colour.mode, "RGB")
                self.assertNotEqual(colour.getpixel((0, 0)), colour.getpixel((511, 511)))
            for style in ("fanart", "monochrome"):
                with Image.open(base / style / (key + ".jpg")) as image:
                    image.load()
                    self.assertEqual(image.size, (1920, 1080))
                    if style == "monochrome":
                        r, g, b = image.split()
                        self.assertIsNone(ImageChops.difference(r, g).getbbox())
                        self.assertIsNone(ImageChops.difference(r, b).getbbox())
                        self.assertGreaterEqual(r.getextrema()[0], 50)
                        self.assertLess(r.crop((0, 0, 1000, 1080)).getextrema()[1], 140)

    def test_contents_is_lazy_and_cached_per_window(self):
        from lib.core import Curator
        curator = object.__new__(Curator)
        curator.addon = FakeAddon("")
        calls = []
        curator._fanart_entries_from_movies = lambda rows: calls.append(rows) or [{"source": "https://art/one.jpg"}]
        curator._provider_artwork_entries = lambda row: calls.append(row) or [{"source": "https://art/two.jpg"}]
        for record in ({"movies": []}, {"movies": [{"title": "Saved"}]}, {"type": "provider_list", "provider": "trakt"}, {"type": "provider_list", "provider": "mdblist"}):
            calls.clear()
            def editor(*args, **kwargs):
                self.assertTrue(kwargs["has_contents"])
                self.assertFalse(calls)
                chooser = args[5]
                self.assertEqual(chooser("fanart", "contents", "colour"), chooser("fanart", "contents", "colour"))
                self.assertEqual(len(calls), 1)
                return "cancel", args[2]
            with patch("lib.core.edit_artwork", side_effect=editor):
                curator._edit_compact_artwork("Artwork", {}, preview_record=record)

    def test_edit_list_passes_an_isolated_content_copy(self):
        from lib.core import Curator
        curator = object.__new__(Curator)
        curator.addon = FakeAddon("")
        record = {"name": "Saved", "movies": [{"title": "Keep", "ids": {"tmdb": 12}}]}
        before = copy.deepcopy(record)
        curator._managed_record_by_id = lambda _key: record
        curator._default_regeneration_interval = lambda: 24
        curator._default_trakt_refresh_interval = lambda: 24
        def editor(_path, draft, *_args, **_kwargs):
            self.assertEqual(draft["movies"], record["movies"])
            self.assertIsNot(draft["movies"], record["movies"])
            draft["movies"][0]["title"] = "Preview"
            return "cancel", draft
        with patch("lib.core.edit_list_settings", side_effect=editor):
            curator.list_settings_interactive("keep")
        self.assertEqual(record, before)

    def test_unsaved_linked_list_artwork_preserves_folder_state(self):
        from lib.core import Curator
        curator = object.__new__(Curator)
        curator.state = {"widget_folders": [{"id": "keep", "entries": []}], "ai_lists": [{"local_id": "keep"}]}
        before = copy.deepcopy(curator.state)
        movies, calls = [{"title": "Linked"}], []
        curator._fanart_entries_from_movies = lambda rows: rows
        curator._fetch_provider_list_movies = lambda provider, key: calls.append((provider, key)) or movies
        for provider in ("trakt", "mdblist"):
            self.assertEqual(curator._provider_artwork_entries({"provider": provider, "provider_list_id": "42"}), movies)
        self.assertEqual(calls, [("trakt", "42"), ("mdblist", "42")])
        self.assertEqual(curator.state, before)
        curator.state["linked_list_cache"] = {"trakt:42": {"movies": movies}}
        self.assertEqual(curator._provider_artwork_entries({"provider": "trakt", "provider_list_id": "42"}), movies)
        self.assertEqual(len(calls), 2)

    def test_old_metadata_cache_fetches_missing_backdrops_once(self):
        from lib.metadata_cache import MetadataCache
        class TMDB:
            api_key, calls = "key", 0
            @staticmethod
            def image_url(path, _size):
                return "https://art" + path if path else ""
            def list_item_details(self, tmdb_id, media_type):
                self.calls += 1
                return {"id": tmdb_id, "backdrop_path": "/backdrop.jpg", "poster_path": "/poster.jpg"}
        with tempfile.TemporaryDirectory() as profile:
            cache = MetadataCache(FakeAddon(profile))
            cache._data = {"version": 1, "items": {"movie:12": {"cached_at": int(time.time()), "metadata": {"cast": []}}}}
            tmdb = TMDB()
            rows = [{"ids": {"tmdb": 12}, "images": {"poster": {"full": "https://custom/poster.jpg"}}}]
            cache.enrich(rows, tmdb)
            self.assertEqual(tmdb.calls, 0)
            cache.enrich(rows, tmdb, include_artwork=True)
            self.assertEqual(tmdb.calls, 1)
            self.assertEqual(rows[0]["images"]["fanart"]["full"], "https://art/backdrop.jpg")
            self.assertEqual(rows[0]["images"]["poster"]["full"], "https://custom/poster.jpg")
            cache.enrich(rows, tmdb, include_artwork=True)
            self.assertEqual(tmdb.calls, 1)

    def test_content_requests_are_bounded_and_deduplicated(self):
        from lib.core import Curator
        curator = object.__new__(Curator)
        curator.addon = FakeAddon("")
        movies = [{"title": str(i), "ids": {"tmdb": i}} for i in range(100)]
        before = copy.deepcopy(movies)
        def enrich(rows, *_args, **kwargs):
            self.assertEqual(len(rows), 24)
            self.assertTrue(kwargs["include_artwork"])
            for i, row in enumerate(rows):
                row["images"] = {"fanart": {"full": "https://art/%s.jpg" % (i // 2)}}
        with patch("lib.core.MetadataCache") as metadata, patch("lib.core.ArtworkCache.cache_urls", return_value={}):
            metadata.return_value.enrich.side_effect = enrich
            self.assertEqual(len(curator._fanart_entries_from_movies(movies)), 12)
        self.assertEqual(movies, before)

    def test_grid_edge_navigation_and_return(self):
        from lib.artwork_editor import ArtworkEditorWindow
        for tab in ("icon", "fanart"):
            window = object.__new__(ArtworkEditorWindow)
            ArtworkEditorWindow.__init__(window, str(ROOT), "Artwork", {}, {}, lambda _draft: {}, lambda *_args: [], lambda _kind: None, has_contents=True)
            window.tab = tab
            window.sources = window._available_sources()
            window.grid_source, window.grid_entries = "curatr", [{"source": "one"}]
            controls = {key: FakeControl() for key in (100, 101, 200, 201, 202, 203, 204, 300, 301, 400, 401, 500, 501, 502)}
            window.getControl = controls.__getitem__
            window._wire_navigation()
            grid = controls[window.GRID_IDS[tab]]
            self.assertIs(grid.navigation[2], controls[500])
            self.assertIs(grid.navigation[3], controls[500])
            for key in (500, 501, 502):
                self.assertIs(controls[key].navigation[0], grid)
            window.onFocus(window.GRID_IDS[tab])
            self.assertTrue(window.in_grid)
            window.onFocus(500)
            self.assertFalse(window.in_grid)
        for skin in ("Default", "Light"):
            editor = ET.parse(ROOT / "resources/skins" / skin / "1080i/curatr-artwork-editor.xml").getroot()
            for key in ("400", "401"):
                grid = editor.find(".//control[@id='%s']" % key)
                self.assertEqual(grid.findtext("onleft"), "500")
                self.assertEqual(grid.findtext("onright"), "500")

    def test_editor_contents_selection_save_and_cancel(self):
        from lib.artwork_editor import ArtworkEditorWindow
        original = {"icon_mode": "auto", "fanart_mode": "auto"}
        for result in ("save", "cancel"):
            window = object.__new__(ArtworkEditorWindow)
            choices = lambda *_args: [{"key": "crime", "label": "Selected title", "source": "https://art/choice.jpg", "mode": "item"}]
            ArtworkEditorWindow.__init__(window, str(ROOT), "Artwork", original, {}, lambda _draft: {}, choices, lambda _kind: None, has_contents=True)
            controls = {key: FakeControl() for key in (10, 20, 21, 22, 23, 100, 101, 200, 201, 202, 203, 204, 300, 301, 400, 401, 500, 501, 502)}
            window.getControl = controls.__getitem__
            window.setFocus = lambda control: window.onFocus(next(key for key, value in controls.items() if value is control))
            closed = []
            window.close = lambda: closed.append(True)
            window.onInit()
            self.assertFalse(window.failed)
            window.onClick(101)
            self.assertEqual(controls[202].label, "Contents")
            self.assertTrue(controls[202].visible)
            window.onClick(202)
            self.assertTrue(controls[401].visible)
            self.assertFalse(controls[300].visible)
            window.onClick(401)
            self.assertEqual(window.draft["fanart_source"], "https://art/choice.jpg")
            self.assertEqual(window.draft["fanart_mode"], "item")
            window.onClick(500 if result == "save" else 502)
            self.assertEqual(window.result, result)
            self.assertTrue(closed)
            self.assertEqual(original, {"icon_mode": "auto", "fanart_mode": "auto"})

    def test_provider_fetch_preserves_media_types(self):
        from lib.core import Curator
        curator = object.__new__(Curator)
        curator._has_oauth = lambda: True
        rows = [{"movie": {"title": "Movie"}}, {"show": {"title": "Show"}}, None, {"movie": {}}]
        curator.trakt = types.SimpleNamespace(list_items=lambda *_args, **_kwargs: rows)
        items = curator._fetch_provider_list_movies("trakt", "42")
        self.assertEqual([(row["title"], row["media_type"]) for row in items], [("Movie", "movie"), ("Show", "show")])
        self.assertNotIn("media_type", rows[0]["movie"])

    def test_empty_contents_preserves_state(self):
        import lib.core as core
        curator = object.__new__(core.Curator)
        curator.name = "curatr"
        curator.state = {"ai_lists": [{"local_id": "keep"}], "widget_folders": [{"id": "keep"}]}
        before = copy.deepcopy(curator.state)
        with patch.object(core.xbmcgui, "Dialog") as dialog:
            self.assertEqual(curator._fanart_entries_from_movies([]), [])
            dialog.return_value.ok.assert_called_once()
        self.assertEqual(curator.state, before)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as profile:
        install_kodi_stubs(profile)
        unittest.main(verbosity=2)

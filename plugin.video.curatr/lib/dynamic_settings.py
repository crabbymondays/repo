"""Dynamic List configuration using Curatr's existing settings and management windows."""

from copy import deepcopy
import uuid

import xbmcgui
import xbmcvfs

from . import dynamic_lists as dynamic
from .dynamic_display import prepare_items
from .collection_manager import CollectionManagerWindow, manage_collection
from .list_settings import ListSettingsWindow
from .list_art import resolved_sources, summary
from .ui_theme import bold, show_tab


class DynamicSettingsWindow(ListSettingsWindow):
    TAB_IDS = {100: "appearance", 101: "content"}
    FIELDS = {"appearance": ("name", "description", "artwork"),
              "content": ("sources", "sort", "direction", "alternate_sources", "count")}

    def onInit(self):
        super().onInit()
        self.getControl(10).setLabel(bold("Dynamic List Settings"))
        show_tab(self, 102, False)
        self.getControl(20).setLabel(dynamic.REFRESH_SUMMARY)
        self.getControl(301).setLabel(bold("Save Changes" if self.draft.get("saved") else "Create List"))
        for cid, other in ((100, 101), (101, 100)):
            self.getControl(cid).setNavigation(self.getControl(other), self.getControl(other),
                                               self.getControl(cid), self.getControl(200))

    def _show_tab(self, tab):
        super()._show_tab(tab)
        if tab == "content":
            self.getControl(202).setEnabled(self.draft["sort"] != "source")
            self.getControl(203).setEnabled(self.draft["sort"] != "title")


def choose_list(curator, existing=()):
    used = {e.get("list_id") for e in existing if e.get("type") == "dynamic_list"}
    rows = [r for r in dynamic.records(curator) if r["id"] not in used]
    if not rows:
        xbmcgui.Dialog().ok("Dynamic Lists", "Create a Dynamic List first, or choose a different folder.")
        return None
    pos = xbmcgui.Dialog().select("Add a Dynamic List", [r["name"] for r in rows])
    return {"id": uuid.uuid4().hex, "type": "dynamic_list", "list_id": rows[pos]["id"]} if pos >= 0 else None


def _choose_source(curator, sources):
    if len(sources) >= dynamic.MAX_SOURCES:
        xbmcgui.Dialog().ok("Sources", "A Dynamic List can combine up to %d sources." % dynamic.MAX_SOURCES)
        return None
    pos = xbmcgui.Dialog().select("Add Source", ["Curatr List", "Add-on Path", "Trakt List", "MDBList List"])
    row = None
    if pos == 0:
        rows = [r for r in curator.state.get("ai_lists", []) if isinstance(r, dict)]
        if not rows:
            xbmcgui.Dialog().ok("Sources", "No saved Curatr lists are available.")
            return None
        selected = xbmcgui.Dialog().select("Curatr List", [r.get("name") or "List" for r in rows])
        if selected >= 0:
            selected = rows[selected]
            row = {"id": uuid.uuid4().hex, "type": "curatr_list", "list_id": curator._record_key(selected),
                   "name": selected.get("name") or "Curatr List"}
    elif pos == 1:
        method = xbmcgui.Dialog().select("Add-on Path", ["Browse add-ons", "Enter a plugin path"])
        if method == 0:
            selected = curator._browse_external_plugin_path()
            if selected:
                path, name, _thumb = selected
                row = {"type": "external_path", "path": path, "name": name}
        elif method == 1:
            path = xbmcgui.Dialog().input("Plugin directory path")
            if path:
                name = xbmcgui.Dialog().input("Source name", defaultt="Add-on Path")
                if name:
                    row = {"type": "external_path", "path": path.strip(), "name": name}
    elif pos in (2, 3):
        row = curator._choose_provider_folder_entry({"entries": sources}, "trakt" if pos == 2 else "mdblist")
    if row:
        if not dynamic.valid_source(row):
            xbmcgui.Dialog().ok("Sources", "Choose a directory from another add-on, or use Curatr List for a local list.")
            return None
        if dynamic.source_key(row) in {dynamic.source_key(s) for s in sources}:
            xbmcgui.Dialog().ok("Sources", "This source is already included.")
            return None
        row["id"] = row.get("id") or uuid.uuid4().hex
    return row


def _edit_sources(curator, draft):
    sources = deepcopy(draft["sources"])
    def entries():
        return [{"key": s["id"], "label": s.get("name") or "Source", "detail":
                 {"curatr_list": "Curatr List", "external_path": "Add-on Path", "provider_list": "Linked List"}[s["type"]],
                 "art": resolved_sources(curator.addon, s), "index": i, "total": len(sources)} for i, s in enumerate(sources)]
    def actions(row):
        result = curator._draft_folder_content_actions(dict(row, kind="source"))
        for action in result:
            if action["key"] == "remove":
                action["label"] = "Remove Source"
        return result
    def action(row, selected):
        index = next((i for i, s in enumerate(sources) if s["id"] == row), -1)
        if index < 0:
            return
        if selected == "remove":
            sources.pop(index)
        elif selected.startswith("move_"):
            target = {"move_up": max(0, index-1), "move_down": min(len(sources)-1, index+1),
                      "move_front": 0, "move_back": len(sources)-1}[selected]
            sources.insert(target, sources.pop(index))
    def add():
        row = _choose_source(curator, sources)
        if row:
            sources.append(row)
        return row
    result = manage_collection(xbmcvfs.translatePath(curator.addon.getAddonInfo("path")), "Sources", "", "Add Source",
                              entries, actions, action, add)
    if result == "fallback":
        raise RuntimeError("The sources window could not be opened.")
    draft["sources"] = sources
    return draft


class PreviewWindow(CollectionManagerWindow):
    def onInit(self):
        super().onInit()
        self.getControl(300).setLabel(bold("Close"))
        self.getControl(300).setPosition(740, 965)
        self.getControl(301).setVisible(False)
        self.getControl(301).setEnabled(False)
        self.getControl(300).setNavigation(self.getControl(100), self.getControl(100),
                                            self.getControl(300), self.getControl(300))
        self.getControl(200).setNavigation(self.getControl(200), self.getControl(300),
                                            self.getControl(100), self.getControl(300))

    def onClick(self, control_id):
        if control_id == 300:
            self.close()
        else:
            super().onClick(control_id)


def _preview(curator, draft):
    result = dynamic.load(curator, draft)
    rows = []
    for i, item in enumerate(prepare_items(curator, result["items"])):
        data = item["data"]
        art = item["art"]
        details = [str(data.get("year") or ""), str(data.get("showtitle") or "")]
        rows.append({"key": str(i), "label": data.get("title") or data.get("label") or "Item",
                     "detail": " · ".join(d for d in details if d), "art": art,
                     "summary": data.get("plot") or data.get("overview") or ""})
    window = PreviewWindow(xbmcvfs.translatePath(curator.addon.getAddonInfo("path")), "Preview: " + draft["name"],
                           " · ".join(result["warnings"]) or "%d items" % len(rows), "Close", lambda: rows,
                           lambda row: [{"key": "info", "label": "Information"}],
                           lambda key, action: xbmcgui.Dialog().textviewer(rows[int(key)]["label"], rows[int(key)]["summary"] or rows[int(key)]["detail"]), lambda: None)
    try:
        window.doModal()
    finally:
        window.close()


def edit(curator, list_id=""):
    original = dynamic.by_id(curator, list_id) if list_id else None
    if list_id and not original:
        raise RuntimeError("This Dynamic List has been removed.")
    draft = deepcopy(dynamic.normalise(original or {"name": "Dynamic List"}))
    draft["saved"] = bool(original)
    def formatter(field, value):
        if field == "artwork":
            icon, fanart, _style = summary(value)
            return "Artwork · %s / %s" % (icon, fanart)
        if field == "sources":
            return "Sources · %d" % len(value["sources"])
        if field == "sort":
            return "Sort · " + dynamic.SORTS[value["sort"]]
        if field == "direction":
            return "Order · " + ("Source order" if value["sort"] == "source" else
                                  ("Descending" if value["descending"] else "Ascending"))
        if field == "alternate_sources":
            return "Alternate sources · " + ("On" if value["alternate_sources"] else "Off")
        return "%s · %s" % ({"count": "Number of items"}.get(field, field.title()), value.get(field) or "None")
    def editor(field, value):
        if field == "sources":
            return _edit_sources(curator, value)
        if field in ("name", "description"):
            text = xbmcgui.Dialog().input(field.title(), defaultt=value.get(field) or "")
            if text or field == "description":
                value[field] = text.strip()
        elif field == "artwork":
            value[field] = curator._edit_compact_artwork("Dynamic List Artwork", value.get("artwork"), preview_record=value)
        elif field == "sort":
            keys = list(dynamic.SORTS)
            pos = xbmcgui.Dialog().select("Sort", list(dynamic.SORTS.values()), preselect=keys.index(value["sort"]))
            if pos >= 0:
                value["sort"] = keys[pos]
                value["descending"] = keys[pos] not in ("title", "source")
        elif field == "direction" and value["sort"] != "source":
            value["descending"] = not value["descending"]
        elif field == "alternate_sources" and value["sort"] != "title":
            value["alternate_sources"] = not value["alternate_sources"]
        elif field == "count":
            text = xbmcgui.Dialog().input("Number of items (1–1000)", defaultt=str(value["count"]), type=xbmcgui.INPUT_NUMERIC)
            if text.isdigit():
                value["count"] = max(1, min(1000, int(text)))
        return value
    while True:
        window = DynamicSettingsWindow(xbmcvfs.translatePath(curator.addon.getAddonInfo("path")), draft, editor, formatter)
        try:
            window.doModal()
            result, draft = window.result, window.draft
        finally:
            window.close()
        if result == "cancel":
            return None
        if result == "preview":
            _preview(curator, draft)
            continue
        return dynamic.store(curator, draft)


def manage(curator):
    def entries():
        return [{"key": r["id"], "label": r["name"], "detail": "%d sources · %s" %
                 (len(r.get("sources", [])), dynamic.SORTS.get(r.get("sort"), "Source order")),
                 "summary": r.get("description") or "", "art": resolved_sources(curator.addon, r)} for r in dynamic.records(curator)]
    def action(row, key):
        if key == "settings":
            return edit(curator, row)
        if key == "delete" and xbmcgui.Dialog().yesno("Dynamic Lists", "Delete %s? Source lists will be kept." % (dynamic.by_id(curator, row) or {}).get("name", "this list")):
            curator.state["dynamic_lists"] = [r for r in dynamic.records(curator) if r["id"] != row]
            curator._save_state()
    result = manage_collection(xbmcvfs.translatePath(curator.addon.getAddonInfo("path")), "Manage Dynamic Lists", "", "Create Dynamic List",
                              entries, lambda row: [{"key": "settings", "label": "List Settings"}, {"key": "delete", "label": "Delete List"}],
                              action, lambda: edit(curator))
    if result == "fallback":
        raise RuntimeError("The Dynamic Lists window could not be opened.")
    return result

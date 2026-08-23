import xbmcgui


_ACTION_SELECT_ITEM = 7
_ACTION_PARENT_DIR = 9
_ACTION_PREVIOUS_MENU = 10
_ACTION_NAV_BACK = 92
_ACCEPT_ACTIONS = {_ACTION_SELECT_ITEM}
_BACK_ACTIONS = {_ACTION_PARENT_DIR, _ACTION_PREVIOUS_MENU, _ACTION_NAV_BACK}


class ArtworkPreviewWindow(xbmcgui.WindowDialog):
    """Kodi-native, remote-friendly preview for local or remote artwork."""

    def __init__(self, image_path, backdrop_path, heading, label):
        super().__init__()
        self.accepted = False
        screen_w = max(960, int(self.getWidth() or 1280))
        screen_h = max(540, int(self.getHeight() or 720))

        margin_x = int(screen_w * 0.07)
        image_y = int(screen_h * 0.15)
        image_h = int(screen_h * 0.67)
        image_w = screen_w - (margin_x * 2)

        self.backdrop = xbmcgui.ControlImage(
            0, 0, screen_w, screen_h, backdrop_path,
            aspectRatio=0, colorDiffuse="0xED101018",
        )
        self.heading = xbmcgui.ControlLabel(
            margin_x, int(screen_h * 0.055), image_w, 45,
            heading or "Artwork preview", font="font30",
            textColor="0xFFFFFFFF", alignment=0x00000002,
        )
        self.image = xbmcgui.ControlImage(
            margin_x, image_y, image_w, image_h, image_path, aspectRatio=2,
        )
        self.label = xbmcgui.ControlLabel(
            margin_x, int(screen_h * 0.84), image_w, 36,
            label or "Artwork", font="font13",
            textColor="0xFFFFFFFF", alignment=0x00000002,
        )
        self.hint = xbmcgui.ControlLabel(
            margin_x, int(screen_h * 0.91), image_w, 32,
            "Press OK to use this artwork  •  Back to choose another",
            font="font13", textColor="0xFFBBBBBB", alignment=0x00000002,
        )
        self.addControls([self.backdrop, self.heading, self.image, self.label, self.hint])

    def onAction(self, action):
        action_id = action.getId()
        if action_id in _ACCEPT_ACTIONS:
            self.accepted = True
            self.close()
        elif action_id in _BACK_ACTIONS:
            self.close()


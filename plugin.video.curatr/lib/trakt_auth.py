import xbmcgui


# Kodi action IDs documented for navigation/back behavior.
_ACTION_PARENT_DIR = 9
_ACTION_PREVIOUS_MENU = 10
_ACTION_NAV_BACK = 92
_BACK_ACTIONS = {_ACTION_PARENT_DIR, _ACTION_PREVIOUS_MENU, _ACTION_NAV_BACK}


class TraktAuthWindow(xbmcgui.WindowDialog):
    """Small Kodi-native QR activation screen for Trakt Device Code auth."""

    def __init__(self, qr_path, backdrop_path, user_code, verification_url, expires_in):
        super().__init__()
        self.cancelled = False
        self._expires_in = max(1, int(expires_in or 600))

        # WindowDialog uses Kodi's GUI coordinate space, which can differ from
        # the physical display resolution on Xbox/4K displays. Sizing controls
        # from getScreenWidth()/getScreenHeight() can therefore make the QR huge
        # and push it outside the visible window. Use this window's dimensions.
        screen_w = max(960, int(self.getWidth() or 1280))
        screen_h = max(540, int(self.getHeight() or 720))

        qr_size = max(240, min(440, int(screen_h * 0.38), int(screen_w * 0.28)))
        qr_x = int(screen_w * 0.10)
        qr_y = int(screen_h * 0.30)
        text_x = int(screen_w * 0.51)
        text_w = int(screen_w * 0.40)

        # A dim backdrop keeps the QR and labels readable across Kodi skins.
        self.backdrop = xbmcgui.ControlImage(
            0, 0, screen_w, screen_h, backdrop_path,
            aspectRatio=0, colorDiffuse="0xE0181818"
        )
        self.title = xbmcgui.ControlLabel(
            int(screen_w * 0.08), int(screen_h * 0.08), int(screen_w * 0.84), 60,
            "Connect Trakt", font="font30", textColor="0xFFFFFFFF", alignment=0x00000002
        )
        self.subtitle = xbmcgui.ControlLabel(
            int(screen_w * 0.08), int(screen_h * 0.15), int(screen_w * 0.84), 45,
            "Scan the QR code with your phone, then enter the code shown on this TV.",
            font="font13", textColor="0xFFDDDDDD", alignment=0x00000002
        )
        self.qr = xbmcgui.ControlImage(qr_x, qr_y, qr_size, qr_size, qr_path, aspectRatio=2)

        label_y = int(screen_h * 0.30)
        self.code_heading = xbmcgui.ControlLabel(
            text_x, label_y, text_w, 45, "Your Trakt code",
            font="font13", textColor="0xFFBBBBBB", alignment=0x00000002
        )
        self.code = xbmcgui.ControlLabel(
            text_x, label_y + 52, text_w, 75, user_code or "",
            font="font30", textColor="0xFFFFFFFF", alignment=0x00000002
        )
        self.url_heading = xbmcgui.ControlLabel(
            text_x, label_y + 145, text_w, 38, "If you cannot scan the QR code, open:",
            font="font13", textColor="0xFFBBBBBB", alignment=0x00000002
        )
        self.url = xbmcgui.ControlLabel(
            text_x, label_y + 183, text_w, 42, verification_url,
            font="font13", textColor="0xFFFFFFFF", alignment=0x00000002
        )
        self.status = xbmcgui.ControlLabel(
            text_x, label_y + 270, text_w, 45, "Waiting for authorization…",
            font="font13", textColor="0xFFFFFFFF", alignment=0x00000002
        )
        self.cancel_hint = xbmcgui.ControlLabel(
            text_x, label_y + 320, text_w, 38, "Press Back to cancel",
            font="font13", textColor="0xFFAAAAAA", alignment=0x00000002
        )

        self.addControls([
            self.backdrop,
            self.title,
            self.subtitle,
            self.qr,
            self.code_heading,
            self.code,
            self.url_heading,
            self.url,
            self.status,
            self.cancel_hint,
        ])

    def onAction(self, action):
        if action.getId() in _BACK_ACTIONS:
            self.cancelled = True
            self.close()

    def update_waiting(self, seconds_left):
        seconds_left = max(0, int(seconds_left))
        minutes, seconds = divmod(seconds_left, 60)
        if minutes:
            remaining = "%d:%02d" % (minutes, seconds)
        else:
            remaining = "%ds" % seconds
        self.status.setLabel("Waiting for authorization…  %s remaining" % remaining)

    def set_success(self):
        self.status.setLabel("Trakt connected successfully")

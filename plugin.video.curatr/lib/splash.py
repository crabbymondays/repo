import threading

import xbmc
import xbmcgui


class CuratrSplashWindow(xbmcgui.WindowXMLDialog):
    def __new__(cls, *args, **kwargs):
        return super(CuratrSplashWindow, cls).__new__(cls, *args)

    def __init__(self, *args, **kwargs):
        self.duration_ms = int(kwargs.pop('duration_ms', 1500))
        super().__init__()

    def onInit(self):
        threading.Thread(target=self._auto_close, daemon=True).start()

    def _auto_close(self):
        xbmc.sleep(self.duration_ms)
        try:
            self.close()
        except Exception:
            pass

    def onAction(self, action):
        # allow any key press to dismiss the splash immediately
        try:
            self.close()
        except Exception:
            pass


def show_splash(addon_path, duration_ms=1500):
    try:
        win = CuratrSplashWindow('curatr-splash.xml', addon_path, 'Default', '1080i', duration_ms=duration_ms)
        win.doModal()
        del win
    except Exception as exc:
        xbmc.log('curatr splash error: %s' % exc, xbmc.LOGWARNING)

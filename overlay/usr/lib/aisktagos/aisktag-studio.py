#!/usr/bin/env python3
"""Mind Studio — окно рабочего места: ваши модели, Claude и Antigravity в одном разговоре.

    aisktag-studio                     открыть окно
    aisktag-studio --link-antigravity  запустить мост к Antigravity (выполнять в терминале Antigravity)
    aisktag-studio --install-antigravity-autostart  Antigravity сам запускает мост при каждом старте
    aisktag-studio --server            только сервер; адрес печатается в консоль (для браузера и отладки)

Окно — Qt WebEngine; если его нет, интерфейс откроется в Chrome/Edge/Chromium в режиме приложения.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/usr/lib/aisktagos")
sys.path.insert(0, str(HERE))


def _open_in_browser(url: str) -> bool:
    """Окно без адресной строки через установленный Chromium-браузер."""
    cands = ["chromium", "chromium-browser", "google-chrome", "microsoft-edge", "msedge", "chrome"]
    if sys.platform == "win32":
        pf = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")]
        cands = [str(Path(p) / sub) for p in pf if p for sub in
                 ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")] + cands
    for c in cands:
        exe = c if Path(c).exists() else shutil.which(c)
        if exe:
            subprocess.Popen([exe, f"--app={url}", "--window-size=1320,860"])
            return True
    import webbrowser
    return webbrowser.open(url)


def run_window(url: str) -> int:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices, QIcon
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, qurl, nav_type, is_main):
            # Внешние ссылки из ответов — в обычный браузер, окно остаётся на Mind Studio
            if qurl.host() not in ("127.0.0.1", "localhost"):
                QDesktopServices.openUrl(qurl)
                return False
            return super().acceptNavigationRequest(qurl, nav_type, is_main)

        def createWindow(self, _type):
            page = Page(self)
            page.urlChanged.connect(lambda u: (QDesktopServices.openUrl(u), page.deleteLater()))
            return page

    app = QApplication(sys.argv)
    app.setApplicationName("Mind Studio")
    app.setDesktopFileName("aisktag-studio")
    view = QWebEngineView()
    view.setPage(Page(view))
    st = view.settings()
    st.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
    st.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    icon = os.environ.get("AISKTAG_MIND_ICON", "")
    view.setWindowIcon(QIcon(icon) if icon and Path(icon).exists() else QIcon.fromTheme("aisktagos-mind"))
    view.setWindowTitle("Mind Studio")
    view.resize(1320, 860)
    view.setMinimumSize(760, 520)
    view.load(QUrl(url))
    view.show()
    return app.exec()


def main() -> int:
    args = sys.argv[1:]
    from mind_studio import antigravity, server
    if "--link-antigravity" in args:
        return antigravity.serve_link(mcp="--mcp" in args)
    if "--install-antigravity-autostart" in args:
        cmd = json.loads(os.environ.get("AISKTAG_STUDIO_SELF") or '["aisktag-studio"]')
        path = antigravity.install_autostart(cmd + ["--link-antigravity", "--mcp"])
        print(f"Готово: мост добавлен в {path}. Перезапустите Antigravity — дальше мост поднимается сам.")
        return 0
    srv = server.start()
    url = server.url(srv)
    if "--server" in args:
        print(url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    try:
        return run_window(url)
    except ImportError:
        if not _open_in_browser(url):
            print(url)
        # Сервер должен жить, пока открыто окно браузера
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())

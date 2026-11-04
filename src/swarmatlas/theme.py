from __future__ import annotations

import sys
from typing import Any


PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#10161D",
        "surface": "#19222C",
        "surface_alt": "#202B36",
        "text": "#E8EEF4",
        "muted": "#9AA8B6",
        "line": "#2B3946",
        "accent": "#42C9B5",
        "accent_light": "#1D3D3B",
        "grid": "#2A3742",
        "land": "#2A3944",
        "download": "#91A4C0",
        "selection": "#294B4A",
        "scroll": "#354453",
        "mica_window": "rgba(16, 22, 29, 0.68)",
        "mica_surface": "rgba(25, 34, 44, 0.90)",
        "mica_sidebar": "rgba(25, 34, 44, 0.72)",
    },
    "light": {
        "bg": "#F5F7F8",
        "surface": "#FFFFFF",
        "surface_alt": "#F0F4F5",
        "text": "#17212B",
        "muted": "#74818D",
        "line": "#E2E8EB",
        "accent": "#159D93",
        "accent_light": "#E6F5F3",
        "grid": "#EEF1F2",
        "land": "#E9EFF0",
        "download": "#8E9AAF",
        "selection": "#D9EFEC",
        "scroll": "#C7D1D8",
        "mica_window": "rgba(245, 247, 248, 0.70)",
        "mica_surface": "rgba(255, 255, 255, 0.88)",
        "mica_sidebar": "rgba(255, 255, 255, 0.74)",
    },
}


def get_palette(theme: str) -> dict[str, str]:
    return dict(PALETTES.get(theme, PALETTES["dark"]))


def system_theme() -> str:
    """Return the OS app theme, with Qt and Windows registry fallbacks."""
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import Qt

        app = QGuiApplication.instance()
        if app is not None:
            scheme = QGuiApplication.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
            if scheme == Qt.ColorScheme.Light:
                return "light"
    except (ImportError, AttributeError, RuntimeError):
        pass

    if sys.platform == "win32":
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(value) else "dark"
        except (OSError, ImportError, ValueError, TypeError):
            pass
    return "light"


def system_language() -> str:
    try:
        from PySide6.QtCore import QLocale

        name = QLocale.system().name().lower()
    except (ImportError, AttributeError):
        import locale

        name = (locale.getlocale()[0] or "").lower()
    return "ru" if name.startswith("ru") else "en"


def resolve_theme(preference: str) -> str:
    return system_theme() if preference == "system" else (
        preference if preference in {"dark", "light"} else "dark"
    )


def resolve_language(preference: str) -> str:
    return system_language() if preference == "system" else (
        preference if preference in {"ru", "en"} else "en"
    )


def apply_windows_mica(window: Any, dark: bool) -> bool:
    """Enable native Windows 11 Mica on supported builds; never required for startup."""
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        if sys.getwindowsversion().build < 22621:
            return False

        dwmapi = ctypes.WinDLL("dwmapi")
        hwnd = wintypes.HWND(int(window.winId()))
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

        dark_value = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(dark_value), ctypes.sizeof(dark_value)
        )

        # DWMSBT_MAINWINDOW (2) is the Windows 11 Mica material.
        backdrop_value = ctypes.c_int(2)
        result = dwmapi.DwmSetWindowAttribute(
            hwnd, 38, ctypes.byref(backdrop_value), ctypes.sizeof(backdrop_value)
        )
        return result == 0
    except (AttributeError, OSError, TypeError, ValueError):
        return False

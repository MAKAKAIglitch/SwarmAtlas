from __future__ import annotations


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
    },
}


def get_palette(theme: str) -> dict[str, str]:
    return dict(PALETTES.get(theme, PALETTES["dark"]))

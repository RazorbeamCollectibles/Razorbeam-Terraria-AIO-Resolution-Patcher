from __future__ import annotations
from dataclasses import dataclass
import re

INT_MAX = 2_147_483_647

def dimension(text: str) -> int:
    text = text.strip()
    if not text.isascii() or not text.isdecimal() or len(text) > 10:
        raise ValueError("Resolution must contain positive whole numbers.")
    value = int(text)
    if not 1 <= value <= INT_MAX:
        raise ValueError("Terraria stores dimensions as signed 32-bit integers: 1 to 2,147,483,647.")
    return value

def resolution_warnings(width: int, height: int) -> list[str]:
    warnings = []
    if min(width, height) < 480:
        warnings.append("Very small dimension. Text, menus, or the entire game may be unusable.")
    if max(width / height, height / width) > 8:
        warnings.append("Extreme aspect ratio. Rendering and mouse interaction may behave unexpectedly.")
    if max(width, height) > 8192:
        warnings.append("Above 8192 pixels. XNA/GPU limits may prevent launch or cause a crash; raising software caps cannot add hardware support.")
    if width * height > 33_177_600:
        warnings.append("Large render surface. High VRAM use, poor performance, and out-of-memory crashes are possible.")
    return warnings

@dataclass(frozen=True)
class Monitor:
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False
    windows_id: int | None = None

    @property
    def right(self): return self.x + self.width

    @property
    def display_id(self):
        if self.windows_id is not None:
            return self.windows_id
        return self.gdi_id

    @property
    def gdi_id(self):
        match = re.search(r"DISPLAY(\d+)$", self.name, re.IGNORECASE)
        return int(match.group(1)) if match else None

def suggest_layout(monitors: list[Monitor]) -> dict:
    """Require three physically adjoining, same-size displays on one horizontal row.

    A different-sized display above that row is deliberately excluded. Negative
    desktop origins are retained. Similar resolution alone is insufficient.
    """
    candidates = []
    for left in monitors:
        row = sorted([m for m in monitors if (m.width, m.height) == (left.width, left.height)
                      and abs(m.y - left.y) <= 2], key=lambda m: m.x)
        for index in range(len(row) - 2):
            triple = row[index:index + 3]
            if all(abs(a.right - b.x) <= 2 for a, b in zip(triple, triple[1:])):
                candidates.append(triple)
    if candidates:
        row = max(candidates, key=lambda r: (any(m.primary for m in r), r[0].width * r[0].height))
        return {"width": row[-1].right - row[0].x, "height": row[0].height,
                "x": row[0].x, "y": row[0].y, "ui_width": row[1].width,
                "ui_height": row[1].height, "ui_x": row[1].x - row[0].x,
                "ui_y": row[1].y - row[0].y, "triple": True,
                "monitors": [m.name for m in row], "ui_monitor": row[1].name}
    m = next((m for m in monitors if m.primary), monitors[0] if monitors else Monitor("Fallback", 0, 0, 1920, 1080, True))
    return {"width": m.width, "height": m.height, "x": m.x, "y": m.y,
            "ui_width": m.width, "ui_height": m.height, "ui_x": 0, "ui_y": 0,
            "triple": False, "monitors": [m.name], "ui_monitor": m.name}

def engine_settings(width, height, x, y, centered, ui_x, ui_y, ui_width, ui_height, mode=1, stable_title=True,
                    display_screen="", skip_splash=False, center_splash=False,
                    splash_x=0, splash_y=0, splash_width=None, splash_height=None,
                    prevent_minimize=False):
    for value in (width, height, ui_width, ui_height): dimension(str(value))
    if not -INT_MAX <= x <= INT_MAX or not -INT_MAX <= y <= INT_MAX:
        raise ValueError("Window position exceeds Windows coordinate range.")
    centered = bool(centered)
    if not centered:
        ui_x, ui_y, ui_width, ui_height = 0, 0, width, height
    splash_width=width if splash_width is None else int(splash_width);splash_height=height if splash_height is None else int(splash_height)
    if splash_x < 0 or splash_y < 0 or splash_x+splash_width > width or splash_y+splash_height > height:
        raise ValueError("The startup splash viewport must fit inside the selected resolution.")
    if ui_x < 0 or ui_y < 0 or ui_x + ui_width > width or ui_y + ui_height > height:
        raise ValueError("The centered UI rectangle must fit inside the selected resolution.")
    return {"Schema": 3, "Width": width, "Height": height, "WindowX": x, "WindowY": y,
            "UiEnabled": int(centered), "UiX": ui_x, "UiY": ui_y, "UiWidth": ui_width,
            "UiHeight": ui_height, "Cap": min(INT_MAX, max(8192, max(width, height) + 256)),
            "Mode": mode, "StableTitle": int(stable_title), "DisplayScreen": str(display_screen),
            "SkipSplash": bool(skip_splash), "SplashEnabled": int(bool(center_splash)),
            "SplashX": int(splash_x), "SplashY": int(splash_y), "SplashWidth": splash_width, "SplashHeight": splash_height,
            "PreventMinimize": int(bool(prevent_minimize))}

"""Parse and match the switch hotkey (e.g. ``ctrl+shift+f12``)."""

from __future__ import annotations

from dataclasses import dataclass

# Low-level hooks report left/right-specific modifier codes; include the generic ones too.
MODIFIERS: dict[str, frozenset[int]] = {
    "ctrl": frozenset({0x11, 0xA2, 0xA3}),
    "shift": frozenset({0x10, 0xA0, 0xA1}),
    "alt": frozenset({0x12, 0xA4, 0xA5}),
    "win": frozenset({0x5B, 0x5C}),
}
_MOD_ALIASES = {"control": "ctrl", "menu": "alt", "super": "win", "meta": "win"}

NAMED_KEYS: dict[str, int] = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "pause": 0x13,
    "capslock": 0x14, "scrolllock": 0x91, "printscreen": 0x2C, "apps": 0x5D,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
NAMED_KEYS.update({f"f{i}": 0x70 + i - 1 for i in range(1, 25)})
NAMED_KEYS.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})


@dataclass(frozen=True)
class Hotkey:
    mods: frozenset[str]
    vk: int
    text: str

    def matches(self, vk: int, pressed: set[int]) -> bool:
        """True if ``vk`` is the trigger key and every required modifier is held."""
        return vk == self.vk and all(MODIFIERS[m] & pressed for m in self.mods)


def parse_hotkey(text: str) -> Hotkey:
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("hotkey is empty")
    *mod_parts, key = parts
    mods = set()
    for m in mod_parts:
        m = _MOD_ALIASES.get(m, m)
        if m not in MODIFIERS:
            raise ValueError(f"unknown modifier {m!r} in hotkey {text!r} (use ctrl, shift, alt, win)")
        mods.add(m)
    if key not in NAMED_KEYS:
        raise ValueError(f"unknown key {key!r} in hotkey {text!r}")
    if not mods and not key.startswith("f"):
        raise ValueError(f"hotkey {text!r} needs at least one modifier (or use an F-key)")
    pretty = "+".join([m.capitalize() for m in sorted(mods, key=list(MODIFIERS).index)] + [key.upper()])
    return Hotkey(frozenset(mods), NAMED_KEYS[key], pretty)

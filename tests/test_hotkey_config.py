import pytest

from kmshare.config import DEFAULT_INI, load_config
from kmshare.hotkey import parse_hotkey


def test_parse_default():
    hk = parse_hotkey("ctrl+shift+f12")
    assert hk.vk == 0x7B and hk.mods == {"ctrl", "shift"}
    assert hk.text == "Ctrl+Shift+F12"


def test_matches_left_or_right_modifiers():
    hk = parse_hotkey("Ctrl + Shift + F12")
    assert hk.matches(0x7B, {0xA2, 0xA1})      # LCtrl + RShift
    assert not hk.matches(0x7B, {0xA2})        # shift missing
    assert not hk.matches(0x7A, {0xA2, 0xA0})  # F11


@pytest.mark.parametrize("bad", ["", "ctrl+", "hyper+a", "ctrl+nope", "a"])
def test_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_shipped_ini_loads():
    cfg = load_config(DEFAULT_INI)
    assert cfg.port == 24800
    assert cfg.hotkey.text == "Ctrl+Shift+F12"


def test_ini_overrides(tmp_path):
    p = tmp_path / "k.ini"
    p.write_text("[network]\nport = 5000 ; comment\nserver_host = pc1\n[hotkey]\ntoggle = alt+q\n")
    cfg = load_config(p)
    assert (cfg.port, cfg.server_host, cfg.hotkey.vk) == (5000, "pc1", ord("Q"))


def test_ini_bad_port(tmp_path):
    p = tmp_path / "k.ini"
    p.write_text("[network]\nport = 70000\n")
    with pytest.raises(ValueError):
        load_config(p)

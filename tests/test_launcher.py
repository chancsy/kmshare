"""Launcher prompts: interactive mode with no arguments, ini + args otherwise."""

import pytest

import kmshare.__main__ as launcher
import kmshare.client
import kmshare.server


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Run main() with scripted input; return (exit code, captured role config, prompts shown)."""
    ini = tmp_path / "k.ini"
    ini.write_text("[network]\nport = 24800\nserver_host = 10.0.0.5\n[hotkey]\ntoggle = ctrl+shift+f12\n")
    captured, prompts = {}, []

    class FakeServer:
        def __init__(self, cfg, master):
            captured.update(role="server", cfg=cfg)

        def serve(self):
            pass

    class FakeClient:
        def __init__(self, cfg, master, host):
            captured.update(role="client", cfg=cfg, host=host)

        def run(self):
            pass

    monkeypatch.setattr(kmshare.server, "Server", FakeServer)
    monkeypatch.setattr(kmshare.client, "Client", FakeClient)
    monkeypatch.setattr(launcher, "derive_master_key", lambda p: b"k" * 32)
    monkeypatch.setattr(launcher.getpass, "getpass", lambda prompt="": "a long enough key")
    monkeypatch.setattr(launcher, "DEFAULT_INI", ini)
    monkeypatch.setattr(launcher.adminwatch, "start", lambda *a, **k: False)

    def _run(answers, argv):
        it = iter(answers)

        def fake_input(prompt=""):
            prompts.append(prompt)
            return next(it)

        monkeypatch.setattr("builtins.input", fake_input)
        return launcher.main(argv), captured, prompts  # DEFAULT_INI is patched to the temp ini

    return _run


def test_no_args_server_all_defaults(run):
    code, cap, prompts = run(["s", "n", "", "", "", "", "", "", "", "", ""], [])
    assert code == 0 and cap["role"] == "server"
    cfg = cap["cfg"]
    assert (cfg.port, cfg.bind, cfg.hotkey.text, cfg.beep, cfg.ping_interval, cfg.peer_timeout) == (
        24800, "0.0.0.0", "Ctrl+Shift+F12", True, 2.0, 6.0)
    assert cfg.edge_switch and cfg.edge.push_px == 8 and cfg.clipboard
    assert "  Share copied text with the other PC when control switches (y/n) [y]: " in prompts
    assert "  Port [24800]: " in prompts and "  Switch hotkey [Ctrl+Shift+F12]: " in prompts


def test_no_args_server_custom_values_and_reprompt(run):
    answers = ["s", "n", "99999", "5000", "", "nope+x", "ctrl+alt+k", "n", "1", "0.5", "3",
               "y", "y", "0", "5", "150", "10", "15", "n"]
    code, cap, prompts = run(answers, [])
    cfg = cap["cfg"]
    assert (cfg.port, cfg.hotkey.text, cfg.beep, cfg.ping_interval, cfg.peer_timeout) == (
        5000, "Ctrl+Alt+K", False, 1.0, 3.0)
    assert prompts.count("  Port [24800]: ") == 2  # 99999 rejected, asked again
    e = cfg.edge
    assert cfg.edge_switch and (e.push_px, e.delay_ms, e.corner_px, e.rearm_px) == (5, 150, 10, 15)
    assert prompts.count("    Push past the edge before switching, pixels [8]: ") == 2  # 0 rejected
    assert not cfg.clipboard


def test_no_args_server_edge_off(run):
    code, cap, prompts = run(["s", "n", "", "", "", "", "", "", "n", ""], [])
    assert not cap["cfg"].edge_switch
    assert not any("safeguards" in p for p in prompts)


def test_no_args_client(run):
    code, cap, prompts = run(["c", "n", "", "24801", "", ""], [])
    assert cap["role"] == "client" and cap["host"] == "10.0.0.5"
    assert cap["cfg"].port == 24801
    assert "  Server address (IP or host name) [10.0.0.5]: " in prompts


def test_args_skip_settings_prompts(run):
    code, cap, prompts = run([], ["--role", "client", "--host", "192.168.0.154"])
    assert code == 0 and cap["host"] == "192.168.0.154" and prompts == []


def test_role_only_arg_asks_nothing_else_for_server(run):
    code, cap, prompts = run([], ["--role", "server"])
    assert code == 0 and cap["role"] == "server" and prompts == []


def test_quick_start_server_asks_nothing_but_the_key(run, capsys):
    code, cap, prompts = run(["s", ""], [])
    assert code == 0 and cap["role"] == "server"
    assert prompts == ["Role [s/c]: ", "Use the settings in k.ini and only ask for the key (y/n) [y]: "]
    cfg = cap["cfg"]
    assert (cfg.port, cfg.hotkey.text, cfg.edge_switch, cfg.clipboard) == (24800, "Ctrl+Shift+F12", True, True)
    assert "  Using: port 24800, hotkey Ctrl+Shift+F12, edge switching on, clipboard sharing on" in capsys.readouterr().out


def test_quick_start_client_asks_only_the_server_address(run, capsys):
    code, cap, prompts = run(["c", "y", "192.168.0.9", "n"], [])
    assert code == 0 and cap["host"] == "192.168.0.9" and cap["cfg"].port == 24800
    assert prompts == ["Role [s/c]: ",
                       "Use the settings in k.ini and only ask for the server address and key (y/n) [y]: ",
                       "  Server address (IP or host name) [10.0.0.5]: ",
                       "  Save 192.168.0.9 as the server address for next time (y/n) [y]: "]
    assert "  Using: port 24800, clipboard sharing on" in capsys.readouterr().out


def test_typed_address_saved_to_local_ini_and_offered_next_time(run, tmp_path, capsys):
    code, cap, prompts = run(["c", "", "192.168.0.9", ""], [])
    local = tmp_path / "k.local.ini"
    assert code == 0 and "server_host = 192.168.0.9" in local.read_text(encoding="utf-8")
    assert "Saved to k.local.ini" in capsys.readouterr().out
    assert "server_host = 10.0.0.5\n" in (tmp_path / "k.ini").read_text()  # the main ini is untouched
    prompts.clear()
    code, cap, prompts = run(["c", "", ""], [])                              # Enter keeps the saved one
    assert cap["host"] == "192.168.0.9" and "  Server address (IP or host name) [192.168.0.9]: " in prompts
    assert not any(p.startswith("  Save ") for p in prompts)                # unchanged: no save question
    assert "+ k.local.ini" in capsys.readouterr().out                       # banner names the local file


def test_typed_address_not_saved_when_declined(run, tmp_path):
    run(["c", "", "192.168.0.9", "n"], [])
    assert not (tmp_path / "k.local.ini").exists()


def test_empty_key_asks_again(run, monkeypatch, capsys):
    keys = iter(["", "", "a long enough key"])
    monkeypatch.setattr(launcher.getpass, "getpass", lambda prompt="": next(keys))
    code, cap, prompts = run(["s", ""], [])
    assert code == 0 and cap["role"] == "server"
    assert capsys.readouterr().out.count("A key is required.") == 2

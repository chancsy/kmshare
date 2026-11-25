# kmshare

Use one Windows PC's keyboard and mouse on a second Windows PC over the LAN or wifi. Both PCs must know the same key, and all traffic is encrypted.

## What it does

- You run the same command, `python -m kmshare`, on both PCs. It asks which **role** this PC plays, then asks for each setting one at a time. Enter keeps the default shown in brackets, which comes from `kmshare.ini`. The roles:
  - **server**: the PC whose keyboard and mouse you're using.
  - **client**: the PC that gets controlled.
- **Ctrl+Shift+F12** on the server hands control to the client. Pressing it again gives control back. Typing, mouse movement, clicks (left, right, middle, back and forward), and vertical and horizontal scrolling all go to the client. While control is with the client, the server's own cursor is parked and ignores input.
- **Protected by a shared key** that you type at every launch and that is never saved. A PC with the wrong key is rejected, and everything on the network is encrypted.
- **Failsafes:** if the wifi drops or either side quits, the server goes back to local control within a few seconds, and the client releases any keys or buttons that were held down. Nothing stays stuck.
- Every behaviour, including each failure case, is described in [BEHAVIOR.md](BEHAVIOR.md).
- Port, server address, hotkey and keep-alive timing are set in `kmshare.ini`.

What the server console shows:

```text
09:42:45  server listening on 0.0.0.0:24800, waiting for a client ...
09:43:02  client connected: 192.168.1.23:51544  (press Ctrl+Shift+F12 to control it)
09:43:10  -> REMOTE: keyboard/mouse now control 192.168.1.23:51544  (Ctrl+Shift+F12 to return)
09:44:31  <- LOCAL (hotkey)
```

### What it doesn't do (yet)

- Moving to the other PC by pushing the cursor past the screen edge. Only the hotkey switches for now.
- Clipboard or file sharing.
- Typing on the client's **lock screen**, **UAC prompts** or **Ctrl+Alt+Del**. Windows blocks injected input there.
- More than one client at a time. A new client replaces the old one.
- Linux or macOS. It uses Win32 hooks and `SendInput`, so it's Windows ↔ Windows only.

## How it works

```text
 SERVER PC                                                CLIENT PC
 low-level hooks (pynput)                                 SendInput / SetCursorPos
 hotkey -> REMOTE: suppress local input,                  replays keys, clicks, wheel,
 park cursor, send relative moves      == TCP 24800 ==>   moves cursor by dx/dy
                                       AES-256-GCM        replies to keep-alive pings
```

The handshake proves that both sides hold the key without ever sending it. Both sides contribute a random value, so every session gets a fresh encryption key and recorded traffic can't be replayed later. The details are in the docstring at the top of `kmshare/channel.py`.

## Layout

| Path | What it is |
|---|---|
| `BEHAVIOR.md` | Full behaviour reference: modes, hotkey, connections, failsafes, security, log messages. |
| `requirements.txt` | Runtime dependencies. |
| `kmshare.ini` | Settings: port, server address, hotkey, beep, keep-alive. |
| `kmshare/__main__.py` | Asks for the role, the settings and the key, then starts the server or client. |
| `kmshare/server.py` | Hooks, hotkey state machine, connection handling, failsafes. |
| `kmshare/client.py` | Connect and reconnect loop, replays received events. |
| `kmshare/channel.py` | Key derivation, handshake, encrypted message framing. |
| `kmshare/winput.py` | Win32 cursor and `SendInput` wrappers. |
| `kmshare/hotkey.py`, `config.py` | Hotkey parsing, ini loading. |
| `tests/` | pytest suite (crypto, hotkey logic, end-to-end over localhost). |
| `.dev/` | Scratch space for temporary files and the pytest cache (git-ignored). |
| `CHANGELOG.md` | Release notes (current: 0.1.0). |

## Requirements

| What | Version / notes |
|---|---|
| Windows | 10 or 11 on **both** PCs. |
| [Python](https://www.python.org/downloads/windows/) | 3.10+ (developed on 3.14). Check with `python --version`. |
| A virtual environment | Recommended, so the packages don't go into your global Python. Any venv works. Setup step 1 creates one in `<repo>\.venv`. |
| [pynput](https://github.com/moses-palmer/pynput) | ≥ 1.7.6 (needs `suppress_event`). Installed from `requirements.txt`. |
| [cryptography](https://cryptography.io/en/latest/installation/) | ≥ 42. Installed from `requirements.txt`. |
| Network | Both PCs on the same LAN or wifi. The client must reach the server on TCP port 24800, or whatever you set in the ini. |

## Setup

`<repo>` = the folder this README is in. Do steps 1–2 on **both** PCs.

1. **Set up a venv and install the dependencies** (PowerShell). If you already have a venv you want to use, activate it and skip the `venv` line.
   ```powershell
   cd <repo>
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```
   Every `python` command below assumes this venv is active and the current folder is `<repo>`. kmshare isn't installed as a package, so `python -m kmshare` only works from `<repo>`.
2. **Optional: edit `kmshare.ini`.** Every setting is asked at launch anyway; the ini only sets the defaults shown in brackets, so you can just press Enter. On the client it's worth setting `server_host` to the server PC's IP address or host name, which you find by running `ipconfig` on the server and reading the "IPv4 Address" line. `port` must be the same on both PCs.
3. **Start the server** (on the PC with the keyboard and mouse you want to use):
   ```powershell
   cd <repo>
   python -m kmshare
   ```
   Answer `s`, press Enter at each setting to keep its default, then type a key. Use the same key on both PCs and make it at least 8 characters; a short phrase works well.
   ```text
   Role [s/c]: s
   Settings (press Enter to keep the value in [brackets], from the ini):
     Port [24800]:
     Listen address (0.0.0.0 = all adapters) [0.0.0.0]:
     Switch hotkey [Ctrl+Shift+F12]:
     Beep when switching (y/n) [y]:
     Keep-alive ping interval, seconds [2]:
     Drop the link after silence of, seconds [6]:
   Shared key (must match on both PCs):
   ```
   **On first launch, Windows Firewall may ask about Python.** Allow it on **Private networks**. If no prompt appeared and the client can't connect, add a rule from an **admin** PowerShell:
   ```powershell
   New-NetFirewallRule -DisplayName "kmshare" -Direction Inbound -Protocol TCP -LocalPort 24800 -Action Allow -Profile Private
   ```
   Also make sure the wifi network is set to **Private** in Windows: Settings → Network & internet → Wi-Fi → (your network) → Network profile type.
4. **Start the client** on the other PC: `python -m kmshare`, answer `c`, type the server's address (or press Enter if `server_host` is set in the ini), keep the other defaults, and type the same key. You should see:
   ```text
   connected to 192.168.1.10:24800. Waiting for the server's hotkey to take control
   ```
5. On the server, press **Ctrl+Shift+F12**. The keyboard and mouse now control the client. Press it again to come back.

Pitfalls:
- Use PowerShell, cmd or Windows Terminal. In the default Git Bash window (mintty), the hidden key prompt doesn't work; run `winpty python -m kmshare` there instead.
- If kmshare runs from an **administrator** terminal on only one of the PCs, see *Elevated windows* under Limitations.

## Test

Untested: the two-PC steps below haven't been run on real hardware yet. The automated suite and a hook start/stop smoke test pass on one PC.

| Step | Expected |
|---|---|
| `python -m pip install pytest` (once), then `python -m pytest` | `31 passed` |
| Start the server, then the client with the **wrong** key | Client: `handshake failed: server rejected the key`. Server: `rejected <ip>:<port>: wrong key` |
| Client with the right key | Server: `client connected: …`. Client: `connected to …` |
| Press Ctrl+Shift+F12, move the mouse, type in Notepad on the client | Cursor and text appear on the client; nothing happens on the server |
| Alt+Tab, scrolling, dragging a window on the client | All work on the client |
| Hold Shift, unplug the wifi or close the client window | Within 6 s the server logs `<- LOCAL (client lost: …)` and the server's own mouse works again; no stuck Shift anywhere |
| Press Ctrl+Shift+F12 with no client connected | Error beep and `no client connected, staying LOCAL` |

## Commands

| Command | What it does |
|---|---|
| `python -m kmshare` | **Interactive:** asks for the role, then each setting (Enter keeps the ini default), then the key, then runs. Answers aren't saved. Ctrl+C quits (switch back to LOCAL first, otherwise Ctrl+C goes to the client). |
| `python -m kmshare --role server` | **Any argument skips the settings questions** and uses the ini as it is; only the key is asked. |
| `python -m kmshare --role client --host 192.168.1.10` | Client using this server address, overriding the ini. |
| `python -m kmshare --config D:\other.ini` | Uses a different ini file. |
| `python -m kmshare -v` | Debug logging. |
| `python -m kmshare --help` | Lists the options. |

`kmshare.ini` keys:

| Section / key | Default | Meaning |
|---|---|---|
| `[network] port` | `24800` | TCP port. Must match on both PCs. |
| `[network] bind` | `0.0.0.0` | Server: local address to listen on. |
| `[network] server_host` | *(empty)* | Client: server address, offered as the default when asked. If it's empty and arguments are given without `--host`, you're asked for it. |
| `[hotkey] toggle` | `ctrl+shift+f12` | Switch key. Modifiers are `ctrl`, `shift`, `alt`, `win`. Keys are `a`–`z`, `0`–`9`, `f1`–`f24`, `space`, `tab`, `home`, `end`, … |
| `[misc] beep` | `true` | Play a system sound on each switch. |
| `[misc] ping_interval` / `peer_timeout` | `2` / `6` | Keep-alive ping interval and silence limit, in seconds. |

## Security notes & limitations

- **What's exposed:** the server listens on TCP `port` on all adapters (`bind = 0.0.0.0`). Anyone who can connect still has to pass the key check, and the server handles one handshake at a time with a 1 s pause after each failure, which slows down guessing. Set `bind` to the server's LAN IP to listen on one adapter only.
- **The key** is never written to disk. It's turned into an encryption key with scrypt, a deliberately slow function. A weak key could still be guessed offline by someone who records a handshake *by posing as your server*, so use a passphrase rather than a short password.
- **Git ignores** `.dev/` (temporary files, pytest cache) and `__pycache__/`. `kmshare.ini` contains no secrets and is tracked.
- **Elevated windows:** Windows stops a normal program from injecting input into admin windows, and stops its hooks from seeing input while an admin window is focused. On the client, run kmshare as administrator if you need to control admin apps. On the server, if an admin window is focused while you're controlling the client, input leaks to that window. Run kmshare as administrator there too, or avoid that situation.
- **Not supported:** lock screen, UAC prompts and Ctrl+Alt+Del on either PC. Windows' secure desktop blocks hooks and injected input.
- **Keyboard layout:** keys are sent as virtual-key codes, so both PCs should use the same keyboard layout.
- **Laptop touchpads:** cursor movement and clicks are forwarded. Untested: precision-touchpad gestures (two-finger scroll, three-finger swipes) may still act on the server.
- **Desktop PC without a mouse as the client:** Windows may hide the cursor when no mouse is plugged in, but it still moves.

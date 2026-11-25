# kmshare

Use one Windows PC's keyboard and mouse on a second Windows PC over the LAN or wifi. Both PCs must know the same key, and all traffic is encrypted.

**Supported OS: Windows 10 and 11 only, on both PCs.** Linux and macOS aren't supported: capturing and sending input uses Windows-only APIs (low-level hooks, `SendInput`).

## What it does

- You run the same command, `python -m kmshare`, on both PCs. It asks which **role** this PC plays, then asks for each setting one at a time. Enter keeps the default shown in brackets, which comes from `kmshare.ini`. The roles:
  - **server**: the PC whose keyboard and mouse you're using.
  - **client**: the PC that gets controlled.
- **Push the cursor past the screen edge** to move it onto the other PC, and push it back past the opposite edge on the other PC to return. When a client connects, the server asks where it sits (left, right, top or bottom). Safeguards stop accidental switching; see [Edge switching](#edge-switching). It can be turned off.
- **Ctrl+Shift+F12** on the server always works too: it hands control to the client, and pressing it again gives control back. Typing, mouse movement, clicks (left, right, middle, back and forward), and vertical and horizontal scrolling all go to the client. While control is with the client, the server's own cursor stays frozen where it was and ignores input.
- **Protected by a shared key** that you type at every launch and that is never saved. A PC with the wrong key is rejected, and everything on the network is encrypted.
- **Failsafes:** if the wifi drops or either side quits, the server goes back to local control within a few seconds, and the client releases any keys or buttons that were held down. Nothing stays stuck.
- Every behaviour, including each failure case, is described in [BEHAVIOR.md](BEHAVIOR.md).
- Port, server address, hotkey, edge switching and keep-alive timing are set in `kmshare.ini`.

What the server console shows:

```text
09:42:45  server listening on 0.0.0.0:24800, waiting for a client ...
09:43:02  client connected: 192.168.1.23:51544  (press Ctrl+Shift+F12 to control it)

Client 192.168.1.23:51544 connected. Where is it, seen from this PC?
  [l]eft  [r]ight  [t]op  [b]ottom   (Enter = right): r
09:43:05  client 192.168.1.23:51544 is to the RIGHT: push the cursor past this PC's right edge to switch (or Ctrl+Shift+F12)
09:43:10  -> REMOTE (right edge): keyboard/mouse now control 192.168.1.23:51544  (Ctrl+Shift+F12 to return)
09:44:31  <- LOCAL (edge)
```

### What it doesn't do (yet)

- Clipboard or file sharing (clipboard is planned, with its own on/off setting).
- Dragging something across to the other PC. Switching is blocked while a mouse button is held.
- Typing on the client's **lock screen**, **UAC prompts** or **Ctrl+Alt+Del**. Windows blocks injected input there.
- More than one client at a time. A new client replaces the old one.
- Linux or macOS. It uses Win32 hooks and `SendInput`, so it's Windows ↔ Windows only.

## How it works

```text
 SERVER PC                                                CLIENT PC
 low-level hooks (pynput)                                 SendInput / SetCursorPos
 hotkey -> REMOTE: suppress local input,                  replays keys, clicks, wheel,
 freeze cursor, send relative moves    == TCP 24800 ==>   moves cursor by dx/dy
                                       AES-256-GCM        replies to keep-alive pings
```

The handshake proves that both sides hold the key without ever sending it. Both sides contribute a random value, so every session gets a fresh encryption key and recorded traffic can't be replayed later. The details are in the docstring at the top of `kmshare/channel.py`.

## Layout

| Path | What it is |
|---|---|
| `BEHAVIOR.md` | Full behaviour reference: modes, hotkey, connections, failsafes, security, log messages. |
| `requirements.txt` | Runtime dependencies. |
| `kmshare.ini` | Settings: port, server address, hotkey, edge switching, beep, keep-alive. |
| `kmshare/__main__.py` | Asks for the role, the settings and the key, then starts the server or client. |
| `kmshare/server.py` | Hooks, hotkey state machine, edge detection, connection handling, failsafes. |
| `kmshare/client.py` | Connect and reconnect loop, replays received events, detects its own edge for the way back. |
| `kmshare/edge.py` | Edge detection, safeguards, monitor layouts, where the cursor appears after a crossing. |
| `kmshare/channel.py` | Key derivation, handshake, encrypted message framing. |
| `kmshare/winput.py` | Win32 cursor and `SendInput` wrappers. |
| `kmshare/adminwatch.py` | Warns when the focused window runs as administrator and kmshare doesn't. |
| `kmshare/hotkey.py`, `config.py` | Hotkey parsing, ini loading. |
| `tests/` | pytest suite (crypto, hotkey and edge logic, launcher, end-to-end over localhost). |
| `.dev/` | Scratch space for temporary files and the pytest cache (git-ignored). |
| `CHANGELOG.md` | Release notes (current: 0.2.0). |

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
     Edge switching: push the cursor past the screen edge to switch (y/n) [y]:
     Change the edge-switching safeguards (y/n) [n]:
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
5. **On the server, answer where the client sits.** As soon as the client connects, the server console asks `Where is it, seen from this PC?`. Click the server console window and type `l`, `r`, `t` or `b`, or press Enter for the ini default. It's asked once per client IP while the server runs, so a reconnect after a wifi drop doesn't ask again.
6. **Switch:** push the cursor past the server's edge on that side, or press **Ctrl+Shift+F12**. The keyboard and mouse now control the client. To come back, push past the client's opposite edge, or press the hotkey again.

Pitfalls:
- Use PowerShell, cmd or Windows Terminal. In the default Git Bash window (mintty), the hidden key prompt doesn't work; run `winpty python -m kmshare` there instead.
- If kmshare runs from an **administrator** terminal on only one of the PCs, see *Elevated windows* under Limitations.

## Test

All steps below have been tested on two PCs (server with two monitors of different sizes).

| Step | Expected |
|---|---|
| `python -m pip install pytest` (once), then `python -m pytest` | `79 passed` |
| Start the server, then the client with the **wrong** key | Client: `handshake failed: server rejected the key`. Server: `rejected <ip>:<port>: wrong key` |
| Client with the right key | Server: `client connected: …`. Client: `connected to …` |
| Press Ctrl+Shift+F12, move the mouse, type in Notepad on the client | Cursor and text appear on the client; nothing happens on the server |
| Alt+Tab, scrolling, dragging a window on the client | All work on the client |
| Hold Shift, unplug the wifi or close the client window | Within 6 s the server logs `<- LOCAL (client lost: …)` and the server's own mouse works again; no stuck Shift anywhere |
| Press Ctrl+Shift+F12 with no client connected | Error beep and `no client connected, staying LOCAL` |
| Answer the side question, push the cursor past that edge of the server | Cursor appears on the client's opposite edge at the same relative height; server logs `-> REMOTE (<side> edge)` |
| Push past the client's opposite edge | Cursor comes back to the server at the matching height; server logs `<- LOCAL (edge)` |
| Slide along the edge into a corner, drag with a button held, push only lightly | No switch (corner dead zone, button block, push threshold) |
| Untested: switch back and forth over the edge as fast as you can, 20+ times | The cursor never gets stuck on the client; if the server logs `server cursor slipped …`, the fix caught it |
| Untested: control the client for 10+ min **only moving the mouse** (no typing or clicking) | Teams on the client stays Available; screen saver/lock don't start |

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
| `[features] edge_switch` | `true` | Edge switching on or off. Off = hotkey only, and the server doesn't ask where the client is. |
| `[layout] client_side` | `right` | Default answer to the server's "where is the client" question: `left`, `right`, `top`, `bottom`. |
| `[layout] edge_push` | `8` | Pixels to push past the edge in total before switching (≥ 1). |
| `[layout] edge_delay_ms` | `0` | Also keep pushing this long, in ms (0 = off). |
| `[layout] edge_corner` | `40` | No switching within this many pixels of either end of an edge. |
| `[layout] edge_rearm` | `20` | After a switch, move this many pixels away from the edge before it can switch again. |
| `[misc] beep` | `true` | Play a system sound on each switch. |
| `[misc] ping_interval` / `peer_timeout` | `2` / `6` | Keep-alive ping interval and silence limit, in seconds. |

## Edge switching

- **Only outer edges count.** With several monitors, an edge that has another monitor beyond it is ignored, so moving between your own monitors works as normal.
- **Where the cursor appears:** at the same relative position along the edge. Leaving the server 25 % down its right edge puts the cursor 25 % down the client's left edge. With several monitors, the position is measured across the whole desktop and the cursor lands on the outermost monitor there.
- **Safeguards** (all set in `[layout]`, and the client uses the server's values):

  | Safeguard | Default | Stops |
  |---|---|---|
  | Push threshold `edge_push` | 8 px | Switching when you merely touch the edge, e.g. for a scrollbar at the screen's edge |
  | Delay `edge_delay_ms` | off | Switching on a quick brush past the edge (set e.g. 150–300 ms if that happens) |
  | Corner dead zone `edge_corner` | 40 px | Switching when aiming for corners (Start button, a window's ✕) |
  | Re-arm distance `edge_rearm` | 20 px | Bouncing straight back after a crossing |
  | Mouse button held | always | Switching mid-drag |

- **The hotkey always works**, edge switching or not.

## Security notes & limitations

- **What's exposed:** the server listens on TCP `port` on all adapters (`bind = 0.0.0.0`). Anyone who can connect still has to pass the key check, and the server handles one handshake at a time with a 1 s pause after each failure, which slows down guessing. Set `bind` to the server's LAN IP to listen on one adapter only.
- **The key** is never written to disk. It's turned into an encryption key with scrypt, a deliberately slow function. A weak key could still be guessed offline by someone who records a handshake *by posing as your server*, so use a passphrase rather than a short password.
- **Git ignores** `.dev/` (temporary files, pytest cache) and `__pycache__/`. `kmshare.ini` contains no secrets and is tracked.
- **Admin windows (for example the HoYoPlay launcher, Task Manager, installers):** Windows hides input aimed at an admin window from a program that isn't running as administrator.
  - **Server:** while an admin window has focus, kmshare doesn't see the mouse or keyboard. Edge switching and Ctrl+Shift+F12 don't work, and the cursor stays **trapped** on the server. Click any normal window (or the desktop or taskbar) and it works again. If an admin window is focused while you're controlling the client, your input can go to that window instead.
  - **Client:** the cursor still moves over admin windows, but clicks and keys don't reach them.
  - kmshare logs `focused window "HoYoPlay" (HYP.exe) runs as administrator: …` when this happens, and `admin window lost focus: …` when it's over. Windows that won't even say whether they're elevated (some game launchers and anti-cheat) are logged as `may run as administrator`.
  - **Fix: run kmshare as administrator** on the PC with the admin app (only that PC needs it):
    1. Start menu → type `Terminal` (or `PowerShell`) → right-click it → **Run as administrator** → **Yes** at the UAC prompt.
    2. An admin terminal starts in `C:\Windows\System32`, so go to the repo and activate the venv again:
       ```powershell
       cd <repo>
       .\.venv\Scripts\Activate.ps1
       python -m kmshare
       ```
       Use your own venv's `Activate.ps1` if it lives elsewhere.
    3. kmshare logs `running as administrator: admin windows can be controlled too` and no longer warns.
- **Not supported:** lock screen, UAC prompts and Ctrl+Alt+Del on either PC. Windows' secure desktop blocks hooks and injected input.
- **Keyboard layout:** keys are sent as virtual-key codes, so both PCs should use the same keyboard layout.
- **Laptop touchpads:** cursor movement and clicks are forwarded. Untested: precision-touchpad gestures (two-finger scroll, three-finger swipes) may still act on the server.
- **Desktop PC without a mouse as the client:** Windows may hide the cursor when no mouse is plugged in, but it still moves.

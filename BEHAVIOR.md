# kmshare behaviour reference

This file is the complete description of what kmshare does, in normal use and when something goes wrong. It covers version **0.1.0**. For setup, see [README.md](README.md).

Terms used below:

- **Server**: the PC whose physical keyboard and mouse are shared.
- **Client**: the PC being controlled.
- **LOCAL**: the server's input stays on the server.
- **REMOTE**: the server's input is blocked on the server and sent to the client.

## Contents

1. [Safety principles](#1-safety-principles)
2. [Launch](#2-launch)
3. [Configuration](#3-configuration)
4. [Hotkey](#4-hotkey)
5. [Server: LOCAL and REMOTE modes](#5-server-local-and-remote-modes)
6. [Server: connections](#6-server-connections)
7. [Client](#7-client)
8. [Failure and safety behaviour (all cases)](#8-failure-and-safety-behaviour-all-cases)
9. [Security](#9-security)
10. [Messages on the wire](#10-messages-on-the-wire)
11. [Log messages](#11-log-messages)
12. [Known gaps](#12-known-gaps)

---

## 1. Safety principles

kmshare is built so that **any failure gives control back to the server's own keyboard and mouse** and **never leaves a key or button held down**:

| Principle | How it's enforced |
|---|---|
| The way back always works | The hotkey is checked before anything is forwarded, so it works in both modes. When it switches back to LOCAL it is never sent to the client. |
| A dead link means LOCAL | If the connection is lost, times out or fails a check while REMOTE, the server switches to LOCAL straight away. |
| A dead process means LOCAL | The blocking of local input lives inside the kmshare process. If it crashes or is killed, Windows removes its hooks and normal input comes back by itself. |
| A hook error means LOCAL | If code inside a hook throws an error, it's logged and that event **passes through to the server**. Nothing is blocked by accident. |
| No stuck keys on the client | The client tracks every key and button it has pressed and releases them all when control returns, when the link drops, or when it quits. |
| No stuck keys on the server | A key pressed on the server before switching gets its key-up on the server, even if you release it after switching to REMOTE. |
| No accidental switching | Holding the hotkey doesn't keep flipping modes: auto-repeat and the key-up of the trigger key are swallowed. |
| Nothing works without the key | No input is sent or replayed until both sides have proven they hold the same key. |

---

## 2. Launch

Both roles start with the same command, `python -m kmshare`, run from the project folder with your venv active.

1. **Arguments** are read: `--role`, `--config`, `--host`, `-v` (debug logging).
2. **DPI awareness** is switched on (per-monitor v2, with older fallbacks), so every coordinate is in physical pixels and works with Windows display scaling.
3. **The config** is loaded. If it's missing or invalid, kmshare prints `Config error: …` and exits with code **2**.
4. A banner shows the version and config path: `kmshare 0.1.0: keyboard/mouse sharing  (config: …)`.
5. **Role:** taken from `--role`, otherwise you're asked `Role [s/c]:` (`s`, `server`, `c` or `client`, any case). The question repeats until the answer is valid.
6. **Settings.** What happens depends on whether any argument was given.

   **No arguments (interactive):** you're asked for each setting the chosen role uses, one at a time. The value in `[brackets]` comes from the ini, and **Enter keeps it**. An invalid answer prints `Invalid: <reason>` and the same question is asked again. Answers apply to **this run only** and are not saved to the ini.

   | Role | Question | Accepts |
   |---|---|---|
   | server | `Port [24800]` | 1–65535 |
   | server | `Listen address (0.0.0.0 = all adapters) [0.0.0.0]` | any address |
   | server | `Switch hotkey [Ctrl+Shift+F12]` | see [Hotkey](#4-hotkey) |
   | server | `Beep when switching (y/n) [y]` | `y`/`yes`/`true`/`1`/`on` or `n`/`no`/`false`/`0`/`off` |
   | server | `Keep-alive ping interval, seconds [2]` | more than 0 |
   | server | `Drop the link after silence of, seconds [6]` | more than the ping interval you just entered. The default is the ini value, raised to twice the ping interval if it's smaller. |
   | client | `Server address (IP or host name) [<server_host>]` | required; if the ini value is empty, there's no default |
   | client | `Port [24800]` | 1–65535; must match the server |
   | client | `Drop the link after silence of, seconds [6]` | more than the ini's `ping_interval` |

   **Any argument given** (for example just `--role server`): no settings questions. Settings come from the ini and the arguments. The only extra question is the server address, for a client when neither `--host` nor `server_host` is set: `Server address (IP or host name):`, repeated until you type something.
7. **Key:** `Shared key (must match on both PCs):`. Typing is hidden.
   - An empty key prints `A key is required.` and exits with code **2**.
   - A key shorter than 8 characters prints a warning but is accepted.
   - The key is turned into a 256-bit master key with scrypt (about 0.1 s, deliberately slow). The typed text is then discarded and never written anywhere.
8. **Run** the chosen role until it's stopped.

| Ending | Output | Exit code |
|---|---|---|
| Ctrl+C | `Stopped.` | 0 |
| Network error at startup (e.g. port already in use) | `Network error: …` | 1 |
| Config error, or empty key | `Config error: …` / `A key is required.` | 2 |

Note: kmshare can't tell whether the two keys match until the client connects. A typo shows up as a rejected handshake, not at the prompt.

---

## 3. Configuration

`kmshare.ini` is read once at launch; restart kmshare to apply changes. With no arguments, its values become the defaults offered at the settings questions ([Launch](#2-launch)); the answers are not written back. A missing section or key uses the default. Comments start with `;` or `#`, including at the end of a line.

| Key | Default | Rules |
|---|---|---|
| `[network] port` | `24800` | 0–65535. `0` lets Windows pick a random free port and the server logs it (only useful for testing, since the client needs a fixed port). |
| `[network] bind` | `0.0.0.0` | Server only. If empty, the default is used. |
| `[network] server_host` | empty | Client only. If empty, you're asked at launch. `--host` overrides it. |
| `[hotkey] toggle` | `ctrl+shift+f12` | See [Hotkey](#4-hotkey). An invalid value is a config error. |
| `[misc] beep` | `true` | Plays a system sound when the mode changes. |
| `[misc] ping_interval` | `2` | Seconds between keep-alive pings. Must be > 0. |
| `[misc] peer_timeout` | `6` | Seconds of silence before the link counts as dead. Must be > `ping_interval`. |

---

## 4. Hotkey

- **Format:** modifiers and one key joined with `+`, any case, spaces allowed (`Ctrl + Shift + F12`).
- **Modifiers:** `ctrl`, `shift`, `alt`, `win`. Also accepted: `control` (= ctrl), `menu` (= alt), `super` and `meta` (= win).
- **Keys:** `a`–`z`, `0`–`9`, `f1`–`f24`, `space`, `tab`, `enter`/`return`, `esc`/`escape`, `backspace`, `insert`/`ins`, `delete`/`del`, `home`, `end`, `pageup`/`pgup`, `pagedown`/`pgdn`, arrow keys (`left`, `up`, `right`, `down`), `pause`, `capslock`, `scrolllock`, `printscreen`, `apps`, and the punctuation keys `` ` `` `-` `=` `[` `]` `\` `;` `'` `,` `.` `/`.
- **A modifier is required** unless the key is an F-key. `f12` alone is allowed; `a` alone is not.
- **Matching:**
  - Left and right modifiers both count (LCtrl or RCtrl).
  - The trigger fires when its key goes down while every required modifier is held.
  - **Extra modifiers don't stop it**, so Ctrl+Alt+Shift+F12 also fires the default hotkey.
- **Only physical keypresses count.** Keys sent by software (AutoHotkey, remote-desktop tools, kmshare itself) never fire the hotkey.
- **The trigger key itself is always swallowed:** its key-down, its auto-repeat while held, and its key-up never reach any app on either PC. Only the first key-down switches the mode.
- **The modifiers of the hotkey** are handled like any other key:
  - LOCAL → REMOTE: they were pressed on the server, so they're released on the server.
  - REMOTE → LOCAL: they were sent to the client, and the client releases them when control returns.

---

## 5. Server: LOCAL and REMOTE modes

The server starts in **LOCAL** and installs low-level keyboard and mouse hooks, which see all physical input on the PC.

### In LOCAL

- Every key and mouse event goes to the server's apps as normal. Nothing is sent to the client.
- kmshare remembers which keys were pressed while LOCAL. This is how their key-ups are kept on the server later.

### Switching LOCAL → REMOTE (hotkey)

| If | Then |
|---|---|
| **No client connected** | Stays LOCAL. Logs `no client connected, staying LOCAL` and plays the error sound. |
| A client is connected | 1. Saves the current cursor position.<br>2. Moves the cursor to the **centre of the primary monitor** and keeps it there.<br>3. Sends `enter` to the client.<br>4. Logs `-> REMOTE: keyboard/mouse now control <ip:port>  (<hotkey> to return)`.<br>5. Plays the "OK" sound. |

### In REMOTE

**Keyboard:**
- Every physical key-down and key-up is **blocked on the server** and sent to the client with its virtual-key code, scan code and extended-key flag (so arrow keys, right Ctrl and right Alt stay distinct).
- Exception 1: the key-up of a key that went down **before** switching to REMOTE goes to the server, not the client. Example: you're still holding Shift from the hotkey.
- Exception 2: the hotkey trigger key (see [Hotkey](#4-hotkey)).
- Windows-reserved combinations still act on the server (see [Known gaps](#12-known-gaps)).

**Mouse:**
- All physical mouse events are **blocked on the server**, including types kmshare doesn't forward. The server's cursor stays parked.
- **Movement** is sent as relative motion: how far the cursor *would* have moved from the parked point. Windows pointer speed and acceleration are applied **once**, on the server, so the feel matches the server's mouse settings.
- Moves that queue up between sends are added together into one message. No motion is lost; there are just fewer packets.
- **Buttons:** left, right, middle, X1 (back), X2 (forward). Presses and releases are sent separately, so dragging works.
- **Wheel:** vertical and horizontal, with the raw amount, so high-resolution and smooth-scrolling devices keep their precision.

### Switching REMOTE → LOCAL

This happens for any of these reasons: **hotkey**, **client lost**, **client replaced**, **server stopping**.

1. Sends `rel` to the client (if the link still works), which releases everything the client is holding.
2. Puts the server's cursor back where it was before switching to REMOTE.
3. Logs `<- LOCAL (<reason>)`. Reasons:
   - `hotkey`
   - `client lost: <detail>`
   - `client replaced`
   - `server stopping`
4. Plays the "asterisk" sound.

---

## 6. Server: connections

| Situation | Behaviour |
|---|---|
| Startup | Listens on `bind:port` and logs `server listening on <bind>:<port>, waiting for a client ...` |
| Incoming connection | Runs the handshake ([Security](#9-security)). **One handshake at a time.** Each one has a 5 s limit. |
| Handshake fails (`wrong key`, `not a kmshare client (or a different version)`, `handshake timed out`, `client hung up during handshake`, or a network error) | Logs `rejected <ip:port>: <reason>`, closes the connection, then **waits 1 s before taking the next handshake**. This limits key guessing to about one try per second. |
| Handshake succeeds | Logs `client connected: <ip:port>  (press <hotkey> to control it)`. Stays in its current mode (LOCAL). |
| A second client connects (correct key) while one is connected | The new client wins. If REMOTE, the server first switches to LOCAL (`client replaced`). The old connection is closed and logged as `client <old> disconnected (replaced by <new>)`. |
| Keep-alive | Sends `ping` every `ping_interval` seconds. Each `pong` resets the timer. |
| No `pong` for `peer_timeout` seconds | Drops the client: `no response from client`. Switches to LOCAL if needed. |
| Client closes, network error, a send fails, or a message fails its check | Drops the client with that reason. Switches to LOCAL if needed. Logs `client <ip:port> disconnected (<reason>)`. |
| Events still queued from a previous connection | Discarded. They're never delivered to a newer client. |
| Ctrl+C in the server console (while LOCAL) | Switches to LOCAL if needed, closes the client, removes the hooks, closes the listening port, prints `Stopped.` |

---

## 7. Client

### Connecting

| Situation | Behaviour |
|---|---|
| Startup | Logs `client: connecting to <host>:<port> ...` |
| Server can't be reached (TCP connect has a 5 s limit) | Logs `cannot reach server …, retrying ...` **once per distinct error** (no repeated spam) and retries every **3 s**, forever. |
| Handshake fails (wrong key, wrong program, timeout) | Logs `handshake failed: <reason>` (e.g. `server rejected the key`) and retries every **5 s**, forever. With the wrong key it keeps getting rejected until you restart it with the right key. |
| Connected | Logs `connected to <host>:<port>. Waiting for the server's hotkey to take control` |
| Disconnected for any reason | **Releases every held key and button**, logs `disconnected: <reason>`, waits 2 s, logs `reconnecting ...`, and goes back to connecting. |
| No message from the server for `peer_timeout` seconds | Disconnects with `server not responding` (then as above). The server's pings keep this from happening on a healthy link. |
| Ctrl+C in the client console | Releases every held key and button, closes the connection, prints `Stopped.` |

### Replaying input

| Message | Action on the client |
|---|---|
| Mouse move | Reads the current cursor position and moves it by the received amount using `SetCursorPos`. Windows keeps the cursor on the screen. Using `SetCursorPos` places the cursor exactly, with no second layer of acceleration, and apps still see normal mouse-move events (hover and drag work). |
| Key | Sends the key with the same virtual-key code, scan code and extended-key flag. Remembers it while it's held down. |
| Button | Sends the press or release. Remembers held buttons. |
| Wheel | Sends a vertical or horizontal wheel event with the exact amount received. |
| `enter` | Logs `server took control`. |
| `rel` | Releases every held key and button. Logs `server released control`. |
| `ping` | Replies `pong`. |
| Malformed message (missing or wrong field) | Logs `ignored malformed message …` and continues. |
| Unknown message type | Ignored without a log line (leaves room for later versions). |

- Events kmshare sends on the client are tagged `KMSH` (`0x4B4D5348` in the event's `dwExtraInfo` field), so they can be recognised.
- The client's own keyboard and mouse **keep working** the whole time. Both sets of input can be used at once.

---

## 8. Failure and safety behaviour (all cases)

| Event | Server | Client |
|---|---|---|
| You press the hotkey while REMOTE | → LOCAL, cursor restored | Releases everything held |
| Wifi drops or the cable is pulled | → LOCAL within `peer_timeout` (6 s) | Releases everything held within `peer_timeout`, then reconnects every 3 s |
| Client app closed with Ctrl+C | → LOCAL as soon as the connection closes | Releases everything before quitting |
| Client window closed with **X** or the process is killed | → LOCAL as soon as the connection closes, or at worst after `peer_timeout` | Process ends. Windows ends injected key presses when the process dies, but this hasn't been verified for every app (untested). |
| Client PC sleeps or freezes | → LOCAL after `peer_timeout` | After waking: detects silence, releases everything, reconnects |
| Server app closed with Ctrl+C | Already LOCAL (Ctrl+C only reaches the server while LOCAL) | Connection closes → releases everything, reconnects |
| Server window closed with **X** or the process is killed while REMOTE | Hooks disappear with the process → **local input returns immediately**. The cursor stays where it was parked. | Connection closes, or goes silent until `peer_timeout` → releases everything, reconnects |
| Server PC sleeps, locks or freezes | Hooks don't see secure-desktop input. Mode is kept. | Silence → releases everything after `peer_timeout` |
| A message fails its check (tampering, replay, reordering, wrong key mid-session, frame > 64 KB) | Drops the client → LOCAL | Disconnects → releases everything, reconnects |
| A hook throws an error | Logs `keyboard hook error` / `mouse hook error`. **That event goes to the server.** The hooks keep running. | – |
| A new client with the correct key connects | Old client replaced; → LOCAL first if REMOTE | Old client: disconnected → releases everything, reconnects (and becomes the current client again, replacing the newer one) |
| A guessing or wrong-key connection | Rejected; next handshake only after 1 s | – |
| Hotkey with no client connected | Stays LOCAL, error sound | – |
| Hotkey held down | Switches once (auto-repeat swallowed) | – |

---

## 9. Security

**What's protected:** everything sent between the two PCs (every key, so passwords too) is encrypted and authenticated. Input from anyone who doesn't have the key is never accepted.

**Key derivation:**
- `master = scrypt(key, salt="kmshare/v1/kdf", n=2^15, r=8, p=1)`, giving 32 bytes.
- The salt is fixed, so the same key gives the same master key on every PC.

**Handshake** (5 s limit, protocol tag `KMS1`):

```text
server → client : "KMS1" | server_nonce (16 random bytes)
client → server : "KMS1" | client_nonce (16) | HMAC-SHA256(master, "client"|sn|cn)
server → client : HMAC-SHA256(master, "server"|sn|cn)
session_key     = HMAC-SHA256(master, "session"|sn|cn)
```

- **Both sides prove they hold the key**; the key itself is never sent. The server checks first and closes the connection if the client is wrong. The client then checks the server's proof, so a fake server can't drive the client either.
- Both sides add random values, so **every session has a new key**. Traffic recorded from an earlier session is useless later.
- Proofs are compared in constant time, so timing can't leak them.

**After the handshake:**
- Each message is a 4-byte length followed by **AES-256-GCM**-encrypted JSON.
- Each direction has its own message counter, built into the encryption (the "nonce": direction byte + counter). Any message that is changed, repeated, out of order or forged fails the check and **ends the connection**.
- Messages over 64 KB are rejected.

**Limits:**
- **The server listens on every network adapter** when `bind = 0.0.0.0`.
- A fake server could collect one client proof and then **guess the key offline**. scrypt makes each guess slow, but a short or common key could still fall. Use a passphrase.
- Online guessing against the real server is limited to about 1 guess per second.
- Nothing is written to disk: no key, no logs, no history. Logs go to the console only.

---

## 10. Messages on the wire

Each message is compact JSON, inside the encryption.

| Direction | Message | Meaning |
|---|---|---|
| S → C | `{"t":"m","x":dx,"y":dy}` | Relative mouse move (several may be combined into one) |
| S → C | `{"t":"k","v":vk,"s":scan,"e":ext,"d":down}` | Key down or up |
| S → C | `{"t":"b","b":"left\|right\|middle\|x1\|x2","d":down}` | Mouse button |
| S → C | `{"t":"w","d":delta,"h":horizontal}` | Wheel; one notch = 120 |
| S → C | `{"t":"enter"}` | Server switched to REMOTE |
| S → C | `{"t":"rel"}` | Release everything (server switched to LOCAL) |
| S → C | `{"t":"ping"}` | Keep-alive |
| C → S | `{"t":"pong"}` | Keep-alive reply |

---

## 11. Log messages

Timestamps are `HH:MM:SS`. `-v` adds debug output.

| Role | Message | Meaning |
|---|---|---|
| S | `server listening on <bind>:<port>, waiting for a client ...` | Started |
| S | `client connected: <ip:port>  (press <hotkey> to control it)` | Client passed the handshake |
| S | `rejected <ip:port>: <reason>` | Handshake failed (see [Connections](#6-server-connections)) |
| S | `-> REMOTE: keyboard/mouse now control <ip:port>  (<hotkey> to return)` | Switched to REMOTE |
| S | `<- LOCAL (<reason>)` | Switched to LOCAL |
| S | `no client connected, staying LOCAL` | Hotkey pressed with no client |
| S | `client <ip:port> disconnected (<reason>)` | Client gone |
| S | `keyboard hook error` / `mouse hook error` + traceback | Bug in a hook; that event went to the server |
| C | `client: connecting to <host>:<port> ...` | Started |
| C | `cannot reach server <host>:<port> (<error>), retrying ...` | Network or firewall problem |
| C | `handshake failed: server rejected the key` | Keys differ |
| C | `handshake failed: server failed the key check (different key?)` | The server couldn't prove it has the key (fake server, or a bug) |
| C | `connected to <host>:<port>. Waiting for the server's hotkey to take control` | Ready |
| C | `server took control` / `server released control` | Mode changes on the server |
| C | `disconnected: <reason>` then `reconnecting ...` | Link lost; everything held was released |
| C | `ignored malformed message …` | Bad message (skipped) |

---

## 12. Known gaps

- **Secure desktop:** lock screen, UAC prompts and Ctrl+Alt+Del can't be controlled on the client, and the server's hooks can't see them.
- **Shortcuts Windows reserves on the server:** Ctrl+Alt+Del always acts on the server. Win+L locks the server; the hook can't stop it (untested).
- **Elevated windows:** if kmshare isn't running as administrator:
  - On the server, input to a focused admin window bypasses the hooks, so it isn't sent to the client.
  - On the client, kmshare can't send input into admin windows.
- **Keyboard layout:** keys are sent as virtual-key codes, so both PCs should use the same layout.
- **Precision-touchpad gestures** (two-finger scroll, three-finger swipes) may still act on the server (untested).
- **Not implemented yet:** switching at the screen edge, clipboard sync, more than one client at a time, operating systems other than Windows.
- **Ctrl+C while REMOTE** goes to the client, not to kmshare. Switch back to LOCAL first to quit the server.

# kmshare behaviour reference

This file is the complete description of what kmshare does, in normal use and when something goes wrong. It covers version **0.3.0** (see [CHANGELOG.md](CHANGELOG.md)). For setup, see [README.md](README.md).

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
6. [Edge switching](#6-edge-switching)
7. [Clipboard sharing](#7-clipboard-sharing)
8. [Server: connections](#8-server-connections)
9. [Client](#9-client)
10. [Failure and safety behaviour (all cases)](#10-failure-and-safety-behaviour-all-cases)
11. [Security](#11-security)
12. [Messages on the wire](#12-messages-on-the-wire)
13. [Log messages](#13-log-messages)
14. [Known gaps](#14-known-gaps)

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
| No accidental switching | Holding the hotkey doesn't keep flipping modes: auto-repeat and the key-up of the trigger key are swallowed. Edge switching needs a real push past an outer edge, ignores corners, never fires mid-drag, and can't bounce straight back ([Edge switching](#6-edge-switching)). |
| No cursor yank on return | When control comes back and kmshare places the cursor, mouse movements Windows had already computed from the frozen spot are dropped, so they can't pull the cursor back there ([Switching REMOTE → LOCAL](#switching-remote--local)). |
| Edge switching can't strand you | The hotkey works whatever the edge state. A `leave` from the client is only acted on while the server is REMOTE for that same connection. |
| The clipboard can't stall input | Clipboard work runs on its own thread with short retries, never inside the hooks. Text copied before launch, or marked private by the copying app (e.g. KeePass), is never sent. |
| Nothing works without the key | No input is sent or replayed until both sides have proven they hold the same key. |

---

## 2. Launch

Both roles start with the same command, `python -m kmshare`, run from the project folder with your venv active.

1. **Arguments** are read: `--role`, `--config`, `--host`, `-v` (debug logging).
2. **DPI awareness** is switched on (per-monitor v2, with older fallbacks), so every coordinate is in physical pixels and works with Windows display scaling.
3. **The config** is loaded. If it's missing or invalid, kmshare prints `Config error: …` and exits with code **2**.
4. A banner shows the version and config path: `kmshare 0.3.0: keyboard/mouse sharing  (config: …)`.
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
   | server | `Edge switching: push the cursor past the screen edge to switch (y/n) [y]` | y/n (default from `[features] edge_switch`). `n` = hotkey only; the next questions are skipped. |
   | server | `Change the edge-switching safeguards (y/n) [n]` | y/n. `n` keeps the four values from `[layout]`. |
   | server | `Push past the edge before switching, pixels [8]` | 1 or more (only if you answered `y` above) |
   | server | `Keep pushing at least, ms (0 = off) [0]` | 0 or more |
   | server | `No switching this close to a corner, pixels [40]` | 0 or more |
   | server | `After a switch, move this far from the edge first, pixels [20]` | 0 or more |
   | server | `Share copied text with the other PC when control switches (y/n) [y]` | y/n (default from `[features] clipboard`). Asked even if edge switching is off. |
   | client | `Server address (IP or host name) [<server_host>]` | required; if the ini value is empty, there's no default |
   | client | `Port [24800]` | 1–65535; must match the server |
   | client | `Drop the link after silence of, seconds [6]` | more than the ini's `ping_interval` |
   | client | `Share copied text with the other PC when control switches (y/n) [y]` | y/n (default from `[features] clipboard`) |

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
| `[features] edge_switch` | `true` | Server only. Edge switching on/off. Off: no side question, hotkey only. |
| `[features] clipboard` | `true` | Both PCs. Clipboard sharing on/off; it only runs when both PCs have it on ([Clipboard sharing](#7-clipboard-sharing)). |
| `[clipboard] max_kb` | `1024` | Both PCs. Largest text sent or accepted, in KB (UTF-8). Must be ≥ 1. |
| `[layout] client_side` | `right` | Server only. Default answer to the side question. One of `left`, `right`, `top`, `bottom` (any case); anything else is a config error. |
| `[layout] edge_push` | `8` | Server only (sent to the client). Must be ≥ 1. |
| `[layout] edge_delay_ms` | `0` | Server only (sent to the client). ≥ 0; 0 = no delay. |
| `[layout] edge_corner` | `40` | Server only (sent to the client). ≥ 0. |
| `[layout] edge_rearm` | `20` | Server only (sent to the client). ≥ 0. |
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

### Switching LOCAL → REMOTE (hotkey or edge)

| If | Then |
|---|---|
| **No client connected** | Stays LOCAL. Logs `no client connected, staying LOCAL` and plays the error sound. |
| A client is connected | 1. Remembers the cursor position as the **frozen spot**.<br>2. The cursor **stays frozen there**, wherever it is (at the edge after an edge crossing). It doesn't move until control returns.<br>3. Sends `enter` to the client. After an **edge** crossing it includes the client's entry edge and the crossing position, so the client can place its cursor; after the **hotkey** the client's cursor stays where it is.<br>4. Logs `-> REMOTE (hotkey)` or `-> REMOTE (<side> edge)`, then `: keyboard/mouse now control <ip:port>  (<hotkey> to return)`.<br>5. Plays the "OK" sound. |

The hotkey works at any time. An edge push only switches when a client is connected **and** its side has been answered ([Edge switching](#6-edge-switching)).

### In REMOTE

**Keyboard:**
- Every physical key-down and key-up is **blocked on the server** and sent to the client with its virtual-key code, scan code and extended-key flag (so arrow keys, right Ctrl and right Alt stay distinct).
- Exception 1: the key-up of a key that went down **before** switching to REMOTE goes to the server, not the client. Example: you're still holding Shift from the hotkey.
- Exception 2: the hotkey trigger key (see [Hotkey](#4-hotkey)).
- Windows-reserved combinations still act on the server (see [Known gaps](#14-known-gaps)).

**Mouse:**
- All physical mouse events are **blocked on the server**, including types kmshare doesn't forward. The server's cursor stays frozen.
- **Movement** is sent as relative motion: how far the cursor *would* have moved from the frozen spot. Windows reports this even when the spot is at a screen edge, so movement towards the edge is measured too. Windows pointer speed and acceleration are applied **once**, on the server, so the feel matches the server's mouse settings.
- **Measured from the real cursor:** each move is measured from where the server's cursor actually is, which is normally the frozen spot. If the cursor ever moved anyway (an event got past the hook, or another program moved it), kmshare logs `server cursor slipped <n> px while controlling the client …; corrected` once per session and freezes it at the new spot. Without this, every later move would carry the same offset, pinning the client's cursor to one edge with only the other direction still working. A hotkey return still puts the cursor back where it was frozen.
- Moves that queue up between sends are added together into one message. No motion is lost; there are just fewer packets.
- **Buttons:** left, right, middle, X1 (back), X2 (forward). Presses and releases are sent separately, so dragging works.
- **Wheel:** vertical and horizontal, with the raw amount, so high-resolution and smooth-scrolling devices keep their precision.

### Switching REMOTE → LOCAL

This happens for any of these reasons: **hotkey**, **edge** (the client reported a push past its edge), **client lost**, **client replaced**, **server stopping**.

1. Sends `rel` to the client (if the link still works), which releases everything the client is holding.
2. After the **hotkey**, a lost client, or the server stopping, the cursor is simply still at the frozen spot. **After an edge return** it's moved to the server's edge at the matching position (see [Edge switching](#6-edge-switching)).
   - **Race guard:** Windows computes a movement's target before kmshare's hook sees it. So a movement computed from the frozen spot can still be in the hook when the cursor is placed, and letting it through would yank the cursor back to the frozen spot. For up to 0.25 s after the cursor was moved, kmshare therefore drops any move whose target is nearer the frozen spot than the cursor's real position. The first normal move ends the guard. When the cursor isn't moved (hotkey return), there's nothing to guard.
3. **Turns the server's edge off until the cursor moves away from it** (re-arm), so it can't bounce straight back to the client.
4. Logs `<- LOCAL (<reason>)`. Reasons:
   - `hotkey`
   - `edge`
   - `client lost: <detail>`
   - `client replaced`
   - `server stopping`
5. Plays the "asterisk" sound.

---

## 6. Edge switching

Push the cursor past the edge of one PC's screen to move it onto the other PC. It's on unless `[features] edge_switch = false`, or you answered `n` at launch. The hotkey works either way.

### Which side the client is on

- When a client connects (after the handshake), the **server console** asks:
  ```text
  Client 192.168.0.235:51544 connected. Where is it, seen from this PC?
    [l]eft  [r]ight  [t]op  [b]ottom   (Enter = right):
  ```
  `l`/`left`, `r`/`right`, `t`/`top` or `b`/`bottom`, any case. Enter takes `[layout] client_side`. Anything else prints `Please answer l, r, t or b.` and asks again.
- **The answer is remembered per client IP until the server stops**, so a reconnect after a wifi drop doesn't ask again. To change it, restart the server.
- If the server console isn't interactive (input redirected), the default is used without asking.
- **Until it's answered, only the hotkey switches.** The connection works normally meanwhile: pings, hotkey and everything else.
- Once answered, the server logs `client <ip:port> is to the <SIDE>: push the cursor past this PC's <side> edge to switch (or <hotkey>)`. It then sends the client its exit edge (the opposite one) and the safeguard values. The client logs `the server is on my <SIDE>: push past my <side> edge to go back`.

### When a push counts

The rules are the same on both PCs: the server checks its own edge while LOCAL, and the client checks its own edge while the server is controlling it.

1. **Only the chosen edge counts**: the server's `side` edge, and the client's opposite edge.
2. **Only outer edges count.** The edge must be the outside of the whole desktop at that point. If another monitor lies beyond it, it's ignored, so moving between your own monitors is unaffected. Each PC re-reads its monitor layout at most every 2 s, so plugging a monitor in or out is picked up.
3. **Push threshold:** the movement past the edge is added up, using where the cursor *would* have gone. The switch needs at least `edge_push` pixels in total (default 8). A fast flick that overshoots by more than that switches at once.
4. **Delay** (`edge_delay_ms`, default 0 = off): the push must also have lasted at least this long, counted from the first push event. You have to keep pushing; just resting at the edge doesn't count.
5. **Corner dead zone:** no switching within `edge_corner` pixels (default 40) of either end of that monitor's edge.
6. **Mouse button held:** no switching while any button is held. On the server that's a physical button; on the client, a button kmshare pressed. Drags stay on their PC.
7. Moving back inside, landing in a corner, or holding a button **resets** the push count and delay timer.
8. **Re-arm:** after any switch (edge or hotkey), the edge turns off until the cursor has been at least `edge_rearm` pixels (default 20) from it. This stops it bouncing straight back, and stops a cursor left resting at the edge from switching again by itself.

### Where the cursor appears

- The crossing position is sent as a fraction (0 = top or left end, 1 = bottom or right end) of the **whole desktop's** extent along that edge, the same scale the receiving PC uses to place the cursor. Pushing straight back therefore returns to the same spot, whatever the monitor sizes on either PC.
- The receiving PC maps that fraction across its **whole desktop** along the entry edge, and puts the cursor on the **outermost monitor** at that point. If the point falls in a gap between monitors of different sizes, the cursor goes to the nearest monitor.
- Example: leave the server 25 % down its right edge, and the cursor appears 25 % down the client's left edge.
- **Client entry** (server → client): the server's cursor stays frozen at the edge where it crossed, and the client moves its cursor to the entry point.
- **Return** (client → server): the client sends `leave` once and waits. The server switches to LOCAL, puts its cursor at the matching point on its `side` edge (with the race guard above), and disarms that edge (re-arm). A `leave` that arrives after the hotkey already switched back, or that belongs to an older connection, is ignored.

---

## 7. Clipboard sharing

Text you copy on one PC is there to paste on the other after control switches. It's on unless `[features] clipboard = false` on **either** PC, or you answered `n` at launch.

### When text moves

| Event | What's sent |
|---|---|
| Server → REMOTE (hotkey or edge) | The **server's** clipboard goes to the client, queued right after `enter`, so it's there by the time you paste. |
| Server → LOCAL (hotkey, edge, or anything else that sends the client `rel`) | The **client's** clipboard goes to the server. |
| Anything else (copying while you stay on one PC, a lost link, a reconnect) | Nothing. |

- **Only when it changed.** Each PC compares the Windows clipboard change counter (`GetClipboardSequenceNumber`) with the one it last looked at. Nothing new copied means nothing is sent, so switching back and forth doesn't resend the same text.
- **No echo:** after putting received text on its own clipboard, a PC records the new counter, so the text isn't sent straight back on the next switch.
- **What's on the clipboard when kmshare starts is never sent.** Only copies made after launch are shared, so something copied earlier (a password, say) doesn't leave the PC just because kmshare started.
- **Each new clipboard is looked at once.** If it isn't sent (not text, private, too big), it isn't tried again on later switches; copy it again to retry. The exception is a busy clipboard, which is tried again on the next switch.

### What's sent and what isn't

| Clipboard holds | Result | Log (on the PC giving up control) |
|---|---|---|
| Plain text (any language, emoji) up to `[clipboard] max_kb` | Sent; the other PC replaces its clipboard with it | `clipboard: sent <size> of text to the client` / `… to the server`; the other PC logs `clipboard: got <size> of text from …` |
| Text over `max_kb` (measured as UTF-8) | Not sent | `clipboard: <size> of text is over the <limit> limit, not sent` |
| Received text over **this** PC's `max_kb` | Not applied | `clipboard: received <size>, over this PC's <limit> limit, not applied` (on the receiving PC) |
| An image, files, or anything without a plain-text form | Not sent; the other PC's clipboard is left as it was | `clipboard: not text, not sent` |
| Content marked private by the app that copied it (`ExcludeClipboardContentFromMonitorProcessing` or `Clipboard Viewer Ignore`, set by some desktop password managers, such as KeePass) | Not sent | `clipboard: marked private by the app that copied it (e.g. a password manager), not sent` |
| Text copied from a browser extension, including password managers such as Bitwarden's | **Sent** like any other text: the browser writes plain text with no private marker (Chrome only records the source, `chrome-extension://<id>/…`, which kmshare doesn't check) | `clipboard: sent <size> of text to …` |
| Empty | Nothing | – |

Rich text and HTML copies usually also carry plain text: that plain text is what's sent, without formatting. Text is cut at the first NUL character, as Windows' plain-text format does.

### How it's done

- **Both PCs must agree.** Right after connecting, each side sends `{"t":"caps","clip":true|false}`. Sharing runs only when both say `true`, and each side logs the result: `clipboard sharing on: …`, `clipboard sharing off (turned off on this PC)`, or `… (turned off on the client)` / `… on the server`. A peer that never sends `caps` (an older kmshare) counts as off.
- **Receiving only needs this PC's setting.** With sharing off here, incoming clipboard messages are ignored.
- **Chunks:** text is split into chunks of 8000 characters (at most 48 KB of JSON each, under the 64 KB message limit): `{"t":"clip","id":n,"i":k,"n":N,"d":"…"}`. The receiver puts them back together in order. A missing, repeated or out-of-order chunk drops that text. A 1 MB text is about 130 chunks and shares the connection with input, so mouse movement can pause briefly while it's sent.
- **Its own thread.** All clipboard work runs on a `kmshare-clipboard` thread, never inside the input hooks, so a slow or busy clipboard can't stall the mouse or keyboard.
- **Busy clipboard:** if another app has the clipboard open, kmshare tries 5 times over about 0.1 s, then gives up for this switch: `clipboard: another app had the clipboard open, not sent (tries again on the next switch)`, or `… received text not applied` on the receiving PC.
- **Writing** uses a hidden message-only window as the clipboard owner (Windows needs a window for `SetClipboardData`). The window is destroyed straight after; the text stays on the clipboard.
- **Link lost mid-send:** `clipboard: link lost while sending (<error>)`. The receiver drops the partial text when it disconnects.
- **Encrypted** like all other traffic ([Security](#11-security)). Clipboard text never appears in the logs, only its size.

---

## 8. Server: connections

| Situation | Behaviour |
|---|---|
| Startup | Listens on `bind:port` and logs `server listening on <bind>:<port>, waiting for a client ...` |
| Incoming connection | Runs the handshake ([Security](#11-security)). **One handshake at a time.** Each one has a 5 s limit. |
| Handshake fails (`wrong key`, `not a kmshare client (or a different version)`, `handshake timed out`, `client hung up during handshake`, or a network error) | Logs `rejected <ip:port>: <reason>`, closes the connection, then **waits 1 s before taking the next handshake**. This limits key guessing to about one try per second. |
| Handshake succeeds | Logs `client connected: <ip:port>  (press <hotkey> to control it)`. Stays in its current mode (LOCAL). If edge switching is on, asks which side the client is on (or reuses the answer for that IP) and sends it the layout ([Edge switching](#6-edge-switching)). Sends `caps` with its clipboard setting ([Clipboard sharing](#7-clipboard-sharing)). |
| A second client connects (correct key) while one is connected | The new client wins. If REMOTE, the server first switches to LOCAL (`client replaced`). The old connection is closed and logged as `client <old> disconnected (replaced by <new>)`. |
| Keep-alive | Sends `ping` every `ping_interval` seconds. Each `pong` resets the timer. |
| No `pong` for `peer_timeout` seconds | Drops the client: `no response from client`. Switches to LOCAL if needed. |
| Client closes, network error, a send fails, or a message fails its check | Drops the client with that reason. Switches to LOCAL if needed. Logs `client <ip:port> disconnected (<reason>)`. |
| Events still queued from a previous connection | Discarded. They're never delivered to a newer client. |
| Client disconnects | Its side answer is kept for that IP. Edge switching stays off until a client is connected again. |
| Ctrl+C in the server console (while LOCAL) | Switches to LOCAL if needed, closes the client, removes the hooks, closes the listening port, prints `Stopped.` |

---

## 9. Client

### Connecting

| Situation | Behaviour |
|---|---|
| Startup | Logs `client: connecting to <host>:<port> ...` |
| Server can't be reached (TCP connect has a 5 s limit) | Logs `cannot reach server …, retrying ...` **once per distinct error** (no repeated spam) and retries every **3 s**, forever. |
| Handshake fails (wrong key, wrong program, timeout) | Logs `handshake failed: <reason>` (e.g. `server rejected the key`) and retries every **5 s**, forever. With the wrong key it keeps getting rejected until you restart it with the right key. |
| Connected | Logs `connected to <host>:<port>. Waiting for the server's hotkey to take control` and sends `caps` with its clipboard setting. |
| Disconnected for any reason | **Releases every held key and button**, forgets the layout (a new one comes after reconnecting), logs `disconnected: <reason>`, waits 2 s, logs `reconnecting ...`, and goes back to connecting. |
| No message from the server for `peer_timeout` seconds | Disconnects with `server not responding` (then as above). The server's pings keep this from happening on a healthy link. |
| Ctrl+C in the client console | Releases every held key and button, closes the connection, prints `Stopped.` |

### Replaying input

| Message | Action on the client |
|---|---|
| Mouse move | Reads the current cursor position and moves it by the received amount using `SetCursorPos`. Windows keeps the cursor on the screen. Using `SetCursorPos` places the cursor exactly, with no second layer of acceleration, and apps still see normal mouse-move events (hover and drag work). `SetCursorPos` doesn't count as user input, though, so at most once a second a move also sends a zero-distance `SendInput` move. That resets the Windows idle timer (Teams presence, screen saver, lock) without moving the cursor. Keys, buttons and wheel already use `SendInput` and count as input. |
| Key | Sends the key with the same virtual-key code, scan code and extended-key flag. Remembers it while it's held down. |
| Button | Sends the press or release. Remembers held buttons. |
| Wheel | Sends a vertical or horizontal wheel event with the exact amount received. |
| `enter` | Marks this PC as controlled. With an entry edge: moves the cursor to the entry point and logs `server took control (came in over my <edge> edge)`. Without one (hotkey): leaves the cursor where it is and logs `server took control`. Either way it disarms its own edge until the cursor moves `edge_rearm` pixels away. |
| `layout` | Stores its exit edge and the safeguard values, and logs `the server is on my <SIDE>: …`. From then on, while controlled, every received move is checked against that edge ([Edge switching](#6-edge-switching)). On a push: sends `leave` once and logs `pushed past the <side> edge, handing control back`. |
| `rel` | Marks this PC as not controlled, releases every held key and button. Logs `server released control`. Then offers its clipboard to the server if something new was copied ([Clipboard sharing](#7-clipboard-sharing)). |
| `caps` | Stores whether the server shares the clipboard, and logs whether sharing is on. |
| `clip` | A clipboard chunk; the last one puts the text on this PC's clipboard. |
| `ping` | Replies `pong`. |
| Malformed message (missing or wrong field) | Logs `ignored malformed message …` and continues. |
| Unknown message type | Ignored without a log line (leaves room for later versions). |

- Events kmshare sends on the client are tagged `KMSH` (`0x4B4D5348` in the event's `dwExtraInfo` field), so they can be recognised.
- The client's own keyboard and mouse **keep working** the whole time. Both sets of input can be used at once.

---

## 10. Failure and safety behaviour (all cases)

| Event | Server | Client |
|---|---|---|
| You press the hotkey while REMOTE | → LOCAL; the cursor is still where it was frozen | Releases everything held |
| Wifi drops or the cable is pulled | → LOCAL within `peer_timeout` (6 s) | Releases everything held within `peer_timeout`, then reconnects every 3 s |
| Client app closed with Ctrl+C | → LOCAL as soon as the connection closes | Releases everything before quitting |
| Client window closed with **X** or the process is killed | → LOCAL as soon as the connection closes, or at worst after `peer_timeout` | Process ends. Windows ends injected key presses when the process dies, but this hasn't been verified for every app (untested). |
| Client PC sleeps or freezes | → LOCAL after `peer_timeout` | After waking: detects silence, releases everything, reconnects |
| Server app closed with Ctrl+C | Already LOCAL (Ctrl+C only reaches the server while LOCAL) | Connection closes → releases everything, reconnects |
| Server window closed with **X** or the process is killed while REMOTE | Hooks disappear with the process → **local input returns immediately**. The cursor is where it was frozen. | Connection closes, or goes silent until `peer_timeout` → releases everything, reconnects |
| Server PC sleeps, locks or freezes | Hooks don't see secure-desktop input. Mode is kept. | Silence → releases everything after `peer_timeout` |
| An **admin window** (for example the HoYoPlay launcher) has focus and kmshare isn't running as administrator | Windows hides its input from the hooks: **edge switching and the hotkey don't work and the cursor stays on the server**. If it happens while REMOTE, input can go to that window. Logs `focused window … runs as administrator: …`; clicking a normal window restores input and logs `admin window lost focus: …`. Fix: run kmshare as administrator (steps in README, *Security notes & limitations*). | The cursor still moves over it, but clicks and keys don't reach it. Logs the same warning with the client wording. Fix: run the client as administrator. |
| A message fails its check (tampering, replay, reordering, wrong key mid-session, frame > 64 KB) | Drops the client → LOCAL | Disconnects → releases everything, reconnects |
| A hook throws an error | Logs `keyboard hook error` / `mouse hook error`. **That event goes to the server.** The hooks keep running. | – |
| A new client with the correct key connects | Old client replaced; → LOCAL first if REMOTE | Old client: disconnected → releases everything, reconnects (and becomes the current client again, replacing the newer one) |
| A guessing or wrong-key connection | Rejected; next handshake only after 1 s | – |
| Hotkey with no client connected | Stays LOCAL, error sound | – |
| Hotkey held down | Switches once (auto-repeat swallowed) | – |
| Cursor pushed into a screen corner, or along the edge with a button held | No switch | No switch back |
| You press the hotkey just as the client sends `leave` | Hotkey wins; the late `leave` is ignored | Released by `rel` as usual |
| Another app holds the clipboard open at a switch | That clipboard isn't sent or applied this time; logged; input isn't delayed | Same |
| Link drops while clipboard chunks are in flight | Partial text discarded; nothing half-written to the clipboard | Same |
| Client disconnects while the side question is open | The answer is still remembered for that IP; edge switching stays off until it reconnects | Reconnects; no question the second time |
| Wifi drops while the cursor is on the client after an edge crossing | → LOCAL within `peer_timeout`, cursor back where it crossed | Releases everything |
| You're still moving the mouse at the moment control returns over the edge | Moves computed from the frozen spot are dropped (race guard); the cursor stays at the return point | – |
| Monitor plugged in/out or resolution changed | New layout used within 2 s | New layout used within 2 s |

---

## 11. Security

**What's protected:** everything sent between the two PCs (every key, so passwords too, and clipboard text) is encrypted and authenticated. Input from anyone who doesn't have the key is never accepted.

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

## 12. Messages on the wire

Each message is compact JSON, inside the encryption.

| Direction | Message | Meaning |
|---|---|---|
| S → C | `{"t":"m","x":dx,"y":dy}` | Relative mouse move (several may be combined into one) |
| S → C | `{"t":"k","v":vk,"s":scan,"e":ext,"d":down}` | Key down or up |
| S → C | `{"t":"b","b":"left\|right\|middle\|x1\|x2","d":down}` | Mouse button |
| S → C | `{"t":"w","d":delta,"h":horizontal}` | Wheel; one notch = 120 |
| S → C | `{"t":"enter"}` | Server switched to REMOTE (hotkey) |
| S → C | `{"t":"enter","edge":"left","f":0.42}` | Server switched to REMOTE over an edge: the client's entry edge and the position along it (0–1, across the whole desktop) |
| S → C | `{"t":"layout","side":"left","push":8,"delay":0,"corner":40,"rearm":20}` | The client's exit edge and the edge-switching safeguards |
| S → C | `{"t":"rel"}` | Release everything (server switched to LOCAL) |
| S → C | `{"t":"ping"}` | Keep-alive |
| C → S | `{"t":"pong"}` | Keep-alive reply |
| both | `{"t":"caps","clip":true}` | Sent once after connecting: is clipboard sharing on here? |
| both | `{"t":"clip","id":3,"i":0,"n":2,"d":"…"}` | Clipboard text, chunk `i` of `n` (8000 characters each) |
| C → S | `{"t":"leave","f":0.42}` | The client was pushed past its exit edge at this position (0–1, across the client's whole desktop); hand control back |

---

## 13. Log messages

Timestamps are `HH:MM:SS`. `-v` adds debug output.

| Role | Message | Meaning |
|---|---|---|
| S | `server listening on <bind>:<port>, waiting for a client ...` | Started |
| S | `client connected: <ip:port>  (press <hotkey> to control it)` | Client passed the handshake |
| S | `rejected <ip:port>: <reason>` | Handshake failed (see [Connections](#8-server-connections)) |
| S | `Client <ip:port> connected. Where is it, seen from this PC?` | Side question (see [Edge switching](#6-edge-switching)) |
| S | `client <ip:port> is to the <SIDE>: push the cursor past this PC's <side> edge to switch (or <hotkey>)` | Edge switching active for this client |
| S | `-> REMOTE (hotkey)…` / `-> REMOTE (<side> edge)…` | Switched to REMOTE |
| S | `<- LOCAL (<reason>)` | Switched to LOCAL |
| S | `no client connected, staying LOCAL` | Hotkey pressed with no client |
| S | `client <ip:port> disconnected (<reason>)` | Client gone |
| S | `keyboard hook error` / `mouse hook error` + traceback | Bug in a hook; that event went to the server |
| S | `server cursor slipped <n> px while controlling the client (…); corrected` | The frozen cursor moved while REMOTE; moves are measured from the new spot (see [Server modes](#5-server-local-and-remote-modes)). Once per session. |
| S, C | `focused window "<title>" (<exe>) runs as administrator: …` | An admin window got focus and kmshare isn't admin (see the failure table). Logged once per window. |
| S, C | `focused window "<title>" may run as administrator (Windows won't say): …` | Windows refused to say whether the focused window is elevated (protected or anti-cheat processes); treated the same way |
| S, C | `admin window lost focus: …` | Focus moved back to a normal window; input works again |
| S, C | `running as administrator: admin windows can be controlled too` | kmshare is elevated, so there's no admin-window check |
| C | `client: connecting to <host>:<port> ...` | Started |
| C | `cannot reach server <host>:<port> (<error>), retrying ...` | Network or firewall problem |
| C | `handshake failed: server rejected the key` | Keys differ |
| C | `handshake failed: server failed the key check (different key?)` | The server couldn't prove it has the key (fake server, or a bug) |
| C | `connected to <host>:<port>. Waiting for the server's hotkey to take control` | Ready |
| C | `server took control` / `server took control (came in over my <edge> edge)` / `server released control` | Mode changes on the server |
| C | `the server is on my <SIDE>: push past my <side> edge to go back` | Layout received |
| C | `pushed past the <side> edge, handing control back` | Sent `leave` |
| C | `disconnected: <reason>` then `reconnecting ...` | Link lost; everything held was released |
| C | `ignored malformed message …` | Bad message (skipped) |
| S, C | `clipboard sharing on: …` / `clipboard sharing off (turned off on …)` | Result of the `caps` exchange after connecting |
| S, C | `clipboard: sent <size> of text to …` / `clipboard: got <size> of text from …` | Clipboard text moved |
| S, C | `clipboard: not text, not sent` / `… marked private …, not sent` / `… over the <limit> limit, not sent` | New clipboard content that isn't shared ([Clipboard sharing](#7-clipboard-sharing)) |
| S, C | `clipboard: another app had the clipboard open, …` / `clipboard: link lost while sending (…)` | Clipboard skipped this time |

---

## 14. Known gaps

- **Secure desktop:** lock screen, UAC prompts and Ctrl+Alt+Del can't be controlled on the client, and the server's hooks can't see them.
- **Shortcuts Windows reserves on the server:** Ctrl+Alt+Del always acts on the server. Win+L locks the server; the hook can't stop it (untested).
- **Elevated windows:** if kmshare isn't running as administrator:
  - On the server, input to a focused admin window bypasses the hooks, so it isn't sent to the client and edge switching can't see the cursor (it stays trapped until you click a normal window).
  - On the client, kmshare can't send input into admin windows.
  - kmshare checks the focused window twice a second and logs a warning when this applies; it can't work around it without running as administrator.
- **Keyboard layout:** keys are sent as virtual-key codes, so both PCs should use the same layout.
- **Precision-touchpad gestures** (two-finger scroll, three-finger swipes) may still act on the server (untested).
- **Clipboard:** plain text only (no images, files or formatting), and only when control switches, not on every copy. Passwords copied from browser-extension password managers (Bitwarden's, for example) aren't marked private, so they are sent.
- **Not implemented yet:** dragging things across PCs, more than one client at a time, operating systems other than Windows.
- **The side answer can't be changed while the server runs.** Restart the server to answer again.
- **The server may go idle while REMOTE** (untested): its own input is blocked before Windows handles it, so Teams presence, the screen saver or the lock may start on the server during long sessions on the client.
- **While REMOTE, the server's cursor stays visible** where it was frozen (it isn't hidden).
- **Ctrl+C while REMOTE** goes to the client, not to kmshare. Switch back to LOCAL first to quit the server.

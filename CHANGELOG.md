# Changelog

## Unreleased

- **Fixed:** switching back and forth quickly could trap the cursor on the client: it was pushed to the client's left edge and only moved vertically. If the server's frozen cursor moved even once while the client was in control, every later move carried that offset. Moves are now measured from the server cursor's real position, and the server logs `server cursor slipped <n> px …` when it happens.
- **Admin-window warning:** when kmshare isn't running as administrator and an admin window (for example the HoYoPlay launcher) gets focus, kmshare logs why input stops working there: on the server the cursor stays trapped and the hotkey and edge switching don't respond; on the client clicks and keys don't reach that window. It logs again when focus moves back. The README and BEHAVIOR.md explain how to run kmshare as administrator.
- **Fixed:** the client PC went idle (Teams showed Away, the screen saver or lock could start) while you only moved the mouse on it. kmshare places the cursor with `SetCursorPos`, which Windows doesn't count as user input. Cursor moves now also send a zero-distance input event, at most once a second, which resets the idle timer without moving the cursor.

## 0.2.0 (2026-11-25)

- **Edge switching:** push the cursor past the server's screen edge to move onto the client, and past the client's opposite edge to come back. When a client connects, the server asks which side it sits on (left, right, top or bottom), once per client IP. The cursor appears at the same relative position along the edge.
  - Safeguards: a push threshold, an optional delay, corner dead zones, a re-arm distance (no bouncing back), no switching while a mouse button is held, and only outer desktop edges count (multi-monitor aware). Values are set in `[layout]` and sent from the server to the client.
  - On/off with `[features] edge_switch` and a launch question. The hotkey keeps working either way.
- **Server cursor frozen instead of parked:** while the client is controlled, the server's cursor stays where it was instead of jumping to the centre of the main screen.
- **Fixed:** after control came back over the edge, the server cursor sometimes landed in the middle of the screen. A mouse move Windows had already computed from the parked position slipped through and overrode the placement. Such moves are now dropped for a moment after returning.
- **Fixed:** with monitors of different sizes, pushing straight back over the edge returned to a different height (or the other monitor) on left, top and bottom layouts. Crossing positions are now measured across the whole desktop at both ends.
- Running `python -m kmshare` with no arguments asks for the role and then each setting that role uses, one at a time. Enter keeps the default from `kmshare.ini`, invalid answers are asked again, and answers are not saved. With any argument, the settings come from the ini and the arguments as before.

## 0.1.0 (2026-11-25)

First MVP.

- One command for both roles (`python -m kmshare`), which asks whether this PC is the server or the client.
- `BEHAVIOR.md` documents every behaviour, including all failure and safety cases.
- The shared key is typed at launch and never stored. The handshake authenticates both sides, and all traffic is encrypted with AES-256-GCM.
- Port, server address, hotkey and keep-alive are set in `kmshare.ini`.
- **Ctrl+Shift+F12** switches control between the server and the client (configurable).
- Failsafes: if the link drops, the server switches back to local control and the client releases any held keys and buttons.

# Changelog

## Unreleased

- Running `python -m kmshare` with no arguments asks for the role and then each setting that role uses, one at a time. Enter keeps the default from `kmshare.ini`, invalid answers are asked again, and answers are not saved. With any argument, the settings come from the ini and the arguments as before.

## 0.1.0 (2026-11-25)

First MVP.

- One command for both roles (`python -m kmshare`), which asks whether this PC is the server or the client.
- `BEHAVIOR.md` documents every behaviour, including all failure and safety cases.
- The shared key is typed at launch and never stored. The handshake authenticates both sides, and all traffic is encrypted with AES-256-GCM.
- Port, server address, hotkey and keep-alive are set in `kmshare.ini`.
- **Ctrl+Shift+F12** switches control between the server and the client (configurable).
- Failsafes: if the link drops, the server switches back to local control and the client releases any held keys and buttons.

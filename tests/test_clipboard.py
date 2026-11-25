"""Clipboard sharing: chunks, when text is (not) sent, and the real Windows clipboard."""

import logging

import pytest

from conftest import FakeClipboard
from kmshare import clipboard
from kmshare.clipboard import CHUNK_CHARS, Assembler, ClipboardSync, split


# ---------------------------------------------------------------- chunks
@pytest.mark.parametrize("text", ["", "hi", "x" * CHUNK_CHARS, "é" * (CHUNK_CHARS * 2 + 5), "😀\x00\n" * 9000],
                         ids=["empty", "short", "one-full-chunk", "accents-3-chunks", "emoji-nul-4-chunks"])
def test_split_and_reassemble(text):
    chunks = split(text, 7)
    assert all(c["n"] == len(chunks) and c["id"] == 7 for c in chunks)
    asm, out = Assembler(), None
    for c in chunks:
        out = asm.feed(c)
    assert out == text


def test_chunks_fit_the_frame_limit():
    import json

    from kmshare.channel import MAX_FRAME

    worst = "\x01" * CHUNK_CHARS  # escaped as \u0001: 6 bytes each
    assert len(json.dumps(split(worst, 1)[0], separators=(",", ":")).encode()) + 16 < MAX_FRAME


def test_missing_chunk_drops_the_text():
    a, b, c = split("x" * (CHUNK_CHARS * 2 + 1), 1)
    asm = Assembler()
    assert asm.feed(a) is None and asm.feed(c) is None   # b missing
    assert asm.feed({"t": "clip", "id": 1, "i": 2, "n": 3, "d": "x"}) is None
    a2, b2 = split("y" * (CHUNK_CHARS + 1), 2)
    assert asm.feed(a2) is None and asm.feed(b2) == "y" * (CHUNK_CHARS + 1)  # next text still works
    assert asm.feed({"t": "clip"}) is None               # malformed: ignored


# ---------------------------------------------------------------- sync
def make(enabled=True, peer=True, max_bytes=1024):
    sent = []
    s = ClipboardSync(enabled, max_bytes, "the client", io=FakeClipboard())
    s.connected(sent.append)
    s.on_caps({"t": "caps", "clip": peer})
    return s, s._io, sent


def offer(s):
    s.offer()
    assert s.drain()


def test_only_new_copies_are_sent_and_only_once(caplog):
    caplog.set_level(logging.INFO, "kmshare.clipboard")
    s, io, sent = make()
    offer(s)
    assert sent == []                                   # clipboard at launch is never sent
    io.copy("hello")
    offer(s)
    assert [c["d"] for c in sent] == ["hello"]
    offer(s)
    assert len(sent) == 1                               # unchanged since: not sent again
    assert "clipboard: sent 5 B of text to the client" in caplog.messages


def test_received_text_is_applied_and_not_echoed_back():
    s, io, sent = make()
    for c in split("from the other PC", 4):
        s.receive(c)
    assert s.drain() and io.writes == ["from the other PC"]
    offer(s)
    assert sent == []                                   # it came from the peer: don't send it back


@pytest.mark.parametrize("kind,log_text", [
    ("private", "marked private by the app that copied it"),
    ("not_text", "not text, not sent"),
])
def test_private_or_non_text_is_not_sent(caplog, kind, log_text):
    caplog.set_level(logging.INFO, "kmshare.clipboard")
    s, io, sent = make()
    io.copy(None, kind)
    offer(s)
    assert sent == [] and any(log_text in m for m in caplog.messages)


def test_over_the_limit_is_not_sent(caplog):
    s, io, sent = make(max_bytes=10)
    io.copy("é" * 6)                                    # 12 bytes in UTF-8
    offer(s)
    assert sent == [] and "over the 10 B limit" in caplog.text


def test_busy_clipboard_is_retried_on_the_next_switch():
    s, io, sent = make()
    io.copy("later")
    io.busy = True
    offer(s)
    assert sent == []
    io.busy = False
    offer(s)
    assert [c["d"] for c in sent] == ["later"]


@pytest.mark.parametrize("enabled,peer", [(False, True), (True, False)])
def test_needs_both_pcs_on(enabled, peer):
    s, io, sent = make(enabled=enabled, peer=peer)
    io.copy("secret")
    offer(s)
    for c in split("incoming", 1):
        s.receive(c)
    assert s.drain()
    assert sent == []
    assert io.writes == ([] if not enabled else ["incoming"])  # receiving only needs this PC on


def test_disconnected_sends_nothing():
    s, io, sent = make()
    s.disconnected()
    io.copy("x")
    offer(s)
    assert sent == [] and not s.active


def test_link_lost_mid_send_is_logged_not_raised(caplog):
    s, io, _ = make()

    def broken(msg):
        raise ConnectionResetError("gone")

    s.connected(broken)
    s.on_caps({"t": "caps", "clip": True})
    io.copy("x")
    offer(s)
    assert "link lost while sending" in caplog.text


# ---------------------------------------------------------------- real clipboard
def test_real_windows_clipboard_round_trip(monkeypatch):
    """Writes the real clipboard, then puts the previous text back (skipped if it held non-text)."""
    monkeypatch.undo()                                  # the real WinClipboard, not the fake
    io = clipboard.WinClipboard()
    before, kind = io.read()
    was_empty = kind != "ok" and clipboard.user32.CountClipboardFormats() == 0
    if kind != "ok" and not was_empty:
        pytest.skip(f"clipboard holds {kind} content; not overwriting it")
    try:
        seq = io.sequence()
        io.write("kmshare test ✓ 😀")
        assert io.read() == ("kmshare test ✓ 😀", "ok") and io.sequence() != seq
    finally:
        if was_empty:
            clipboard._open(None)
            clipboard.user32.EmptyClipboard()
            clipboard.user32.CloseClipboard()
        else:
            io.write(before)
    assert io.read() == ((None, "not_text") if was_empty else (before, "ok"))


def test_size_wording():
    assert (clipboard._size(42), clipboard._size(2048), clipboard._size(1024 * 1024)) == ("42 B", "2.0 KB", "1.0 MB")

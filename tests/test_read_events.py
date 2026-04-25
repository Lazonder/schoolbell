"""
Tests for read_events() — the JSONL tail reader used by the Logs page.

This one isn't 'pure' (it touches the filesystem) but the I/O is small
enough to run in-test against a temp file. We patch the module-level
EVENTS_LOG_PATH so the function reads our fixture instead of the real
events log.

The interesting case is the line-boundary off-by-one: read_events()
seeks to size - max_bytes from the end. If that landing byte happens
to be exactly on a newline, the chunk starts at a complete line and
must NOT be dropped. The previous version always dropped it.
"""

import json

import webinterface


def _write_events(path, records):
    """Write a list of dicts as one JSONL line each."""
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_read_events_returns_all_when_under_max_bytes(tmp_path, monkeypatch):
    # Small file — start = 0, no boundary handling involved.
    p = tmp_path / "events.jsonl"
    records = [{"ts": f"t{i}", "type": "ui", "data": {"i": i}} for i in range(5)]
    _write_events(p, records)
    monkeypatch.setattr(webinterface, "EVENTS_LOG_PATH", str(p))

    out = webinterface.read_events(limit=100, max_bytes=10_000)

    assert out == records


def test_read_events_keeps_first_line_when_window_lands_on_newline(tmp_path, monkeypatch):
    # The regression case: pick max_bytes so that start - 1 is a '\n'.
    # We craft 3 lines of fixed length and choose max_bytes = 2 * line_len,
    # which makes start = line_len, and byte at start-1 is the newline at
    # the end of line 0. Line 1 is therefore complete and must be returned.
    p = tmp_path / "events.jsonl"
    records = [
        {"ts": "t0", "type": "ui", "data": {"i": 0}},
        {"ts": "t1", "type": "ui", "data": {"i": 1}},
        {"ts": "t2", "type": "ui", "data": {"i": 2}},
    ]
    # All records serialize to the same length here (json.dumps is
    # deterministic for these inputs), so the math works out.
    line_len = len(json.dumps(records[0])) + 1  # +1 for "\n"
    _write_events(p, records)
    monkeypatch.setattr(webinterface, "EVENTS_LOG_PATH", str(p))

    out = webinterface.read_events(limit=100, max_bytes=2 * line_len)

    # Old buggy behavior would drop record 1 ("first line of the chunk").
    # Correct behavior: record 1 is complete (start lands on newline)
    # and is kept; record 0 is outside the window.
    assert out == [records[1], records[2]]


def test_read_events_drops_partial_first_line(tmp_path, monkeypatch):
    # If start lands mid-line, the first line is partial JSON and must
    # be dropped — both because parsing it would fail and because the
    # caller doesn't want garbage records.
    p = tmp_path / "events.jsonl"
    records = [
        {"ts": "t0", "type": "ui", "data": {"i": 0}},
        {"ts": "t1", "type": "ui", "data": {"i": 1}},
    ]
    _write_events(p, records)
    monkeypatch.setattr(webinterface, "EVENTS_LOG_PATH", str(p))

    # Pick a window that lands somewhere inside record 0, so its tail
    # leaks into the chunk — must be dropped, leaving only record 1.
    line_len = len(json.dumps(records[0])) + 1
    out = webinterface.read_events(limit=100, max_bytes=line_len + 5)

    assert out == [records[1]]


def test_read_events_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(webinterface, "EVENTS_LOG_PATH", str(tmp_path / "nope.jsonl"))
    assert webinterface.read_events() == []

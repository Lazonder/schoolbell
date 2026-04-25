"""
Tests for locked_json() — the read-modify-write context manager that
serializes concurrent writers on a JSON state file.

The interesting case is concurrency: two threads each load the file,
each append a record, and each save. Without locking, the second
saver overwrites the first's changes (lost update). With locking,
both updates land — the second writer reads what the first one just
saved, then appends on top of that.

We use threading because Gunicorn's default deployment for this app
is workers × threads, and Python threads (despite the GIL) are
exactly the case that breaks unlocked read-modify-write: I/O releases
the GIL and the second thread wakes up after the first's load but
before the first's save.
"""

import threading

import webinterface


def test_locked_json_basic_roundtrip(tmp_path):
    p = str(tmp_path / "state.json")

    with webinterface.locked_json(p, {"items": []}) as (data, save):
        data["items"].append("a")
        save(data)

    # Re-read to confirm it was persisted.
    with webinterface.locked_json(p, {"items": []}) as (data, save):
        assert data == {"items": ["a"]}


def test_locked_json_skip_save_does_not_persist(tmp_path):
    p = str(tmp_path / "state.json")

    # First writer commits a baseline.
    with webinterface.locked_json(p, {"items": []}) as (data, save):
        data["items"].append("a")
        save(data)

    # Second writer mutates but doesn't call save() — file should be
    # unchanged. This mirrors a route that flashes a validation error
    # and returns without saving.
    with webinterface.locked_json(p, {"items": []}) as (data, save):
        data["items"].append("DROPME")
        # no save(data)

    with webinterface.locked_json(p, {"items": []}) as (data, save):
        assert data == {"items": ["a"]}


def test_locked_json_serializes_concurrent_writers(tmp_path):
    """The race we're guarding against: two threads each append one
    item. Without a lock, one append is lost. With a lock, both land."""
    p = str(tmp_path / "state.json")

    # Seed the file with an empty list.
    with webinterface.locked_json(p, {"items": []}) as (data, save):
        save(data)

    n_threads = 20
    barrier = threading.Barrier(n_threads)

    def worker(i):
        # All threads line up at the barrier so they hit the lock at
        # roughly the same time — that's the worst case for races.
        barrier.wait()
        with webinterface.locked_json(p, {"items": []}) as (data, save):
            data["items"].append(i)
            save(data)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with webinterface.locked_json(p, {"items": []}) as (data, save):
        # Order isn't guaranteed (which thread got the lock first?),
        # but every value 0..n-1 must appear exactly once. If the lock
        # didn't work, len would be < n_threads.
        assert sorted(data["items"]) == list(range(n_threads))


def test_locked_json_lock_released_on_exception(tmp_path):
    """If the with-body raises, the lock must still be released so
    that subsequent writers can proceed. Without `finally:` releasing
    it, this test would hang."""
    p = str(tmp_path / "state.json")

    try:
        with webinterface.locked_json(p, {"items": []}) as (data, save):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    # If the lock leaked, this acquire would deadlock. pytest's
    # default timeout would catch it, but we'll just trust that
    # this returning at all means the lock was released.
    with webinterface.locked_json(p, {"items": []}) as (data, save):
        data["items"].append("ok")
        save(data)

    with webinterface.locked_json(p, {"items": []}) as (data, save):
        assert data == {"items": ["ok"]}

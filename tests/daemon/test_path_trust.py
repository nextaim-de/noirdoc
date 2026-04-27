"""NDS-028: daemon must reject input/output paths owned by another UID."""

from __future__ import annotations

import os

import pytest

from noirdoc.daemon import server

pytestmark = pytest.mark.asyncio


async def test_redact_rejects_input_owned_by_another_uid(monkeypatch, tmp_path):
    """A peer that asks the daemon to read a file not owned by its UID is refused."""
    target = tmp_path / "victim.txt"
    target.write_bytes(b"some content")

    # Pretend our UID is something other than the file's owner.
    monkeypatch.setattr(os, "getuid", lambda: os.stat(target).st_uid + 1)

    state = server.DaemonState()
    params = {
        "input": {"type": "file", "path": str(target)},
        "output_path": str(tmp_path / "out.txt"),
    }
    with pytest.raises(ValueError, match="not owned by the current user"):
        await server.handle_redact(state, params)


async def test_redact_rejects_missing_input(tmp_path):
    state = server.DaemonState()
    params = {
        "input": {"type": "file", "path": str(tmp_path / "nonexistent")},
    }
    with pytest.raises(ValueError, match="not found"):
        await server.handle_redact(state, params)


async def test_redact_rejects_output_parent_owned_by_another_uid(monkeypatch, tmp_path):
    """Refuse to write into a directory owned by a different UID."""
    src = tmp_path / "in.txt"
    src.write_bytes(b"content")
    bad_dir = tmp_path / "owned-by-someone-else"
    bad_dir.mkdir()

    real_stat = os.stat
    real_uid = os.getuid()

    def fake_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path).endswith("owned-by-someone-else"):
            return os.stat_result(
                (
                    st.st_mode,
                    st.st_ino,
                    st.st_dev,
                    st.st_nlink,
                    real_uid + 1,  # foreign uid
                    st.st_gid,
                    st.st_size,
                    st.st_atime,
                    st.st_mtime,
                    st.st_ctime,
                ),
            )
        return st

    monkeypatch.setattr(server.os, "stat", fake_stat)

    state = server.DaemonState()
    params = {
        "input": {"type": "file", "path": str(src)},
        "output_path": str(bad_dir / "result.txt"),
    }
    with pytest.raises(ValueError, match="not owned by the current user"):
        await server.handle_redact(state, params)

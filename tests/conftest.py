"""Shared test setup. Right now it does exactly one thing, and it matters.

WHY THIS FILE EXISTS
--------------------
`AuditLogger` writes a real JSONL file for every transaction, into `exports/`.
That is correct in the app — the audit trail is a deliverable, and it has to
survive a crash mid-demo. It is wrong in a test run.

Without the fixture below, every test that builds a logger, and every test that
drives the app through Streamlit's `AppTest` harness, drops a real file into the
project's `exports/` directory. That had already produced 1,335 of them. Worse,
`new_transaction_id()` picks four random digits, so with a directory that full a
run would occasionally reuse an id and APPEND into an older run's file — leaving
one `TXN-*.jsonl` holding two different orders.

So the tests get their own throwaway directory, one per test. The suite exercises
the real write path, and the repository stays clean.

HOW IT WORKS
------------
`AuditLogger.__init__` reads `audit.EXPORT_DIR` at the moment it is constructed
(`self.export_dir = export_dir or EXPORT_DIR`), not at import. Repointing the
module attribute therefore catches every logger built after the fixture runs —
including the ones `app.py` builds deep inside an `AppTest` run, which no test
could reach to pass an argument to.
"""

from __future__ import annotations

import pytest

from agent import audit as audit_module


@pytest.fixture(autouse=True)
def _isolated_exports(tmp_path, monkeypatch):
    """Point the audit log at a per-test temporary directory.

    Autouse, because the tests most in need of it are the ones that never
    mention the audit logger at all — they drive the whole app and the logger is
    created several layers down.
    """
    monkeypatch.setattr(audit_module, "EXPORT_DIR", tmp_path / "exports")

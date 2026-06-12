"""Unit tests for the ai_audit_state content-hash index (app/store/aggregate.py).

Covers the duplicate-detection-on-cache-hit fix: update() persists a contentHash on each
per-attachment entry, get_content_hash() reads it back, and find_by_content_hash() resolves
same-hash siblings while excluding the queried id and isolating tenants. Uses mongomock.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture()
def state_col():
    """In-memory mongomock collection standing in for ai_audit_state."""
    try:
        import mongomock
    except ImportError:
        pytest.skip("mongomock not installed")
    client = mongomock.MongoClient()
    return client["ready2go_test"]["ai_audit_state"]


@pytest.fixture()
def aggregate(state_col):
    """app.store.aggregate wired to the in-memory collection."""
    with patch("app.store.aggregate.get_state_col", return_value=state_col):
        from app.store import aggregate as agg
        yield agg


def _add(agg, tenant, att_id, *, content_hash, file_name="f.pdf", plan_id="plan_a",
         category="coop", status="Compliant", score=80):
    agg.update(
        tenant,
        attachment_id=att_id,
        category=category,
        status=status,
        score=score,
        file_name=file_name,
        plan_id=plan_id,
        summary="summary text",
        content_hash=content_hash,
    )


class TestContentHashIndex:
    def test_update_persists_content_hash(self, aggregate):
        _add(aggregate, "sub_t1", "att_1", content_hash="hashA")
        assert aggregate.get_content_hash("sub_t1", "att_1") == "hashA"

    def test_get_content_hash_unknown_returns_none(self, aggregate):
        assert aggregate.get_content_hash("sub_t1", "missing") is None

    def test_find_by_content_hash_returns_siblings_excluding_self(self, aggregate):
        _add(aggregate, "sub_t1", "att_A", content_hash="dup", file_name="a.pdf", plan_id="p1")
        _add(aggregate, "sub_t1", "att_B", content_hash="dup", file_name="b.pdf", plan_id="p2")
        _add(aggregate, "sub_t1", "att_C", content_hash="other", file_name="c.pdf")

        siblings = aggregate.find_by_content_hash("sub_t1", "dup", exclude_attachment_id="att_A")
        ids = {s["attachmentId"] for s in siblings}
        assert ids == {"att_B"}                 # att_A excluded, att_C different hash
        assert siblings[0]["fileName"] == "b.pdf"
        assert siblings[0]["planId"] == "p2"

    def test_find_by_content_hash_three_copies_cross_reference(self, aggregate):
        for att in ("att_A", "att_B", "att_C"):
            _add(aggregate, "sub_t1", att, content_hash="trip")
        for att in ("att_A", "att_B", "att_C"):
            ids = {s["attachmentId"]
                   for s in aggregate.find_by_content_hash("sub_t1", "trip", exclude_attachment_id=att)}
            assert ids == {"att_A", "att_B", "att_C"} - {att}

    def test_tenant_isolation(self, aggregate):
        _add(aggregate, "sub_t1", "att_A", content_hash="dup")
        _add(aggregate, "sub_t2", "att_B", content_hash="dup")  # same bytes, other tenant
        assert aggregate.find_by_content_hash("sub_t1", "dup", exclude_attachment_id="att_A") == []

    def test_empty_hash_returns_no_matches(self, aggregate):
        _add(aggregate, "sub_t1", "att_A", content_hash="")
        assert aggregate.find_by_content_hash("sub_t1", "", exclude_attachment_id="zzz") == []

    def test_remove_drops_from_index(self, aggregate):
        _add(aggregate, "sub_t1", "att_A", content_hash="dup")
        _add(aggregate, "sub_t1", "att_B", content_hash="dup")
        aggregate.remove("sub_t1", "att_B")
        assert aggregate.find_by_content_hash("sub_t1", "dup", exclude_attachment_id="att_A") == []

    def test_reanalyze_replaces_not_duplicates(self, aggregate):
        _add(aggregate, "sub_t1", "att_A", content_hash="h1", score=50)
        _add(aggregate, "sub_t1", "att_A", content_hash="h2", score=90)  # same id, re-analyzed
        assert aggregate.get_content_hash("sub_t1", "att_A") == "h2"
        state = aggregate.read("sub_t1")
        assert state["scoreCount"] == 1                 # one entry, not two
        assert state["all_analyzed"][0]["score"] == 90


class TestAuditReadStillWorks:
    """The derived audit aggregates must be unaffected by the added contentHash field."""

    def test_read_derives_counts_and_integrity(self, aggregate):
        _add(aggregate, "sub_t1", "att_A", content_hash="h1", category="coop", status="Compliant", score=80)
        _add(aggregate, "sub_t1", "att_B", content_hash="h2", category="bcp",
             status="Non-Compliant", score=20)
        state = aggregate.read("sub_t1")
        assert state["counts"]["coop"] == 1
        assert state["counts"]["bcp"] == 1
        assert state["integrity"]["compliant"] == 1
        assert state["integrity"]["nonCompliant"] == 1
        assert state["scoreSum"] == 100
        assert state["scoreCount"] == 2

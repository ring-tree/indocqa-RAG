"""控制面 SQLite：迁移、仓储与 Outbox 测试。"""

import sqlite3

import pytest

from app.control.db import SCHEMA_VERSION, ControlPlane


@pytest.fixture()
def plane(tmp_path):
    db = ControlPlane(tmp_path / "control.db")
    yield db
    db.close()


def test_migration_is_idempotent_and_sets_version(tmp_path):
    path = tmp_path / "control.db"
    first = ControlPlane(path)
    version = first.connection.execute("PRAGMA user_version").fetchone()[0]
    first.close()
    assert version == SCHEMA_VERSION

    second = ControlPlane(path)
    assert second.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert second.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    second.close()


def test_build_activation_moves_previous_pointer(plane):
    kb = plane.create_kb("测试库", kb_id="kb_test")
    first = plane.create_build(kb, pipeline_version="1")
    second = plane.create_build(kb, pipeline_version="1")

    plane.activate_build(kb, first)
    assert plane.get_kb(kb)["active_build_id"] == first

    plane.activate_build(kb, second)
    info = plane.get_kb(kb)
    assert info["active_build_id"] == second
    assert info["previous_build_id"] == first
    assert plane.get_build(first)["status"] == "archived"
    assert plane.get_build(second)["status"] == "active"


def test_document_dedup_and_soft_delete(plane):
    kb = plane.create_kb("测试库", kb_id="kb_test")
    doc_id, created = plane.register_document(
        kb, "doc-a", "hash-a", "kb/kb_test/blobs/aa/hash-a", "a.pdf"
    )
    assert created is True
    duplicate_id, created_again = plane.register_document(
        kb, "doc-b", "hash-a", "kb/kb_test/blobs/aa/hash-a", "a-copy.pdf"
    )
    assert duplicate_id == doc_id
    assert created_again is False
    assert len(plane.list_documents(kb)) == 1

    plane.soft_delete_document(doc_id)
    assert plane.list_documents(kb) == []
    assert len(plane.list_documents(kb, include_deleted=True)) == 1


def test_chunks_are_scoped_by_build(plane):
    kb = plane.create_kb("测试库", kb_id="kb_test")
    build_a = plane.create_build(kb)
    build_b = plane.create_build(kb)
    plane.register_document(kb, "doc-a", "hash-a", "key", "a.pdf")

    plane.add_chunks(
        build_a,
        "doc-a",
        [{"chunk_id": "c1", "text": "v1", "kind": "text"}],
        [{"parent_id": "p1", "text": "parent-v1", "section": "s"}],
    )
    plane.add_chunks(
        build_b,
        "doc-a",
        [{"chunk_id": "c1", "text": "v2", "kind": "text"}],
        [{"parent_id": "p1", "text": "parent-v2", "section": "s"}],
    )

    assert plane.get_chunks(build_a, ["c1"])[0]["text"] == "v1"
    assert plane.get_chunks(build_b, ["c1"])[0]["text"] == "v2"
    assert plane.get_parent_chunk(build_a, "p1")["text"] == "parent-v1"
    assert plane.get_parent_chunk(build_b, "p1")["text"] == "parent-v2"


def test_outbox_claim_complete_and_retry(plane):
    kb = plane.create_kb("测试库", kb_id="kb_test")
    build = plane.create_build(kb)

    first = plane.enqueue_index_task(build, "c1", "upsert", {"doc_id": "d1"})
    second = plane.enqueue_index_task(build, "c2", "delete")
    assert plane.pending_task_count(build) == 2

    claimed = plane.claim_index_tasks(limit=1)
    assert len(claimed) == 1
    assert claimed[0]["id"] == first
    assert claimed[0]["status"] == "running"

    remaining = plane.claim_index_tasks(limit=10)
    assert [item["id"] for item in remaining] == [second]

    plane.complete_index_task(first)
    plane.fail_index_task(second, "adapter down", backoff_seconds=60)
    assert plane.pending_task_count(build) == 1
    assert plane.claim_index_tasks(limit=10) == []


def test_folder_path_is_unique(plane):
    kb = plane.create_kb("测试库", kb_id="kb_test")
    plane.create_folder(kb, "设备手册/螺杆空压机")
    with pytest.raises(sqlite3.IntegrityError):
        plane.create_folder(kb, "设备手册/螺杆空压机")


def test_audit_log_roundtrip(plane):
    kb = plane.create_kb("测试库", kb_id="kb_test")
    plane.write_audit("build.activate", "build", "bld_1", kb_id=kb, detail={"ok": True})
    rows = plane.list_audit(kb)
    assert len(rows) == 1
    assert rows[0]["action"] == "build.activate"
    assert rows[0]["detail_json"] == '{"ok": true}'

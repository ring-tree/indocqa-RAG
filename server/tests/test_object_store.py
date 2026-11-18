"""本地对象存储适配器测试。"""

import hashlib

import pytest

from app.adapters.object_store.local import InvalidObjectKey, LocalObjectStore


@pytest.fixture()
def store(tmp_path):
    return LocalObjectStore(tmp_path / "objects")


def test_roundtrip_and_etag(store):
    payload = b"hello object store"
    ref = store.put("kb/kb_1/blobs/ab/abcd", payload, content_type="text/plain")
    assert ref.size == len(payload)
    assert ref.etag == hashlib.sha256(payload).hexdigest()
    assert store.exists("kb/kb_1/blobs/ab/abcd")
    assert store.get("kb/kb_1/blobs/ab/abcd") == payload
    assert store.stat("kb/kb_1/blobs/ab/abcd").size == len(payload)
    assert store.signed_url("kb/kb_1/blobs/ab/abcd", 60) is None

    store.delete("kb/kb_1/blobs/ab/abcd")
    assert not store.exists("kb/kb_1/blobs/ab/abcd")


def test_delete_many(store):
    for index in range(3):
        store.put(f"k/{index}", b"x")
    store.delete_many([f"k/{index}" for index in range(3)])
    assert all(not store.exists(f"k/{index}") for index in range(3))


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/../../b", "", "a\\b"])
def test_rejects_illegal_keys(store, key):
    with pytest.raises(InvalidObjectKey):
        store.put(key, b"x")


def test_health_reports_ok(store):
    assert store.health().status.value == "ok"

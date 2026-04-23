from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet

from noirdoc.mappings.backends import RedisMappingBackend
from noirdoc.mappings.store import MappingStore
from noirdoc.pseudonymization.mapper import PseudonymMapper


@pytest.fixture
def encryption_key():
    return Fernet.generate_key().decode()


@pytest.fixture
def fake_redis():
    import fakeredis

    return fakeredis.FakeAsyncRedis()


@pytest.fixture
def store(fake_redis, encryption_key):
    return MappingStore(RedisMappingBackend(fake_redis), encryption_key)


@pytest.fixture
def mapper_with_data():
    mapper = PseudonymMapper()
    mapper.get_or_create("Max Müller", "PERSON")
    mapper.get_or_create("max@test.de", "EMAIL")
    return mapper


# -- Save + Load --


async def test_save_and_load(store, mapper_with_data):
    request_id = uuid.uuid4()
    await store.save(
        request_id=request_id,
        tenant_id=uuid.uuid4(),
        mapper=mapper_with_data,
    )
    mappings = await store.load(request_id)
    assert mappings is not None
    assert mappings["<<PERSON_1>>"] == "Max Müller"
    assert mappings["<<EMAIL_1>>"] == "max@test.de"


async def test_load_nonexistent(store):
    result = await store.load(uuid.uuid4())
    assert result is None


async def test_empty_mapper_not_saved(store, fake_redis):
    mapper = PseudonymMapper()
    request_id = uuid.uuid4()
    await store.save(request_id=request_id, tenant_id=uuid.uuid4(), mapper=mapper)
    raw = await fake_redis.get(f"mapping:{request_id}")
    assert raw is None


# -- Encryption --


async def test_data_is_encrypted(store, fake_redis, mapper_with_data):
    request_id = uuid.uuid4()
    await store.save(request_id=request_id, tenant_id=uuid.uuid4(), mapper=mapper_with_data)
    raw = await fake_redis.get(f"mapping:{request_id}")
    assert b"Max" not in raw
    assert raw.startswith(b"gAAAAA")  # Fernet token prefix


async def test_wrong_key_fails(fake_redis, mapper_with_data):
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()
    backend = RedisMappingBackend(fake_redis)
    store1 = MappingStore(backend, key1)
    store2 = MappingStore(backend, key2)

    request_id = uuid.uuid4()
    await store1.save(request_id=request_id, tenant_id=uuid.uuid4(), mapper=mapper_with_data)
    result = await store2.load(request_id)
    assert result is None  # Decryption fails gracefully


# -- TTL --


async def test_custom_ttl(store, fake_redis, mapper_with_data):
    request_id = uuid.uuid4()
    await store.save(
        request_id=request_id,
        tenant_id=uuid.uuid4(),
        mapper=mapper_with_data,
        ttl_days=7,
    )
    ttl = await store.get_ttl(request_id)
    assert ttl is not None
    assert ttl <= 7 * 86400
    assert ttl > 6 * 86400


async def test_default_ttl_30_days(store, mapper_with_data):
    request_id = uuid.uuid4()
    await store.save(request_id=request_id, tenant_id=uuid.uuid4(), mapper=mapper_with_data)
    ttl = await store.get_ttl(request_id)
    assert ttl is not None
    assert ttl > 29 * 86400


# -- Delete --


async def test_delete(store, mapper_with_data):
    request_id = uuid.uuid4()
    await store.save(request_id=request_id, tenant_id=uuid.uuid4(), mapper=mapper_with_data)
    assert await store.delete(request_id) is True
    assert await store.load(request_id) is None


async def test_delete_nonexistent(store):
    assert await store.delete(uuid.uuid4()) is False


# -- Load Full --


async def test_load_full(store, mapper_with_data):
    request_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    await store.save(request_id=request_id, tenant_id=tenant_id, mapper=mapper_with_data)
    full = await store.load_full(request_id)
    assert full is not None
    assert full["request_id"] == str(request_id)
    assert full["tenant_id"] == str(tenant_id)
    assert "created_at" in full
    assert "<<PERSON_1>>" in full["mappings"]


# -- Health Check --


async def test_ping(store):
    assert await store.ping() is True


async def test_ttl_nonexistent(store):
    assert await store.get_ttl(uuid.uuid4()) is None

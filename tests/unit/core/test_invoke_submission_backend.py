import importlib
import importlib.util

import pytest


def test_invoke_submission_backend_module_exists() -> None:
    spec = importlib.util.find_spec("app.core.invoke_submission_backend")

    assert spec is not None


@pytest.mark.asyncio
async def test_memory_submission_backend_rejects_duplicate_request_in_progress() -> None:
    module = importlib.import_module("app.core.invoke_submission_backend")
    backend_type = getattr(module, "MemoryInvokeSubmissionBackend", None)
    acquire_result_type = getattr(module, "SubmissionAcquireResult", None)

    assert backend_type is not None
    assert acquire_result_type is not None

    backend = backend_type()
    acquired = await backend.acquire("owner:/v1/invoke/1:key", "fingerprint-1")
    duplicate = await backend.acquire("owner:/v1/invoke/1:key", "fingerprint-1")

    assert acquired is acquire_result_type.ACQUIRED
    assert duplicate is acquire_result_type.REQUEST_IN_PROGRESS


@pytest.mark.asyncio
async def test_memory_submission_backend_rejects_reused_key_with_different_fingerprint() -> None:
    module = importlib.import_module("app.core.invoke_submission_backend")
    backend_type = getattr(module, "MemoryInvokeSubmissionBackend", None)
    acquire_result_type = getattr(module, "SubmissionAcquireResult", None)

    assert backend_type is not None
    assert acquire_result_type is not None

    backend = backend_type()
    acquired = await backend.acquire("owner:/v1/invoke/1:key", "fingerprint-1")
    different = await backend.acquire("owner:/v1/invoke/1:key", "fingerprint-2")

    assert acquired is acquire_result_type.ACQUIRED
    assert different is acquire_result_type.IDEMPOTENCY_KEY_REUSED


@pytest.mark.asyncio
async def test_memory_submission_backend_release_reopens_the_slot() -> None:
    module = importlib.import_module("app.core.invoke_submission_backend")
    backend_type = getattr(module, "MemoryInvokeSubmissionBackend", None)
    acquire_result_type = getattr(module, "SubmissionAcquireResult", None)

    assert backend_type is not None
    assert acquire_result_type is not None

    backend = backend_type()
    await backend.acquire("owner:/v1/invoke/1:key", "fingerprint-1")
    await backend.release("owner:/v1/invoke/1:key", "fingerprint-1")
    reacquired = await backend.acquire("owner:/v1/invoke/1:key", "fingerprint-1")

    assert reacquired is acquire_result_type.ACQUIRED

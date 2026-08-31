import pytest

from hermes_trading.adapters import SchemaError
from hermes_trading.loop import CircuitBreaker, CircuitOpenError, fetch_with_retry


@pytest.mark.asyncio
async def test_adapter_is_retried_three_times_then_resets_breaker_on_success():
    attempts = 0
    sleeps = []

    async def flaky_fetch():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("temporary")
        return {"schema_version": 1, "value": "ok"}

    async def fake_sleep(delay: float):
        sleeps.append(delay)

    breaker = CircuitBreaker(threshold=5)
    result = await fetch_with_retry(
        "price", flaky_fetch, breaker, attempts=3, base_delay=0.25, sleep=fake_sleep
    )

    assert result["value"] == "ok"
    assert attempts == 3
    assert sleeps == [0.25, 0.5]
    assert breaker.failures["price"] == 0


@pytest.mark.asyncio
async def test_schema_error_is_not_retried():
    attempts = 0

    async def bad_schema():
        nonlocal attempts
        attempts += 1
        raise SchemaError("changed upstream")

    breaker = CircuitBreaker(threshold=5)
    with pytest.raises(SchemaError):
        await fetch_with_retry("price", bad_schema, breaker)

    assert attempts == 1


def test_circuit_breaker_opens_on_fifth_consecutive_failure():
    breaker = CircuitBreaker(threshold=5)

    for _ in range(4):
        breaker.record_failure("news")

    with pytest.raises(CircuitOpenError):
        breaker.record_failure("news")

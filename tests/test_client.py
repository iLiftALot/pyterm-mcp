from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from iterm2_api_wrapper.client import iTermClient
from iterm2_api_wrapper.gateway import ITermGateway


@dataclass
class DummyState:
    marker: str = "initial"
    setup_kwargs: dict[str, Any] | None = None

    refresh_callback: Any = None
    ensure_state_calls: int = 0
    ensure_state_exc: Exception | None = None

    async def ensure_state(self, refresh_callback: Any = None) -> None:
        self.ensure_state_calls += 1
        if self.ensure_state_exc is None:
            return
        exc = self.ensure_state_exc
        self.ensure_state_exc = None
        raise exc

    def refresh_from(self, new_state: Any) -> None:
        assert isinstance(new_state, DummyState)
        self.marker = new_state.marker
        self.setup_kwargs = new_state.setup_kwargs
        self.refresh_callback = new_state.refresh_callback


@dataclass
class FailingRefreshState(DummyState):
    def refresh_from(self, new_state: Any) -> None:
        raise RuntimeError("refresh_from failed")


class DummyGateway(ITermGateway[DummyState]):
    def __init__(self, states: list[DummyState]) -> None:
        self._states = list(states)
        self.calls: list[dict[str, Any]] = []

    async def create_state(self, **kwargs: Any) -> DummyState:
        self.calls.append(kwargs)
        state = self._states.pop(0) if self._states else DummyState()
        state.setup_kwargs = dict(kwargs)
        return state


def stop_client(client: iTermClient[Any]) -> None:
    client.close()


def test_client_initializes_state_and_thread() -> None:
    gateway = DummyGateway([DummyState(marker="boot")])
    client: iTermClient[DummyState] = iTermClient(
        gateway=gateway, debug=True, new_tab=False, select_tab=True, order_window_front=False
    )
    try:
        assert client._thread.is_alive()
        assert client.state.marker == "boot"
        assert gateway.calls == [{"debug": True, "new_tab": False, "select_tab": True, "order_window_front": False}]
    finally:
        stop_client(client)


def test_get_state_calls_ensure_state() -> None:
    gateway = DummyGateway([DummyState(marker="boot")])
    client: iTermClient[DummyState] = iTermClient(gateway=gateway)
    try:
        assert client.state.ensure_state_calls == 0
        state = client.get_state()
        assert state is client.state
        assert state.ensure_state_calls == 1
    finally:
        stop_client(client)


def test_get_state_refreshes_state_on_ensure_state_error() -> None:
    initial = DummyState(marker="initial", ensure_state_exc=RuntimeError("boom"))
    refreshed = DummyState(marker="refreshed")
    gateway = DummyGateway([initial, refreshed])

    client: iTermClient[DummyState] = iTermClient(gateway=gateway)
    try:
        state = client.get_state()
        assert state is client.state
        assert state.marker == "refreshed"
        assert state.ensure_state_calls == 1
        assert len(gateway.calls) == 2
    finally:
        stop_client(client)


def test_get_state_replaces_state_when_in_place_refresh_fails() -> None:
    initial = FailingRefreshState(marker="initial", ensure_state_exc=RuntimeError("boom"))
    refreshed = DummyState(marker="replacement")
    gateway = DummyGateway([initial, refreshed])

    client: iTermClient[DummyState] = iTermClient(gateway=gateway)
    try:
        state = client.get_state()
        assert state is refreshed
        assert client.state is refreshed
        assert state.marker == "replacement"
        assert len(gateway.calls) == 2
    finally:
        stop_client(client)


def test_async_create_factory_initializes_without_blocking_running_loop() -> None:
    async def scenario() -> None:
        gateway = DummyGateway([DummyState(marker="async")])
        client: iTermClient[DummyState] = await iTermClient.create(gateway=gateway, debug=False)
        try:
            assert client.state.marker == "async"
            assert gateway.calls == [{"debug": False}]
        finally:
            stop_client(client)

    asyncio.run(scenario())


def test_get_state_async_from_foreign_loop_calls_ensure_state() -> None:
    async def scenario() -> None:
        gateway = DummyGateway([DummyState(marker="boot")])
        client: iTermClient[DummyState] = iTermClient(gateway=gateway)
        try:
            state = await client.get_state_async()
            assert state is client.state
            assert state.ensure_state_calls == 1
        finally:
            stop_client(client)

    asyncio.run(scenario())


def test_get_state_async_from_client_loop_uses_direct_async_path() -> None:
    gateway = DummyGateway([DummyState(marker="boot")])
    client: iTermClient[DummyState] = iTermClient(gateway=gateway)
    try:
        async def call_from_client_loop() -> DummyState:
            return await client.get_state_async()

        state = asyncio.run_coroutine_threadsafe(call_from_client_loop(), client.loop).result(timeout=2)

        assert state is client.state
        assert state.ensure_state_calls == 1
    finally:
        stop_client(client)

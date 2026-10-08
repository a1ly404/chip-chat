"""Listener stub. Live Discord gateway wiring is intentionally absent."""

from __future__ import annotations

from chip_relay.config import RelayConfig


class ListenerNotWired(RuntimeError):
    """Raised if a caller asks the stub to connect."""


class ListenerStub:
    def __init__(self) -> None:
        self.started = False

    def run(self, config: RelayConfig) -> int:
        del config
        self.started = False
        return 0

    def connect(self, config: RelayConfig) -> None:
        del config
        raise ListenerNotWired("chip-relay listener is a stub; gateway connect is not implemented")

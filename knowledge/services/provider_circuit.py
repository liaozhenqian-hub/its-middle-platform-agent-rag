from threading import Lock
from time import monotonic
from typing import Callable


class NonRetryableProviderCircuit:
    """Temporarily suppress calls after provider-side request rejection."""

    def __init__(
        self,
        cooldown_seconds: float = 60.0,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self._retry_at = 0.0
        self._lock = Lock()

    def allow(self) -> bool:
        with self._lock:
            return self.clock() >= self._retry_at

    def record_success(self) -> None:
        with self._lock:
            self._retry_at = 0.0

    def record_failure(self, error: Exception) -> None:
        if getattr(error, "status_code", None) not in {400, 401, 403}:
            return
        with self._lock:
            self._retry_at = self.clock() + self.cooldown_seconds

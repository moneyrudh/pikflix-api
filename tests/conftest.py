import os
from typing import List, Tuple

import pytest

# Required at import time by app.config; tests never reach a real backend
os.environ.setdefault("BACKEND_SHARED_SECRET", "test-secret")

from app.services.rate_limit import UsageLimits  # noqa: E402


@pytest.fixture(autouse=True)
def usage_limits(monkeypatch) -> List[Tuple[str, str, int, int]]:  # a list with a `refuse` attribute
    """Usage limits never touch Supabase in tests: every request is allowed, and each check is recorded
    (bucket, key, per_minute, per_day). A test can refuse with `usage_limits.refuse = seconds`."""
    class Recorder(list):
        refuse = 0

    recorder = Recorder()

    async def take(self, bucket, key, per_minute, per_day):
        recorder.append((bucket, key, per_minute, per_day))
        return (recorder.refuse == 0, recorder.refuse)

    monkeypatch.setattr(UsageLimits, "take", take)
    return recorder

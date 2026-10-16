import json
from collections.abc import Callable

import pytest

LoggedEntries = Callable[[], list[dict[str, object]]]


@pytest.fixture
def logged_entries(capsys: pytest.CaptureFixture[str]) -> LoggedEntries:
    """Read the JSON lines logged to stdout since the last read."""

    # capsys swaps stdout after fixture setup: configure logging in the test body.
    def read() -> list[dict[str, object]]:
        return [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    return read

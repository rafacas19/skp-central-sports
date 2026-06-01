import pytest

from scouting_bot.ai.mock import MockAIProvider
from scouting_bot.service import ScoutingService
from scouting_bot.storage import Storage


@pytest.fixture
def storage():
    s = Storage(":memory:")
    yield s
    s.close()


@pytest.fixture
def service(storage):
    return ScoutingService(storage, MockAIProvider(), confidence_threshold=0.65)

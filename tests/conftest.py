import pytest

from triage.rag import RunbookIndex
from triage.state import LabState


@pytest.fixture
def state(tmp_path):
    return LabState(tmp_path / "state")


@pytest.fixture(scope="session")
def index():
    return RunbookIndex()

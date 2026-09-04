"""Pytest Configuration and Common Fixtures."""

import os
import sys
import pytest

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.session import Session, SessionManager
from app.core.event_logger import VoiceEventLogger


@pytest.fixture
def event_logger():
    return VoiceEventLogger()


@pytest.fixture
def session_manager():
    return SessionManager()


@pytest.fixture
def test_session(event_logger):
    return Session(session_id="test-session-001", event_logger=event_logger, initial_version=0)


import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

@pytest.fixture(autouse=True)
def fresh_db():
    db._client = None  # new in-memory database per test
    yield

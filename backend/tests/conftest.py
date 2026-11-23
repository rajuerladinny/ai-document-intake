import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLES_DIR = ROOT.parent / "samples"

# Set TEST_DATABASE_URL=postgresql://... to run the service tests against
# PostgreSQL as well (CI does). Without it they use in-memory SQLite.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


def make_store():
    from app.store import Store

    if not TEST_DATABASE_URL:
        return Store(":memory:")
    store = Store(TEST_DATABASE_URL)
    store.reset()
    return store

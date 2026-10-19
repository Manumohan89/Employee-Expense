"""MongoDB connection. Set MONGO_URI (Atlas). Without it, local dev uses an in-memory mongomock."""
import os
_client = None


def get_db():
    global _client
    if _client is None:
        uri = os.environ.get("MONGO_URI")
        if uri:
            from pymongo import MongoClient
            _client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        elif os.environ.get("RENDER") or os.environ.get("VERCEL"):
            raise RuntimeError("MONGO_URI is not set. Add your MongoDB Atlas connection string in the host's environment settings.")
        else:
            import mongomock
            _client = mongomock.MongoClient()
        d = _client[os.environ.get("MONGO_DB", "ledgerly")]
        d.users.create_index("email", unique=True)
        d.expenses.create_index([("user_id", 1), ("date", -1)])
    return _client[os.environ.get("MONGO_DB", "ledgerly")]

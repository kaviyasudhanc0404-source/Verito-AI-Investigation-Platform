"""
database/mongodb.py
--------------------
Async MongoDB Atlas connection using Motor (async PyMongo driver).
The client is created once at startup and shared across all requests.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings

settings = get_settings()

# Module-level client — initialised in lifespan, used everywhere
_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    """Return the shared Motor client. Must be called after connect()."""
    if _client is None:
        raise RuntimeError("MongoDB client is not initialised. Call connect() first.")
    return _client


def get_database() -> AsyncIOMotorDatabase:
    """Return the VERITO database handle."""
    return get_client()[settings.mongodb_database]


async def connect() -> None:
    """Open the MongoDB connection. Called during FastAPI startup."""
    global _client
    _client = AsyncIOMotorClient(settings.mongodb_uri)
    # Ping to verify the connection is working
    await _client.admin.command("ping")
    print("✅  MongoDB Atlas connected")


async def disconnect() -> None:
    """Close the MongoDB connection. Called during FastAPI shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        print("🔌  MongoDB Atlas disconnected")

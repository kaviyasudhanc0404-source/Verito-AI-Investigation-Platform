"""
database/collections.py
------------------------
Typed accessors for each MongoDB collection.
Import from here instead of hardcoding collection names.
"""

from motor.motor_asyncio import AsyncIOMotorCollection

from app.database.mongodb import get_database


def users() -> AsyncIOMotorCollection:
    return get_database()["users"]


def investigations() -> AsyncIOMotorCollection:
    return get_database()["investigations"]


def evidence() -> AsyncIOMotorCollection:
    return get_database()["evidence"]


def agent_runs() -> AsyncIOMotorCollection:
    return get_database()["agent_runs"]


def reports() -> AsyncIOMotorCollection:
    return get_database()["reports"]


def chat_messages() -> AsyncIOMotorCollection:
    return get_database()["chat_messages"]

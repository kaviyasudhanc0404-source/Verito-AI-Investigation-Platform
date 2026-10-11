"""
utils/id_generator.py
----------------------
Generate human-readable IDs used throughout VERITO.
"""

import uuid


def new_investigation_id() -> str:
    """Generate a unique investigation ID (e.g. 'inv_a3f7b2c1')."""
    return "inv_" + uuid.uuid4().hex[:8]


def new_user_id() -> str:
    """Generate a unique user ID (e.g. 'usr_a3f7b2c1')."""
    return "usr_" + uuid.uuid4().hex[:8]


def new_chat_id() -> str:
    """Generate a unique chat message ID (e.g. 'm_a3f7b2c1')."""
    return "m_" + uuid.uuid4().hex[:6]


def new_evidence_db_id() -> str:
    """Generate a unique MongoDB _id for an evidence document."""
    return "ev_" + uuid.uuid4().hex[:10]




def format_evidence_id(index: int) -> str:
    """
    Format an evidence sequence number as E-001, E-002, etc.
    index should be 1-based.
    """
    return f"E-{index:03d}"

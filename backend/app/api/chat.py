"""
api/chat.py
-----------
RAG-powered follow-up Q&A for completed investigations.
"""

from fastapi import APIRouter, Depends

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.database import collections
from app.dependencies.auth import get_current_user
from app.schemas.chat import ChatRequest, ChatMessageResponse
from app.services.investigation_service import get_investigation
from app.services.rag_service import answer_question
from app.utils.datetime_utils import utcnow
from app.utils.id_generator import new_chat_id

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/{investigation_id}", response_model=list[ChatMessageResponse])
async def send_message(
    investigation_id: str,
    body: ChatRequest,
    current_user: dict = Depends(get_current_user),
) -> list[ChatMessageResponse]:
    """
    Send a follow-up question about the investigation.
    Uses RAG to retrieve relevant evidence and generate a cited answer.
    Returns both the user message and the AI response.
    """
    if not body.text.strip():
        raise BadRequestException("Question text cannot be empty.")

    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")
    if inv["status"] != "complete":
        raise BadRequestException(
            "Follow-up questions are only available after the investigation is complete."
        )

    user_id = current_user["_id"]
    now = utcnow()

    # 1. Save user message
    user_msg_id = new_chat_id()
    user_msg = {
        "_id": user_msg_id,
        "investigation_id": investigation_id,
        "user_id": user_id,
        "role": "user",
        "text": body.text.strip(),
        "refs": [],
        "created_at": now,
    }
    await collections.chat_messages().insert_one(user_msg)

    # 2. Load chat history for context
    history_docs = await collections.chat_messages().find(
        {"investigation_id": investigation_id},
        sort=[("created_at", -1)],
        limit=10,
    ).to_list(length=None)

    history = [
        {"role": m["role"], "content": m["text"]}
        for m in reversed(history_docs)
    ]

    # 3. Run RAG pipeline
    answer_text, cited_ids = await answer_question(
        question=body.text.strip(),
        investigation_id=investigation_id,
        user_id=user_id,
        chat_history=history,
    )

    # 4. Save AI response
    ai_msg_id = new_chat_id()
    ai_msg = {
        "_id": ai_msg_id,
        "investigation_id": investigation_id,
        "user_id": user_id,
        "role": "verito",
        "text": answer_text,
        "refs": cited_ids,
        "created_at": utcnow(),
    }
    await collections.chat_messages().insert_one(ai_msg)

    return [
        ChatMessageResponse(id=user_msg_id, role="user", text=user_msg["text"], refs=[]),
        ChatMessageResponse(id=ai_msg_id, role="verito", text=answer_text, refs=cited_ids),
    ]


@router.get("/{investigation_id}", response_model=list[ChatMessageResponse])
async def get_chat_history(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> list[ChatMessageResponse]:
    """Return the full chat history for an investigation."""
    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")

    msgs = await collections.chat_messages().find(
        {"investigation_id": investigation_id},
        sort=[("created_at", 1)],
    ).to_list(length=None)

    return [
        ChatMessageResponse(
            id=m["_id"],
            role=m["role"],
            text=m["text"],
            refs=m.get("refs", []),
        )
        for m in msgs
    ]

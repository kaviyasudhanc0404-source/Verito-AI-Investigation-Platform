"""
api/chat.py
-----------
Follow-up questions about a completed investigation, answered from its evidence.
"""

from fastapi import APIRouter, Depends

from app.core.exceptions import BadRequestException
from app.database import collections
from app.dependencies.auth import get_owned_investigation
from app.schemas.chat import ChatMessageResponse, ChatRequest
from app.services.rag_service import answer_question
from app.services.report_service import get_report
from app.utils.datetime_utils import utcnow
from app.utils.id_generator import new_chat_id

router = APIRouter(prefix="/chat", tags=["Chat"])
_HISTORY_MESSAGES = 10


@router.post("/{investigation_id}", response_model=list[ChatMessageResponse])
async def send_message(body: ChatRequest, inv: dict = Depends(get_owned_investigation)) -> list[ChatMessageResponse]:
    """Answer a follow-up question; returns the saved question and answer."""
    question = body.text.strip()
    if not question:
        raise BadRequestException("Question text cannot be empty.")
    if inv["status"] != "complete":
        raise BadRequestException("Follow-up questions are available once the investigation is complete.")

    history = await collections.chat_messages().find(
        {"investigation_id": inv["_id"]}, sort=[("created_at", -1)], limit=_HISTORY_MESSAGES
    ).to_list(length=None)
    answer, refs = await answer_question(
        question=question,
        investigation_id=inv["_id"],
        user_id=inv["user_id"],
        chat_history=[{"role": m["role"], "content": m["text"]} for m in reversed(history)],
        report=await get_report(inv["_id"]),
    )

    saved = []
    for role, text, cited in (("user", question, []), ("verito", answer, refs)):
        doc = {
            "_id": new_chat_id(), "investigation_id": inv["_id"], "user_id": inv["user_id"],
            "role": role, "text": text, "refs": cited, "created_at": utcnow(),
        }
        await collections.chat_messages().insert_one(doc)
        saved.append(ChatMessageResponse.from_doc(doc))
    return saved


@router.get("/{investigation_id}", response_model=list[ChatMessageResponse])
async def get_chat_history(inv: dict = Depends(get_owned_investigation)) -> list[ChatMessageResponse]:
    messages = await collections.chat_messages().find(
        {"investigation_id": inv["_id"]}, sort=[("created_at", 1)]
    ).to_list(length=None)
    return [ChatMessageResponse.from_doc(m) for m in messages]

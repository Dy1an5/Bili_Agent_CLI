from .models import ChatRequest, ChatResponse
from .model_client import call_model_once

async def create_chat_completion(
    request: ChatRequest,
) -> ChatResponse:
    return await call_model_once(request)
from __future__ import annotations

import json
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bili_agent_cli.routes.agent import router as agent_router
from bili_agent_cli.routes.following import router as following_router
from bili_agent_cli.routes.favorites import router as favorites_router
from bili_agent_cli.routes.watch_later import router as watch_later_router
from bili_agent_cli.routes.search import router as search_router

class HealthResponse(BaseModel):
    status: Literal["ok"]


class PrettyJSONResponse(JSONResponse):
    def render(self, content: object) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=4,
        ).encode("utf-8")


app = FastAPI(
    title="Bili Agent CLI API",
    default_response_class=PrettyJSONResponse,
)
app.include_router(following_router)
app.include_router(favorites_router)
app.include_router(watch_later_router)
app.include_router(search_router)
app.include_router(agent_router)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")

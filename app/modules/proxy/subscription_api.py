from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, Request, Security
from pydantic import BaseModel
from starlette.responses import JSONResponse, Response

from app.core.anthropic.models import AnthropicMessageRequest
from app.core.auth.dependencies import validate_proxy_api_key
from app.core.types import JsonValue
from app.dependencies import AnthropicProxyContext, get_subscription_proxy_context
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.anthropic_service import AnthropicProxyError
from app.modules.proxy.api import _anthropic_proxy_error_response, v1_messages, v1_messages_count_tokens
from app.modules.proxy.subscription_policy import validate_subscription_model

router = APIRouter(prefix="/subscription", tags=["subscription"], dependencies=[Security(validate_proxy_api_key)])


class SubscriptionPolicy(BaseModel):
    provider: Literal["anthropic"] = "anthropic"
    authentication: Literal["subscription-oauth"] = "subscription-oauth"
    apiKeyFallback: Literal[False] = False
    extraUsage: Literal[False] = False


@router.get("/policy")
async def subscription_policy() -> SubscriptionPolicy:
    return SubscriptionPolicy()


@router.post("/v1/messages", response_model=None)
async def subscription_messages(
    request: Request,
    payload: AnthropicMessageRequest = Body(...),
    context: AnthropicProxyContext = Depends(get_subscription_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    try:
        validate_subscription_model(payload.model)
    except AnthropicProxyError as exc:
        return _anthropic_proxy_error_response(exc)
    return await v1_messages(request, payload, context, api_key)


@router.post("/v1/messages/count_tokens", response_model=None)
async def subscription_count_tokens(
    request: Request,
    payload: dict[str, JsonValue] = Body(...),
    context: AnthropicProxyContext = Depends(get_subscription_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    model = payload.get("model")
    try:
        validate_subscription_model(model if isinstance(model, str) else "")
    except AnthropicProxyError as exc:
        return _anthropic_proxy_error_response(exc)
    return await v1_messages_count_tokens(request, payload, context, api_key)


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def unknown_subscription_route(path: str) -> Response:
    return JSONResponse(status_code=404, content={"error": {"type": "subscription_route_not_found"}})

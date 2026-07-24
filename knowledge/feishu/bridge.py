from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from time import perf_counter
from typing import Any
from uuid import uuid4

from knowledge.feishu.gateway import FeishuGateway
from knowledge.feishu.messages import (
    format_agent_reply,
    parse_message_event,
    split_reply,
)
from knowledge.feishu.repository import FeishuEventRepository
from knowledge.quality import TurnStart


logger = logging.getLogger(__name__)
_FAILURE_REPLY = "当前机器人暂时无法处理该请求，请稍后重试。"
_POSITIVE_REACTIONS = {"THUMBSUP", "OK", "APPLAUSE", "HEART"}
_NEGATIVE_REACTIONS = {"THUMBSDOWN", "DISLIKE"}


class FeishuBotBridge:
    def __init__(
        self,
        *,
        gateway: FeishuGateway,
        agent_service: Any,
        repository: FeishuEventRepository,
        quality_capture: Any | None = None,
        reply_max_chars: int,
        agent_timeout_seconds: float,
        require_group_mention: bool = True,
        provider: str = "",
        model_name: str = "",
        thread_isolation_enabled: bool = True,
        ownership_service: Any | None = None,
    ) -> None:
        self.gateway = gateway
        self.agent_service = agent_service
        self.repository = repository
        self.quality_capture = quality_capture
        self.reply_max_chars = reply_max_chars
        self.agent_timeout_seconds = agent_timeout_seconds
        self.require_group_mention = require_group_mention
        self.provider = provider
        self.model_name = model_name
        self.thread_isolation_enabled = thread_isolation_enabled
        self.ownership_service = ownership_service
        self._loop: asyncio.AbstractEventLoop | None = None
        self._futures: set[Future] = set()
        self._closing = False
        self._user_names: dict[str, str] = {}

    async def start(self) -> None:
        await self.repository.initialize()
        self._loop = asyncio.get_running_loop()
        self._closing = False
        await asyncio.to_thread(self.gateway.start, self._submit_event)

    async def close(self) -> None:
        self._closing = True
        for future in tuple(self._futures):
            future.cancel()
        await self.gateway.close()
        self._futures.clear()

    def _submit_event(self, payload: dict[str, Any]) -> None:
        if self._closing or self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self.handle_event(payload), self._loop)
        self._futures.add(future)
        future.add_done_callback(self._futures.discard)

    async def handle_event(self, payload: dict[str, Any]) -> bool:
        if payload.get("event_type") == "reaction":
            return await self._handle_reaction(payload)
        message = parse_message_event(
            payload,
            require_group_mention=self.require_group_mention,
            bot_open_id=str(getattr(self.gateway, "bot_open_id", "") or ""),
        )
        if message is None:
            return False
        claimed = await self.repository.claim(
            message.event_id,
            message.message_id,
            message.chat_id,
        )
        if not claimed:
            return False

        started_at = perf_counter()
        run_id = str(uuid4())
        quality_turn = None
        sender_name = message.sender_name or await self._user_name(message.sender_id)
        conversation_id = await self._conversation_id(message)
        if self.ownership_service is not None:
            if not message.sender_id:
                raise ValueError("Feishu sender open_id is required")
            await self.ownership_service.claim(
                conversation_id,
                message.sender_id,
                channel="feishu",
            )
        if self.quality_capture is not None:
            try:
                quality_turn = await self.quality_capture.start(
                    TurnStart(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        channel="feishu",
                        channel_message_id=message.message_id,
                        user_id=message.sender_id or None,
                        user_name=sender_name or None,
                        chat_id=message.chat_id,
                        question=message.text,
                        knowledge_space_id="middle-platform",
                        provider=self.provider,
                        model_name=self.model_name,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Feishu quality start failed event_id=%s error_type=%s",
                    message.event_id,
                    type(exc).__name__,
                )
        try:
            response = await asyncio.wait_for(
                self.agent_service.chat(
                    message.text,
                    conversation_id,
                    run_id=run_id,
                    knowledge_space_id="middle-platform",
                    domain_id=None,
                    scope_provided=True,
                    user_id=message.sender_id or None,
                ),
                timeout=self.agent_timeout_seconds,
            )
            reply = format_agent_reply(response)
            chunks = split_reply(reply, max_chars=self.reply_max_chars)
            first_reply_id = ""
            for index, chunk in enumerate(chunks, 1):
                title = (
                    "中台助手"
                    if len(chunks) == 1
                    else f"中台助手 ({index}/{len(chunks)})"
                )
                reply_id = await asyncio.to_thread(
                    self.gateway.reply_markdown,
                    message.message_id,
                    chunk,
                    title=title,
                )
                if not first_reply_id:
                    first_reply_id = str(reply_id or "")
            if self.quality_capture is not None and quality_turn is not None:
                try:
                    if first_reply_id:
                        await self.quality_capture.bind_reply(run_id, first_reply_id)
                    await self.quality_capture.complete_response(
                        run_id,
                        response,
                        status=str(
                            getattr(response, "status", "completed") or "completed"
                        ),
                        duration_ms=(perf_counter() - started_at) * 1000,
                    )
                except Exception as capture_error:
                    logger.warning(
                        "Feishu quality completion failed event_id=%s error_type=%s",
                        message.event_id,
                        type(capture_error).__name__,
                    )
            await self.repository.complete(message.event_id)
            logger.info(
                "Feishu event completed event_id=%s message_id=%s duration_ms=%.2f",
                message.event_id,
                message.message_id,
                (perf_counter() - started_at) * 1000,
            )
            return True
        except Exception as exc:
            error_type = type(exc).__name__
            if self.quality_capture is not None and quality_turn is not None:
                try:
                    await self.quality_capture.complete_response(
                        run_id,
                        None,
                        status=("timeout" if isinstance(exc, TimeoutError) else "error"),
                        duration_ms=(perf_counter() - started_at) * 1000,
                        error_type=error_type,
                    )
                except Exception as capture_error:
                    logger.warning(
                        "Feishu quality completion failed event_id=%s error_type=%s",
                        message.event_id,
                        type(capture_error).__name__,
                    )
            await self.repository.fail(message.event_id, error_type)
            logger.warning(
                "Feishu event failed event_id=%s message_id=%s error_type=%s duration_ms=%.2f",
                message.event_id,
                message.message_id,
                error_type,
                (perf_counter() - started_at) * 1000,
            )
            try:
                await asyncio.to_thread(
                    self.gateway.reply_text,
                    message.message_id,
                    _FAILURE_REPLY,
                )
            except Exception as reply_error:
                logger.warning(
                    "Feishu failure reply unavailable event_id=%s error_type=%s",
                    message.event_id,
                    type(reply_error).__name__,
                )
            return False

    async def _conversation_id(self, message) -> str:
        if not self.thread_isolation_enabled:
            return f"feishu:{message.chat_id}"
        if message.chat_type != "group":
            return f"feishu:{message.chat_id}"
        thread_key = message.thread_id or message.root_id
        if thread_key:
            return f"feishu:group:{message.chat_id}:thread:{thread_key}"
        if message.parent_id and self.quality_capture is not None:
            repository = getattr(self.quality_capture, "repository", None)
            if repository is not None:
                parent = await repository.get_turn_by_channel_reply(
                    "feishu", message.parent_id
                )
                if parent is None:
                    parent = await repository.get_turn_by_channel_message(
                        "feishu", message.parent_id
                    )
                if parent is not None and parent.conversation_id:
                    return parent.conversation_id
        return f"feishu:group:{message.chat_id}:topic:{message.message_id}"

    async def _handle_reaction(self, payload: dict[str, Any]) -> bool:
        if self.quality_capture is None:
            return False
        message_id = str(payload.get("message_id") or "").strip()
        user_id = str(payload.get("user_id") or "").strip()
        emoji_type = str(payload.get("emoji_type") or "").strip().upper()
        action = str(payload.get("action") or "").strip()
        if not message_id or not user_id or action not in {"created", "deleted"}:
            return False
        turn = await self.quality_capture.repository.get_turn_by_channel_reply(
            "feishu", message_id
        )
        if turn is None:
            return False
        if action == "deleted":
            await self.quality_capture.repository.delete_feedback(
                turn_id=turn.id, channel="feishu", user_id=user_id
            )
            return True
        if emoji_type in _POSITIVE_REACTIONS:
            rating = "positive"
        elif emoji_type in _NEGATIVE_REACTIONS:
            rating = "negative"
        else:
            return False
        await self.quality_capture.repository.upsert_feedback(
            turn_id=turn.id,
            feedback_token=None,
            rating=rating,
            reason=emoji_type,
            user_id=user_id,
            user_name=await self._user_name(user_id),
            channel="feishu",
            trusted=True,
        )
        return True

    async def _user_name(self, user_id: str) -> str:
        if not user_id:
            return ""
        if user_id not in self._user_names:
            try:
                self._user_names[user_id] = await asyncio.to_thread(
                    self.gateway.get_user_name, user_id
                )
            except Exception:
                self._user_names[user_id] = ""
        return self._user_names[user_id]

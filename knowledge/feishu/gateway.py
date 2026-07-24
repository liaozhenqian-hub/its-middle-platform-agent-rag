from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

import lark_oapi as lark
import lark_oapi.ws.client as lark_ws_client
from lark_oapi.api.contact.v3 import GetUserRequest
from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
from lark_oapi.channel.bot_identity import fetch_bot_identity


logger = logging.getLogger(__name__)


class FeishuGatewayError(RuntimeError):
    pass


class FeishuGateway(Protocol):
    @property
    def bot_open_id(self) -> str: ...

    def start(self, handler: Callable[[dict[str, Any]], None]) -> None: ...

    def reply_text(self, message_id: str, text: str) -> str: ...

    def reply_markdown(
        self,
        message_id: str,
        text: str,
        *,
        title: str = "中台助手",
    ) -> str: ...

    def get_user_name(self, user_id: str) -> str: ...

    async def close(self) -> None: ...


def normalize_sdk_event(data: Any) -> dict[str, Any]:
    header = getattr(data, "header", None)
    event = getattr(data, "event", None)
    sender = getattr(event, "sender", None)
    message = getattr(event, "message", None)
    mentions = getattr(message, "mentions", None) or []
    sender_id = getattr(sender, "sender_id", None)
    return {
        "event_id": str(getattr(header, "event_id", None) or ""),
        "sender_type": str(getattr(sender, "sender_type", None) or ""),
        "sender_id": str(getattr(sender_id, "open_id", None) or ""),
        "sender_name": "",
        "message_id": str(getattr(message, "message_id", None) or ""),
        "thread_id": str(getattr(message, "thread_id", None) or ""),
        "root_id": str(getattr(message, "root_id", None) or ""),
        "parent_id": str(getattr(message, "parent_id", None) or ""),
        "chat_id": str(getattr(message, "chat_id", None) or ""),
        "chat_type": str(getattr(message, "chat_type", None) or ""),
        "message_type": str(getattr(message, "message_type", None) or ""),
        "content": str(getattr(message, "content", None) or ""),
        "mentions": [
            {
                "key": str(getattr(mention, "key", None) or ""),
                "open_id": str(
                    getattr(getattr(mention, "id", None), "open_id", None) or ""
                ),
                "mentioned_type": str(
                    getattr(mention, "mentioned_type", None) or ""
                ),
            }
            for mention in mentions
        ],
    }


def normalize_sdk_reaction_event(data: Any, *, action: str) -> dict[str, Any]:
    header = getattr(data, "header", None)
    event = getattr(data, "event", None)
    reaction_type = getattr(event, "reaction_type", None)
    user_id = getattr(event, "user_id", None)
    return {
        "event_type": "reaction",
        "event_id": str(getattr(header, "event_id", None) or ""),
        "action": action,
        "message_id": str(getattr(event, "message_id", None) or ""),
        "emoji_type": str(getattr(reaction_type, "emoji_type", None) or ""),
        "user_id": str(getattr(user_id, "open_id", None) or ""),
        "action_time": str(getattr(event, "action_time", None) or ""),
    }


class _ObservedWsClient(lark.ws.Client):
    def __init__(self, *args, connected_event: threading.Event, **kwargs):
        self._connected_event = connected_event
        super().__init__(*args, **kwargs)

    async def _connect(self) -> None:
        await super()._connect()
        if self._conn is not None:
            self._connected_event.set()

    async def _disconnect(self) -> None:
        try:
            await super()._disconnect()
        finally:
            self._connected_event.clear()


class LarkOapiGateway:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        connect_timeout_seconds: float = 15.0,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._connect_timeout_seconds = connect_timeout_seconds
        self._connected = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_error: Exception | None = None
        self._ws_client: _ObservedWsClient | None = None
        self._bot_open_id = ""
        self._api_client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.ERROR)
            .build()
        )

    @property
    def bot_open_id(self) -> str:
        return self._bot_open_id

    @property
    def connected(self) -> bool:
        return self._connected.is_set() and bool(
            self._thread is not None and self._thread.is_alive()
        )

    def start(self, handler: Callable[[dict[str, Any]], None]) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        identity = asyncio.run(fetch_bot_identity(self._api_client._config))
        self._bot_open_id = str(getattr(identity, "open_id", None) or "")
        if not self._bot_open_id:
            logger.warning(
                "Feishu bot identity unavailable; group mentions will be ignored"
            )

        def receive(data: Any) -> None:
            handler(normalize_sdk_event(data))

        def reaction_created(data: Any) -> None:
            handler(normalize_sdk_reaction_event(data, action="created"))

        def reaction_deleted(data: Any) -> None:
            handler(normalize_sdk_reaction_event(data, action="deleted"))

        dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(receive)
            .register_p2_im_message_reaction_created_v1(reaction_created)
            .register_p2_im_message_reaction_deleted_v1(reaction_deleted)
            .build()
        )
        self._ws_client = _ObservedWsClient(
            self._app_id,
            self._app_secret,
            log_level=lark.LogLevel.ERROR,
            event_handler=dispatcher,
            auto_reconnect=True,
            connected_event=self._connected,
        )
        self._thread_error = None
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="feishu-bot-connection",
            daemon=True,
        )
        self._thread.start()
        if not self._connected.wait(self._connect_timeout_seconds):
            error_type = (
                type(self._thread_error).__name__
                if self._thread_error is not None
                else "ConnectionTimeout"
            )
            raise FeishuGatewayError(
                f"Feishu long connection unavailable error_type={error_type}"
            )

    def _run(self) -> None:
        try:
            if self._ws_client is not None:
                self._ws_client.start()
        except Exception as exc:
            self._thread_error = exc
            if not self._stopping.is_set():
                self._connected.clear()

    def reply_text(self, message_id: str, text: str) -> str:
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self._api_client.im.v1.message.reply(request)
        if not response.success():
            raise FeishuGatewayError(
                f"Feishu reply failed code={getattr(response, 'code', 'unknown')}"
            )
        return self._response_message_id(response)

    def reply_markdown(
        self,
        message_id: str,
        text: str,
        *,
        title: str = "中台助手",
    ) -> str:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [{"tag": "markdown", "content": text}],
        }
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        try:
            response = self._api_client.im.v1.message.reply(request)
        except Exception as exc:
            logger.warning(
                "Feishu markdown card unavailable; falling back to text "
                "error_type=%s",
                type(exc).__name__,
            )
            return self.reply_text(message_id, text)
        if response.success():
            return self._response_message_id(response)
        logger.warning(
            "Feishu markdown card rejected; falling back to text code=%s",
            getattr(response, "code", "unknown"),
        )
        return self.reply_text(message_id, text)

    def get_user_name(self, user_id: str) -> str:
        if not user_id.strip():
            return ""
        request = (
            GetUserRequest.builder()
            .user_id_type("open_id")
            .user_id(user_id)
            .build()
        )
        try:
            response = self._api_client.contact.v3.user.get(request)
        except Exception as exc:
            logger.warning(
                "Feishu user name unavailable error_type=%s", type(exc).__name__
            )
            return ""
        if not response.success():
            return ""
        user = getattr(getattr(response, "data", None), "user", None)
        return str(getattr(user, "name", None) or "")

    @staticmethod
    def _response_message_id(response: Any) -> str:
        return str(getattr(getattr(response, "data", None), "message_id", None) or "")

    async def close(self) -> None:
        self._stopping.set()
        client = self._ws_client
        thread = self._thread
        if client is not None and thread is not None and thread.is_alive():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    client._disconnect(),
                    lark_ws_client.loop,
                )
                await asyncio.to_thread(future.result, 5)
            except Exception:
                pass
            lark_ws_client.loop.call_soon_threadsafe(lark_ws_client.loop.stop)
            await asyncio.to_thread(thread.join, 5)
        self._connected.clear()

from dataclasses import dataclass


@dataclass(frozen=True)
class FeishuIncomingMessage:
    event_id: str
    message_id: str
    chat_id: str
    chat_type: str
    text: str
    sender_id: str = ""
    sender_name: str = ""
    thread_id: str = ""
    root_id: str = ""
    parent_id: str = ""

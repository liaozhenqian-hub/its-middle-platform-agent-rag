import asyncio
import os

import pytest

from knowledge.feishu.gateway import LarkOapiGateway


@pytest.mark.live
def test_feishu_long_connection_and_reply_live_smoke():
    if os.getenv("RUN_FEISHU_LIVE_SMOKE") != "1":
        pytest.skip("set RUN_FEISHU_LIVE_SMOKE=1 for an explicit Feishu live test")
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    message_id = os.getenv("FEISHU_TEST_MESSAGE_ID", "").strip()
    if not app_id or not app_secret or not message_id:
        pytest.skip("rotated Feishu credentials and FEISHU_TEST_MESSAGE_ID are required")

    gateway = LarkOapiGateway(app_id, app_secret)
    try:
        gateway.start(lambda payload: None)
        gateway.reply_text(message_id, "中台知识机器人飞书长连接测试成功。")
    finally:
        asyncio.run(gateway.close())

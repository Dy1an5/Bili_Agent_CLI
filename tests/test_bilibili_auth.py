from __future__ import annotations

import asyncio
import unittest

import httpx

from bili_agent_cli.bilibili.auth import create_qr_login, wait_for_qr_login


class BilibiliAuthTest(unittest.TestCase):
    def test_qr_login_collects_response_cookies(self) -> None:
        async def run_scenario() -> dict[str, str]:
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/generate"):
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "message": "0",
                            "data": {
                                "url": "https://example.test/qr",
                                "qrcode_key": "qr-key",
                            },
                        },
                    )

                return httpx.Response(
                    200,
                    headers=[
                        (
                            "set-cookie",
                            "SESSDATA=session-value; Domain=.bilibili.com; Path=/",
                        ),
                        (
                            "set-cookie",
                            "bili_jct=csrf-value; Domain=.bilibili.com; Path=/",
                        ),
                        (
                            "set-cookie",
                            "DedeUserID=123; Domain=.bilibili.com; Path=/",
                        ),
                    ],
                    json={
                        "code": 0,
                        "message": "0",
                        "data": {"code": 0, "message": ""},
                    },
                )

            transport = httpx.MockTransport(handle_request)

            async with httpx.AsyncClient(transport=transport) as client:
                qr_login = await create_qr_login(client)
                return await wait_for_qr_login(client, qr_login)

        cookies = asyncio.run(run_scenario())
        self.assertEqual(cookies["SESSDATA"], "session-value")
        self.assertEqual(cookies["bili_jct"], "csrf-value")
        self.assertEqual(cookies["DedeUserID"], "123")


if __name__ == "__main__":
    unittest.main()

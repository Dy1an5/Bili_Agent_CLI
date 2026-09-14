from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from bili_agent_cli.bilibili.subtitles import (
    SubtitleFetchResult,
    VideoSubtitleError,
    fetch_video_subtitle,
)
from bili_agent_cli.content.store import ContentStore
from bili_agent_cli.content.subtitles import get_video_subtitle
from bili_agent_cli.schemas.subtitles import (
    GetVideoSubtitleArgs,
    SubtitleCue,
    SubtitleStatus,
    SubtitleTrack,
    SubtitleTrackSource,
    SubtitleUnavailableReason,
    VideoSubtitleDocument,
)


NAV_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "wbi_img": {
            "img_url": (
                "https://i0.hdslb.com/bfs/wbi/"
                "abcdefghijklmnopqrstuvwxyz0123456789.png"
            ),
            "sub_url": (
                "https://i0.hdslb.com/bfs/wbi/"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.png"
            ),
        }
    },
}

TRACKS = [
    {
        "lan": "ai-zh",
        "lan_doc": "中文（自动生成）",
        "subtitle_url": "//aisubtitle.hdslb.com/subtitle/ai.json",
        "type": 1,
    },
    {
        "lan": "en-US",
        "lan_doc": "English",
        "subtitle_url": "https://aisubtitle.hdslb.com/subtitle/en.json",
        "type": 0,
    },
    {
        "lan": "zh-CN",
        "lan_doc": "中文",
        "subtitle_url_v2": "//aisubtitle.hdslb.com/subtitle/zh.json",
        "type": 0,
    },
    {"lan": "broken", "subtitle_url": "http://insecure.example/subtitle"},
]


class SubtitleSchemaTest(unittest.TestCase):
    def test_args_normalize_ids_and_expose_pagination_defaults(self) -> None:
        args = GetVideoSubtitleArgs(bvid="  BV1abc123  ", cid=" 456 ")

        self.assertEqual(args.bvid, "BV1abc123")
        self.assertEqual(args.cid, "456")
        self.assertEqual(args.offset, 0)
        self.assertEqual(args.limit, 100)
        self.assertFalse(args.refresh)

    def test_args_reject_invalid_boundaries_and_extra_fields(self) -> None:
        invalid = (
            {"bvid": "av123", "cid": "1"},
            {"bvid": "BV1abc", "cid": "0"},
            {"bvid": "BV1abc", "cid": "not-a-cid"},
            {"bvid": "BV1abc", "cid": "1", "language": "zh CN"},
            {"bvid": "BV1abc", "cid": "1", "offset": -1},
            {"bvid": "BV1abc", "cid": "1", "limit": 201},
            {"bvid": "BV1abc", "cid": "1", "unknown": True},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                GetVideoSubtitleArgs.model_validate(value)


class BilibiliSubtitleTest(unittest.TestCase):
    def test_fetches_signed_player_info_and_preferred_human_chinese_track(self) -> None:
        requested_paths: list[str] = []

        def handle_request(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            if request.url.path == "/x/web-interface/nav":
                self.assertEqual(request.headers["cookie"], "SESSDATA=test")
                return httpx.Response(200, json=NAV_PAYLOAD)
            if request.url.path == "/x/player/wbi/v2":
                self.assertEqual(request.url.params["bvid"], "BV1subtitle")
                self.assertEqual(request.url.params["cid"], "987")
                self.assertIn("wts", request.url.params)
                self.assertEqual(len(request.url.params["w_rid"]), 32)
                self.assertEqual(request.headers["cookie"], "SESSDATA=test")
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "message": "0",
                        "data": {"subtitle": {"subtitles": TRACKS}},
                    },
                )
            self.assertEqual(request.url.path, "/subtitle/zh.json")
            self.assertNotIn("cookie", request.headers)
            return httpx.Response(
                200,
                json={
                    "body": [
                        {"from": 1.2344, "to": 2.5, "content": " 第一行\n文字 "},
                        {"from": 2.5, "to": 2.5, "content": "无效"},
                        {"from": 2.5, "to": 4, "content": "第二行"},
                    ]
                },
            )

        async def run() -> SubtitleFetchResult:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await fetch_video_subtitle(
                    "BV1subtitle",
                    "987",
                    "SESSDATA=test",
                    client=client,
                )

        result = asyncio.run(run())

        self.assertEqual(
            requested_paths,
            [
                "/x/web-interface/nav",
                "/x/player/wbi/v2",
                "/subtitle/zh.json",
            ],
        )
        self.assertIsNotNone(result.document)
        assert result.document is not None
        self.assertEqual(result.document.track.language, "zh-CN")
        self.assertEqual(
            result.document.track.source,
            SubtitleTrackSource.HUMAN,
        )
        self.assertEqual(
            [track.language for track in result.document.available_tracks],
            ["zh-CN", "ai-zh", "en-US"],
        )
        self.assertEqual(
            result.document.cues[0].model_dump(),
            {
                "index": 0,
                "start_ms": 1234,
                "end_ms": 2500,
                "text": "第一行 文字",
            },
        )
        self.assertEqual(result.document.cues[1].index, 1)
        self.assertRegex(result.document.source_hash, r"^sha256:[0-9a-f]{64}$")

    def test_returns_language_not_found_without_fetching_subtitle_file(self) -> None:
        requested_paths: list[str] = []

        def handle_request(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            if request.url.path == "/x/web-interface/nav":
                return httpx.Response(200, json=NAV_PAYLOAD)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"subtitle": {"subtitles": TRACKS}},
                },
            )

        async def run() -> SubtitleFetchResult:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await fetch_video_subtitle(
                    "BV1subtitle",
                    "987",
                    "SESSDATA=test",
                    language="ja-JP",
                    client=client,
                )

        result = asyncio.run(run())

        self.assertIsNone(result.document)
        self.assertEqual(result.reason, SubtitleUnavailableReason.LANGUAGE_NOT_FOUND)
        self.assertEqual(
            requested_paths,
            ["/x/web-interface/nav", "/x/player/wbi/v2"],
        )

    def test_returns_no_subtitle_as_normal_unavailable_result(self) -> None:
        def handle_request(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/x/web-interface/nav":
                return httpx.Response(200, json=NAV_PAYLOAD)
            return httpx.Response(200, json={"code": 0, "data": {}})

        async def run() -> SubtitleFetchResult:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                return await fetch_video_subtitle(
                    "BV1subtitle", "987", "SESSDATA=test", client=client
                )

        result = asyncio.run(run())
        self.assertIsNone(result.document)
        self.assertEqual(result.reason, SubtitleUnavailableReason.NO_SUBTITLE)

    def test_maps_player_business_and_subtitle_file_errors(self) -> None:
        scenarios = (
            (
                {"code": -404, "message": "视频不存在"},
                None,
                "播放器信息接口错误 -404",
                -404,
            ),
            (
                {"code": 0, "data": {"subtitle": {"subtitles": "bad"}}},
                None,
                "轨道列表格式不正确",
                None,
            ),
            (
                {"code": 0, "data": {"subtitle": {"subtitles": TRACKS}}},
                {"body": [{"from": 1, "to": 1, "content": "bad"}]},
                "没有可用文本",
                None,
            ),
        )
        for player_payload, subtitle_payload, message, code in scenarios:
            with self.subTest(message=message):
                def handle_request(request: httpx.Request) -> httpx.Response:
                    if request.url.path == "/x/web-interface/nav":
                        return httpx.Response(200, json=NAV_PAYLOAD)
                    if request.url.path == "/x/player/wbi/v2":
                        return httpx.Response(200, json=player_payload)
                    return httpx.Response(200, json=subtitle_payload)

                async def run() -> None:
                    async with httpx.AsyncClient(
                        transport=httpx.MockTransport(handle_request)
                    ) as client:
                        with self.assertRaisesRegex(VideoSubtitleError, message) as ctx:
                            await fetch_video_subtitle(
                                "BV1subtitle",
                                "987",
                                "SESSDATA=test",
                                client=client,
                            )
                        self.assertEqual(ctx.exception.code, code)

                asyncio.run(run())

    def test_maps_http_and_invalid_json_errors(self) -> None:
        async def run_http_error() -> None:
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/x/web-interface/nav":
                    return httpx.Response(200, json=NAV_PAYLOAD)
                return httpx.Response(503)

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                with self.assertRaises(VideoSubtitleError) as ctx:
                    await fetch_video_subtitle(
                        "BV1subtitle", "987", "SESSDATA=test", client=client
                    )
                self.assertEqual(ctx.exception.status, 503)

        async def run_json_error() -> None:
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/x/web-interface/nav":
                    return httpx.Response(200, json=NAV_PAYLOAD)
                return httpx.Response(200, content=b"not-json")

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                with self.assertRaisesRegex(VideoSubtitleError, "无法解析"):
                    await fetch_video_subtitle(
                        "BV1subtitle", "987", "SESSDATA=test", client=client
                    )

        async def run_network_error() -> None:
            def handle_request(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/x/web-interface/nav":
                    return httpx.Response(200, json=NAV_PAYLOAD)
                raise httpx.ConnectError("offline", request=request)

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ) as client:
                with self.assertRaisesRegex(VideoSubtitleError, "网络请求失败"):
                    await fetch_video_subtitle(
                        "BV1subtitle", "987", "SESSDATA=test", client=client
                    )

        asyncio.run(run_http_error())
        asyncio.run(run_json_error())
        asyncio.run(run_network_error())


class SubtitleCacheServiceTest(unittest.TestCase):
    def test_caches_full_document_and_pages_agent_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ContentStore(Path(directory) / "content.db")
            now = datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc)
            track = SubtitleTrack(
                language="zh-CN",
                display_name="中文",
                source=SubtitleTrackSource.HUMAN,
            )
            document = VideoSubtitleDocument(
                bvid="BV1subtitle",
                cid="987",
                track=track,
                available_tracks=[track],
                cues=[
                    SubtitleCue(index=0, start_ms=0, end_ms=1000, text="一"),
                    SubtitleCue(index=1, start_ms=1000, end_ms=2000, text="二"),
                ],
                source_hash="sha256:" + "a" * 64,
                fetched_at=now,
            )
            fetch_result = SubtitleFetchResult(
                document=document,
                available_tracks=(track,),
                reason=None,
                fetched_at=now,
            )

            async def run() -> tuple[object, object]:
                first = await get_video_subtitle(
                    GetVideoSubtitleArgs(
                        bvid="BV1subtitle", cid="987", limit=1
                    ),
                    "SESSDATA=test",
                    store=store,
                    now=now,
                )
                second = await get_video_subtitle(
                    GetVideoSubtitleArgs(
                        bvid="BV1subtitle", cid="987", offset=1, limit=1
                    ),
                    "SESSDATA=test",
                    store=store,
                    now=now,
                )
                return first, second

            with patch(
                "bili_agent_cli.content.subtitles.fetch_video_subtitle",
                new=AsyncMock(return_value=fetch_result),
            ) as fetch_mock:
                first, second = asyncio.run(run())

            self.assertEqual(fetch_mock.await_count, 1)
            self.assertEqual(first.status, SubtitleStatus.AVAILABLE)
            self.assertFalse(first.cached)
            self.assertTrue(first.has_more)
            self.assertEqual(first.next_offset, 1)
            self.assertTrue(second.cached)
            self.assertFalse(second.has_more)
            self.assertEqual(second.cues[0].text, "二")
            with store.connect() as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM subtitle_documents"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM subtitle_cues"
                    ).fetchone()[0],
                    2,
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import unittest

import httpx

from bili_agent_cli.bilibili.video_details import (
    VideoDetailError,
    fetch_default_video_record,
)
from bili_agent_cli.content.cid_resolver import CidCandidate, resolve_video_cids


DETAIL_PAYLOAD = {
    "code": 0,
    "message": "0",
    "data": {
        "aid": 123,
        "bvid": "BVdetail",
        "cid": 9001,
        "title": "多P视频",
        "desc": "详情",
        "pic": "//i0.hdslb.com/cover.jpg",
        "duration": 100,
        "pubdate": 1_757_808_000,
        "owner": {"mid": 456, "name": "UP", "face": "//i0/face.jpg"},
        "stat": {
            "view": 100,
            "danmaku": 10,
            "favorite": 20,
            "reply": 5,
            "like": 30,
        },
        "pages": [
            {"cid": 9001, "page": 1, "part": "P1"},
            {"cid": 9002, "page": 2, "part": "P2"},
        ],
    },
}


class VideoDetailTest(unittest.TestCase):
    def test_fetches_only_default_cid_from_multi_part_detail(self) -> None:
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=DETAIL_PAYLOAD)
                )
            ) as client:
                return await fetch_default_video_record(
                    "BVdetail", "SESSDATA=test", client
                )

        record = asyncio.run(run())
        self.assertEqual(record.identity.cid, "9001")
        self.assertTrue(record.detail.is_default_part)
        self.assertEqual(record.detail.aid, "123")
        self.assertEqual(record.feedback.likes, 30)


class CidResolverTest(unittest.TestCase):
    def test_uses_direct_and_cached_cids_without_fetching(self) -> None:
        calls = 0

        async def fetch_default(bvid, cookie, client):
            nonlocal calls
            calls += 1
            raise AssertionError("should not fetch")

        result = asyncio.run(
            resolve_video_cids(
                [
                    CidCandidate(key="direct", bvid="BVdirect", cid="100"),
                    CidCandidate(key="cached", bvid="BVcached"),
                ],
                sessdata_cookie="SESSDATA=test",
                lookup_default_cid=lambda bvid: (
                    "200" if bvid == "BVcached" else None
                ),
                fetch_default_video=fetch_default,
            )
        )

        self.assertEqual(calls, 0)
        self.assertEqual([item.cid for item in result.resolved], ["100", "200"])
        self.assertEqual(result.status, "completed")

    def test_deduplicates_fetches_and_returns_partial_failures(self) -> None:
        calls: list[str] = []

        async def fetch_default(bvid, cookie, client):
            calls.append(bvid)
            if bvid == "BVfail":
                raise VideoDetailError("不存在", code=-404)
            payload = {
                **DETAIL_PAYLOAD,
                "data": {**DETAIL_PAYLOAD["data"], "bvid": bvid},
            }
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=payload)
                )
            ) as detail_client:
                return await fetch_default_video_record(
                    bvid, cookie, detail_client
                )

        result = asyncio.run(
            resolve_video_cids(
                [
                    CidCandidate(key="one", bvid="BVsame"),
                    CidCandidate(key="two", bvid="BVsame"),
                    CidCandidate(key="bad", bvid="BVfail"),
                ],
                sessdata_cookie="SESSDATA=test",
                lookup_default_cid=lambda bvid: None,
                fetch_default_video=fetch_default,
            )
        )

        self.assertEqual(calls.count("BVsame"), 1)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual([item.cid for item in result.resolved], ["9001", "9001"])
        self.assertEqual(result.failures[0].upstream_code, -404)


if __name__ == "__main__":
    unittest.main()

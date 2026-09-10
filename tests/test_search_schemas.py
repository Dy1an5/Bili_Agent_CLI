from __future__ import annotations

import unittest

from pydantic import ValidationError

from bili_agent_cli.schemas.search import (
    SearchVideoQuery,
    SearchVideoResponse,
    VideoSearchDuration,
    VideoSearchOrder,
)


EXPECTED_SEARCH_RESPONSE = {
    "videos": [
        {
            "aid": "123",
            "bvid": "BV1search",
            "title": "测试搜索结果",
            "description": "视频简介",
            "cover_url": "https://i0.hdslb.com/search.jpg",
            "duration_seconds": 272,
            "published_at": "2025-09-10T02:46:40Z",
            "author": {
                "mid": "456",
                "name": "测试UP主",
                "avatar_url": "https://i0.hdslb.com/avatar.jpg",
            },
            "stats": {
                "views": 1000,
                "danmaku": 20,
                "favorites": 30,
                "replies": 40,
                "likes": 50,
            },
        }
    ],
    "total_count": 21,
    "page": 1,
    "page_size": 20,
    "has_more": True,
}


class SearchSchemasTest(unittest.TestCase):
    def test_query_contract_and_defaults(self) -> None:
        query = SearchVideoQuery(keyword="  Python 教程  ")

        self.assertEqual(query.keyword, "Python 教程")
        self.assertEqual(query.page, 1)
        self.assertEqual(query.page_size, 20)
        self.assertEqual(query.order, VideoSearchOrder.RELEVANCE)
        self.assertEqual(query.duration, VideoSearchDuration.ANY)

    def test_query_rejects_invalid_values(self) -> None:
        invalid_queries = (
            {"keyword": ""},
            {"keyword": "Python", "page": 0},
            {"keyword": "Python", "page_size": 51},
            {"keyword": "Python", "tid": 0},
            {
                "keyword": "Python",
                "published_after": "2025-09-10T00:00:00",
            },
            {
                "keyword": "Python",
                "published_after": "2025-09-11T00:00:00Z",
                "published_before": "2025-09-10T00:00:00Z",
            },
        )

        for raw_query in invalid_queries:
            with self.subTest(raw_query=raw_query):
                with self.assertRaises(ValidationError):
                    SearchVideoQuery.model_validate(raw_query)

    def test_response_converts_published_at_to_datetime(self) -> None:
        response = SearchVideoResponse.model_validate(EXPECTED_SEARCH_RESPONSE)

        self.assertEqual(
            response.model_dump(mode="json"),
            EXPECTED_SEARCH_RESPONSE,
        )


if __name__ == "__main__":
    unittest.main()

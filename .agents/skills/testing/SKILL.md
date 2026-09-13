---
name: testing
description: "仅在用户显式调用 $testing 时，为本项目的代码修改选择 pytest 范围、执行回归并正确处理失败。"
---

# Testing

使用 pytest 验证行为，不访问真实 B 站，不读取 `secret/`。测试使用 `httpx.MockTransport`、mock 和临时目录隔离网络、凭据与持久化数据。

## 测试顺序

1. 修改期间先运行最小定向范围：`uv run pytest -q tests/test_<feature>.py`，必要时用 `::TestClass::test_case` 聚焦单例。
2. 修复失败后先重跑原失败范围，再按影响面补充相关回归。
3. 交付前始终运行 `uv run pytest -q`。当前全量套件很快，不以“改动很小”为由跳过。

## 影响面映射

- B 站 client/parser：对应领域测试；涉及公共解析器、认证或 WBI 时追加所有直接使用方测试。
- Pydantic schema：对应 schema/领域测试，并运行 `tests/test_main.py` 检查 FastAPI 契约。
- FastAPI route 或 `main.py`：运行 `tests/test_main.py` 和对应领域测试。
- Agent Tool：运行对应领域测试、`tests/test_agent_tools.py`、`tests/test_result_models.py`；涉及来源、Evidence 或分页时追加 `tests/test_agent_context.py`。
- context 或 memory：运行对应的 `test_context_*`/`test_memory_*`，并追加 `tests/test_agent_context.py`；影响 CLI 或 HTTP 会话接口时再追加 `tests/test_cli.py` 或 `tests/test_main.py`。
- profile/auth/CLI：运行直接测试；公共入口或错误映射变化时追加 `tests/test_main.py`。

## 回归处理

- 先读取失败断言和相关实现，判断是本次回归、预期契约变化还是无关既有失败。必要时用修改前可复现证据区分，不猜测原因。
- 本次回归应修实现并增加能捕获该问题的最小测试。不得删除测试、放宽关键断言、吞掉异常或过度 mock 只为变绿。
- 预期契约变更必须同步实现、契约测试及面向用户的文档，再运行所有受影响测试。
- 无关既有失败不要擅自扩大修复范围；记录准确命令和失败摘要，并继续完成仍可验证的范围。
- 最终报告列出实际运行的命令、通过数量和任何未解决失败；不得声称未运行的测试已通过。

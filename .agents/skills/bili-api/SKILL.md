---
name: bili-api
description: "仅在用户显式调用 $bili-api 时，按本项目约定实现或审查 Bilibili 接口客户端、FastAPI 路由与 Pydantic 模型。"
---

# Bili API

让 Bilibili 上游契约、项目内部模型和 HTTP API 保持清晰分层。不要读取、输出或上传 `secret/` 中的任何内容。

## 开始前

1. 先检查 `src/bili_agent_cli/schemas/`、`bilibili/`、`routes/` 中最接近的现有功能，不另造并行架构。
2. 涉及 B 站接口地址、参数、签名、响应字段或业务码时，必须先用 `rg` 在 `/home/dylan/Projects/PiliPlus` 和 `/home/dylan/Projects/newBili` 查找对应实现。只参考接口契约，不照搬 UI 或状态管理代码；若两个项目不一致，以当前可验证的接口行为为准并保留兼容解析。
3. 明确该接口是读取还是写入、是否需要登录、WBI、`bili_jct`/CSRF 或其他凭据。写操作不得仅凭读取接口的认证方式推断。

## 分层约定

- 在 `schemas/<feature>.py` 定义公开查询和响应模型。查询模型使用 `ConfigDict(extra="forbid")`，用 `Field`、受约束类型、枚举和 validator 表达边界；不要在路由或请求函数中重复校验。
- 对外 ID 使用字符串，避免数值精度和跨接口类型差异。缺失或未知数据使用 `None`，不要伪造为 `0` 或空字符串。HTTP 响应时间使用带时区的 UTC ISO 8601；仅 Agent 投影转换为 UTC+8 文本。
- 在 `bilibili/<feature>.py` 封装上游请求、业务码检查和解析。公开异步函数接受可选注入的 `httpx.AsyncClient`；默认客户端使用有限超时，且只关闭函数自行创建的客户端。
- 将 HTTP 状态、网络异常、非法 JSON、非法响应结构和非零 B 站业务码转换为功能专用异常，保留可用的 HTTP 状态或业务码。顶层结构错误应失败；列表内单条畸形记录可在不破坏分页语义时跳过。
- 复用 `bilibili/parsing.py`、认证头和 WBI 工具。规范化图片 URL；读取接口只发送实际需要的 Cookie。不得记录 Cookie、CSRF、签名原文或响应中的敏感字段。
- 在 `routes/<feature>.py` 使用 `APIRouter`、显式 `response_model` 和 Pydantic Query/Path 模型。认证资料错误映射为 401，上游 B 站失败映射为 502；保留异常链。最后在 `main.py` 注册 router。

## 验证

- 用 `httpx.MockTransport` 断言请求路径、参数、必要请求头、签名和解析结果，不访问真实 B 站。
- 覆盖参数边界、成功响应、非零业务码、网络/HTTP/JSON 错误、空列表和关键兼容字段。
- 覆盖 FastAPI 状态码与响应模型契约。只有用户也显式调用了 `$agent-tool`，才加载其完整 Tool 规则。
- 运行定向测试与全量回归；只有用户也显式调用了 `$testing`，才加载其测试规则。

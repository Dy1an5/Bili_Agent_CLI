---
name: agent-tool
description: "仅在用户显式调用 $agent-tool 时，按 Args、Result、Registry、Executor、Schema、Test 完整链路新增或审查本项目 Agent Tool。"
---

# Agent Tool

新增或修改 Tool 时必须完成下面六项。任何一项缺失都不算完成。若用户也显式调用了 `$bili-api`，同时遵循其 B 站接口规则；不得读取 `secret/`。

## 必备链路

1. **Args**：在 `schemas/` 定义专用 Pydantic 参数模型，禁止额外字段并描述字段语义、默认值和边界。即使无参数也要有空模型。
2. **Result**：定义完整、强类型的服务结果模型。它是内部/FastAPI 契约，不为节省模型上下文而删除字段。
3. **Registry**：在 `agent/registry.py` 注册稳定的 snake_case 名称、面向模型的操作说明、Args/Result 模型、类型检查适配器、executor 和 result projector。描述应说明前置 Tool、ID 来源及分页方式。
4. **Executor**：Tool wrapper 放在 `agent/tools/bili/`，只负责加载认证、组装依赖并调用领域服务；继续使用通用 `execute_tool` 的参数校验、执行、Result 验证和投影流程。新增可预期领域异常时，补充稳定错误码，不能泄露异常或凭据。
5. **Schema**：让 `build_tool_schemas()` 从 Args 的 `model_json_schema()` 生成函数参数；不要手写一份会漂移的 JSON Schema。确认 required、properties、枚举、默认值与描述对模型足够明确。
6. **Test**：至少覆盖注册与 Schema、合法执行、非法参数、领域异常、非法 Result/投影以及最终精简数据。

## Agent 结果约定

- 在 `agent/result_models.py` 定义独立的 LLM 投影，只保留回答与后续调用需要的字段，不改变完整 Result。
- 视频结果必须从真实 `bvid` 生成 `bilibili:video:<bvid>` 形式的 `source_id`，不得让模型自行构造。
- 发送给模型的时间使用项目统一的 UTC+8 文本；FastAPI Result 继续使用 UTC ISO 8601。
- 保留继续翻页所需的页码、`has_more`、offset 或双游标，并确保上下文系统能提取新增分页形状。

## 完成检查

- 在 `tests/test_agent_tools.py` 更新注册集合并断言 Schema 和 executor 行为。
- 在 `tests/test_result_models.py` 测试字段裁剪、来源 ID 和时间序列化。
- 若新增来源、Evidence 或分页形状，在 `tests/test_agent_context.py` 覆盖跨轮使用和来源校验。
- 运行相关测试与全量回归；只有用户也显式调用了 `$testing`，才加载其测试规则。

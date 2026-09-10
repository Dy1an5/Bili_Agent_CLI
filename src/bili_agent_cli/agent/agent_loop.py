import json
from typing import Any

from .models import (
    AgentSource,
    AgentStep,
    AgentRunResponse,
)
from bili_agent_cli.agent.deepseek.model_client import create_agent_message
from .registry import build_tool_schemas
from .executor import execute_tool
MAX_AGENT_STEPS = 10

SYSTEM_PROMPT = """
你是用户的 Bilibili 内容助手

你可以：
查询动态
查询收藏
查询稍后再看
搜索视频
分析用户画像

规则：
不编造视频
涉及用户数据时优先调用工具
推荐必须解释依据
没登录时不要尝试访问账号接口
""".strip()

def deduplicate_sources(
    sources: list[AgentSource],
) -> list[AgentSource]:
    seen: set[tuple[str, str]] = set()

    result: list[AgentSource] = []

    for source in sources:
        key = (
            source.bvid,
            source.cid,
        )

        if key in seen:
            continue

        seen.add(key)

        result.append(source)

    return result

async def run_agent(task: str) -> AgentRunResponse:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (SYSTEM_PROMPT),
        },
        {
            "role": "user",
            "content": task
        }
    ]

    tools = build_tool_schemas()

    trace: list[AgentStep] = []

    sources: list[AgentSource] = []

    for step in range(1, MAX_AGENT_STEPS + 1):
        assistant_message = (
            await create_agent_message(
                messages = messages,
                tools = tools,
            )
        )

        tool_calls = assistant_message.get("tool_calls")

        if not tool_calls:
            content = assistant_message.get("content")

            if not isinstance(content, str):
                content = "模型没有生成有效最终答案"

            return AgentRunResponse(
                answer = content,
                sources = deduplicate_sources(sources),
                trace = trace,
            )

        messages.append(assistant_message)

        for tool_call in tool_calls:
            tool_call_id = tool_call.get("id")
            function = tool_call.get("function")

            if (not isinstance(tool_call_id, str) or not isinstance(function, dict)):
                continue

            tool_name = function.get("name")
            arguments_text = function.get("arguments")

            if not isinstance(tool_name, str):
                continue

            if not isinstance(arguments_text, str):
                tool_result = {
                    "ok": "False",
                    "error": "INVALID_ARGUMENT_JSON",
                }
            else: 
                try:
                    raw_arguments = json.loads(arguments_text)

                except (json.JSONDecodeError):
                    tool_result = {
                        "ok": False,
                        "error": "INVALID_ARGUMENT_JSON",
                    }

                else:
                    tool_result = await execute_tool(
                        tool_name,
                        raw_arguments,
                    )

            trace.append(
                AgentStep(
                    step = step,
                    tool = tool_name,
                    ok = tool_result.get("ok") is True,
                )
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(
                        tool_result,
                        ensure_ascii=False,
                    )
                }
            )

    return AgentRunResponse(
        answer = (
            "Agent 达到最大步骤数,"
            "任务为正常结束."
        ),
        sources = (deduplicate_sources(sources)),
        trace = trace,
    )
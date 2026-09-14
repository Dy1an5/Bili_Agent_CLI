import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from bili_agent_cli.agent.deepseek.config import (
    AGENT_CONTEXT_MAX_UNITS,
    AGENT_CONTEXT_RECENT_TURNS,
    AGENT_CONTEXT_SUMMARIZE_AT_UNITS,
    AGENT_CONTEXT_SUMMARY_MAX_TOKENS,
    AGENT_CONTEXT_TURN_EVIDENCE_UNITS,
    AGENT_CONTEXT_TOOL_RESULT_UNITS,
    AGENT_SESSION_CAPACITY,
    AGENT_SESSION_TTL_SECONDS,
)
from bili_agent_cli.agent.deepseek.model_client import (
    create_agent_message,
    create_conversation_summary,
    create_memory_extraction,
)
from bili_agent_cli.agent.deepseek.errors import ModelCallError

from .context.manager import (
    ContextManager,
    ContextSettings,
    extract_pagination_state,
    extract_sources,
    merge_sources,
)
from .context.models import AgentEvidenceBatch, ConversationTurn
from .context.store import FileSessionStore
from .memory.service import (
    MemoryProcessingResult,
    process_memory_candidates,
    render_memory_context,
    retrieve_memories,
    should_extract_memory,
)
from .memory.store import MemoryStorageError, MemoryStore
from .executor import execute_tool
from .tool_context import ToolExecutionContext
from .models import (
    AgentFinalAnswer,
    AgentMemoryResult,
    AgentMemoryStatus,
    AgentRunResponse,
    AgentSource,
    AgentStep,
    TokenUsage,
)
from .registry import SUBMIT_AGENT_ANSWER_TOOL_NAME, build_tool_schemas

MAX_AGENT_STEPS = 10
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
你是用户的 Bilibili 内容助手

你可以：
查询动态
查询关注用户
查询收藏
查询稍后再看
查询观看历史
搜索视频
分析用户画像
将可信工具结果中的视频保存到收藏夹

规则：
不编造视频
涉及用户数据时优先调用工具
推荐必须解释依据
用户明确要求分析画像或根据个人喜好推荐时，先调用 get_user_profile
普通搜索、查看动态或指定条件查询不要调用 get_user_profile
画像中的 explicit_preferences 高于 inferred；冲突时服从明确偏好并说明
没登录时不要尝试访问账号接口
用户要求继续翻页时，优先使用最近 pagination_state 中的 next_arguments
pagination_state 的 has_more 为 false 时，不要重复请求同一页
工具结果中的 source_id 是可信视频来源标识
工具结果中的时间已经换算成 UTC+8，直接使用该文本，不要再次换算或改标时区
最终回答必须单独调用 submit_agent_answer 提交 answer 和实际使用的 source_ids
不要把没有用于回答的视频放进 source_ids，也不要编造 source_id
不要声称长期记忆已经成功保存；记忆保存结果由程序另行提示
收藏写入必须先调用 prepare_save_videos_to_favorite_folder，展示预检计划并结束回答
只有用户在下一轮明确确认后，才能调用 commit_save_videos_to_favorite_folder
不得在同一用户轮次中连续调用 prepare 和 commit，也不得自行构造 source_id 或 confirmation_id
commit 返回 retryable=true 时保留原确认计划；用户下一轮要求重试可直接再次 commit，不必重新 prepare
写入失败时只陈述工具返回的 added_count、upstream_code 和 upstream_message，不要推测成功数量或原因
""".strip()

session_store = FileSessionStore(
    ttl_seconds=AGENT_SESSION_TTL_SECONDS,
    capacity=AGENT_SESSION_CAPACITY,
)

context_manager = ContextManager(
    ContextSettings(
        max_input_units=AGENT_CONTEXT_MAX_UNITS,
        summarize_at_units=AGENT_CONTEXT_SUMMARIZE_AT_UNITS,
        recent_turns=AGENT_CONTEXT_RECENT_TURNS,
        summary_max_tokens=AGENT_CONTEXT_SUMMARY_MAX_TOKENS,
        max_tool_result_units=AGENT_CONTEXT_TOOL_RESULT_UNITS,
        max_turn_evidence_units=AGENT_CONTEXT_TURN_EVIDENCE_UNITS,
    )
)

memory_store = MemoryStore()

BVID_PATTERN = re.compile(
    r"(?<![0-9A-Za-z])(BV[0-9A-Za-z]{6,20})(?![0-9A-Za-z])"
)


def _collect_retained_sources(turns: list[ConversationTurn]) -> list[AgentSource]:
    sources: list[AgentSource] = []
    for turn in turns:
        sources.extend(turn.sources)
        for batch in turn.evidence_batches:
            sources.extend(extract_sources(batch.result, batch.tool_name))
    return merge_sources(sources)


def _select_sources(
    source_ids: list[str],
    candidates: list[AgentSource],
) -> tuple[list[AgentSource], list[str]]:
    by_id = {source.source_id: source for source in candidates}
    selected = [by_id[source_id] for source_id in source_ids if source_id in by_id]
    unknown = [source_id for source_id in source_ids if source_id not in by_id]
    return merge_sources(selected), unknown


def _fallback_source_ids(
    answer: str,
    candidates: list[AgentSource],
) -> list[str]:
    by_bvid = {source.bvid: source.source_id for source in candidates}
    result: list[str] = []
    for bvid in BVID_PATTERN.findall(answer):
        source_id = by_bvid.get(bvid)
        if source_id is not None and source_id not in result:
            result.append(source_id)
    return result


async def run_agent(
    task: str,
    session_id: UUID | None = None,
) -> AgentRunResponse:
    session = (
        await session_store.create()
        if session_id is None
        else await session_store.get(session_id)
    )

    async with session.lock:
        total_input_tokens = 0
        total_output_tokens = 0

        def record_usage(usage: TokenUsage) -> None:
            nonlocal total_input_tokens, total_output_tokens
            total_input_tokens += usage.input_tokens
            total_output_tokens += usage.output_tokens

        def current_usage() -> TokenUsage:
            return TokenUsage(
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )

        if session.pending_turn is not None:
            previous_pending = session.pending_turn
            previous_pending.status = "interrupted"
            previous_pending.updated_at = datetime.now(timezone.utc)
            if not previous_pending.assistant_content:
                previous_pending.assistant_content = (
                    "上一轮在生成最终回答前中断。"
                )
            session.turns.append(previous_pending)
            session.pending_turn = None
            session.updated_at = datetime.now(timezone.utc)
            await session_store.save(session)

        try:
            retrieved_memories = retrieve_memories(
                memory_store,
                task,
                limit=8,
            )
        except MemoryStorageError as error:
            logger.warning(
                "memory retrieval failed: %s",
                error.__class__.__name__,
            )
            retrieved_memories = []

        run_system_prompt = SYSTEM_PROMPT
        if retrieved_memories:
            run_system_prompt += "\n\n" + render_memory_context(
                retrieved_memories
            )

        tools = build_tool_schemas()

        async def summarize_with_usage(
            summary_input: str,
            max_tokens: int,
        ) -> str:
            return await create_conversation_summary(
                summary_input,
                max_tokens,
                on_usage=record_usage,
            )

        context_compacted = await context_manager.compact_session_if_needed(
            system_prompt=run_system_prompt,
            session=session,
            task=task,
            tools=tools,
            summarize=summarize_with_usage,
        )
        if context_compacted:
            session.updated_at = datetime.now(timezone.utc)
            await session_store.save(session)
        messages = context_manager.build_messages(
            system_prompt=run_system_prompt,
            session=session,
            task=task,
        )
        context_manager.ensure_fits(messages, tools)

        pending_turn = ConversationTurn(
            status="pending",
            user_content=task,
        )
        session.pending_turn = pending_turn
        session.updated_at = datetime.now(timezone.utc)
        await session_store.save(session)

        trace: list[AgentStep] = []
        evidence_batches = pending_turn.evidence_batches
        pagination_states = {}
        source_candidates = _collect_retained_sources(session.turns)

        async def finish_turn(
            answer: str,
            final_sources: list[AgentSource],
        ) -> AgentRunResponse:
            now = datetime.now(timezone.utc)
            pending_turn.status = "completed"
            pending_turn.assistant_content = answer
            pending_turn.sources = final_sources
            pending_turn.pagination_states = list(pagination_states.values())
            pending_turn.updated_at = now
            session.turns.append(pending_turn)
            session.pending_turn = None
            session.updated_at = now
            await session_store.save(session)

            memory_result = AgentMemoryResult()
            if not should_extract_memory(pending_turn.user_content):
                memory_result = AgentMemoryResult(
                    status=AgentMemoryStatus.NO_CANDIDATES,
                )
                return AgentRunResponse(
                    answer=answer,
                    session_id=session.id,
                    context_compacted=context_compacted,
                    sources=final_sources,
                    trace=trace,
                    memory=memory_result,
                    usage=current_usage(),
                )

            try:
                extraction = await create_memory_extraction(
                    pending_turn.user_content,
                    on_usage=record_usage,
                )
            except ModelCallError as error:
                error_code = _memory_error_code(error)
                logger.warning(
                    "memory extraction failed: %s",
                    error_code,
                )
                memory_result = AgentMemoryResult(
                    status=AgentMemoryStatus.EXTRACTION_FAILED,
                    error_code=error_code,
                )
            except ValidationError as error:
                logger.warning(
                    "memory extraction validation failed: %s",
                    error.__class__.__name__,
                )
                memory_result = AgentMemoryResult(
                    status=AgentMemoryStatus.EXTRACTION_FAILED,
                    error_code=error.__class__.__name__,
                )
            else:
                try:
                    processing = process_memory_candidates(
                        memory_store,
                        extraction.candidates,
                        user_content=pending_turn.user_content,
                        source_ref=(
                            f"session:{session.id}:turn:{pending_turn.id}"
                        ),
                    )
                except MemoryStorageError as error:
                    logger.warning(
                        "memory storage failed: %s",
                        error.__class__.__name__,
                    )
                    memory_result = AgentMemoryResult(
                        status=AgentMemoryStatus.STORAGE_FAILED,
                        error_code=error.__class__.__name__,
                    )
                else:
                    memory_result = _memory_result_from_processing(processing)

            return AgentRunResponse(
                answer=answer,
                session_id=session.id,
                context_compacted=context_compacted,
                sources=final_sources,
                trace=trace,
                memory=memory_result,
                usage=current_usage(),
            )

        async def mark_turn_interrupted(error_code: str) -> None:
            now = datetime.now(timezone.utc)
            pending_turn.status = "interrupted"
            pending_turn.error_code = error_code
            pending_turn.pagination_states = list(pagination_states.values())
            pending_turn.updated_at = now
            session.updated_at = now
            await session_store.save(session)

        async def ensure_working_context_fits() -> None:
            try:
                context_manager.ensure_fits(messages, tools)
            except Exception as exc:
                await mark_turn_interrupted(exc.__class__.__name__)
                raise

        for step in range(1, MAX_AGENT_STEPS + 1):
            try:
                assistant_message = await create_agent_message(
                    messages=messages,
                    tools=tools,
                    on_usage=record_usage,
                )
            except Exception as exc:
                await mark_turn_interrupted(exc.__class__.__name__)
                raise
            raw_tool_calls = assistant_message.get("tool_calls")
            tool_calls = raw_tool_calls if isinstance(raw_tool_calls, list) else []

            if not tool_calls:
                content = assistant_message.get("content")
                if not isinstance(content, str):
                    content = "模型没有生成有效最终答案"

                fallback_source_ids = _fallback_source_ids(
                    content,
                    source_candidates,
                )
                final_sources, _ = _select_sources(
                    fallback_source_ids,
                    source_candidates,
                )
                return await finish_turn(content, final_sources)

            messages.append(assistant_message)

            if len(tool_calls) == 1:
                only_call = tool_calls[0]
                only_function = (
                    only_call.get("function")
                    if isinstance(only_call, dict)
                    else None
                )
                only_name = (
                    only_function.get("name")
                    if isinstance(only_function, dict)
                    else None
                )
                if only_name == SUBMIT_AGENT_ANSWER_TOOL_NAME:
                    tool_call_id = only_call.get("id")
                    arguments_text = only_function.get("arguments")
                    final_answer = None

                    if isinstance(arguments_text, str):
                        try:
                            final_arguments = json.loads(arguments_text)
                            final_answer = AgentFinalAnswer.model_validate(
                                final_arguments
                            )
                        except (json.JSONDecodeError, ValidationError):
                            pass

                    if final_answer is not None:
                        final_sources, unknown_source_ids = _select_sources(
                            final_answer.source_ids,
                            source_candidates,
                        )
                        if not unknown_source_ids:
                            return await finish_turn(
                                final_answer.answer,
                                final_sources,
                            )
                        final_tool_result = {
                            "ok": False,
                            "error": "UNKNOWN_SOURCE_IDS",
                            "unknown_source_ids": unknown_source_ids,
                        }
                    else:
                        final_tool_result = {
                            "ok": False,
                            "error": "INVALID_FINAL_ANSWER",
                        }

                    if isinstance(tool_call_id, str):
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": json.dumps(
                                    final_tool_result,
                                    ensure_ascii=False,
                                ),
                            }
                        )
                        await ensure_working_context_fits()
                    continue

            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = tool_call.get("id")
                function = tool_call.get("function")

                if not isinstance(tool_call_id, str) or not isinstance(function, dict):
                    continue

                tool_name = function.get("name")
                arguments_text = function.get("arguments")

                if not isinstance(tool_name, str):
                    continue

                if tool_name == SUBMIT_AGENT_ANSWER_TOOL_NAME:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "error": "FINAL_ANSWER_MUST_BE_SUBMITTED_ALONE",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue

                raw_arguments = None
                if not isinstance(arguments_text, str):
                    tool_result = {
                        "ok": False,
                        "error": "INVALID_ARGUMENT_JSON",
                    }
                else:
                    try:
                        raw_arguments = json.loads(arguments_text)
                    except json.JSONDecodeError:
                        tool_result = {
                            "ok": False,
                            "error": "INVALID_ARGUMENT_JSON",
                        }
                    else:
                        tool_result = await execute_tool(
                            tool_name,
                            raw_arguments,
                            ToolExecutionContext(
                                session=session,
                                current_turn_id=pending_turn.id,
                                trusted_sources=source_candidates,
                                on_usage=record_usage,
                            ),
                        )
                        pagination_state = extract_pagination_state(
                            tool_name,
                            raw_arguments,
                            tool_result,
                        )
                        if pagination_state is not None:
                            pagination_states[tool_name] = pagination_state

                trace.append(
                    AgentStep(
                        step=step,
                        tool=tool_name,
                        ok=tool_result.get("ok") is True,
                    )
                )

                model_tool_result = context_manager.compact_tool_result_for_turn(
                    tool_result,
                    evidence_batches,
                )
                evidence_batches.append(
                    AgentEvidenceBatch(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        arguments=(
                            raw_arguments
                            if isinstance(raw_arguments, dict)
                            else None
                        ),
                        result=model_tool_result,
                    )
                )
                source_candidates = merge_sources(
                    source_candidates
                    + extract_sources(model_tool_result, tool_name)
                )
                now = datetime.now(timezone.utc)
                pending_turn.pagination_states = list(pagination_states.values())
                pending_turn.updated_at = now
                session.updated_at = now
                await session_store.save(session)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            model_tool_result,
                            ensure_ascii=False,
                        ),
                    }
                )
                await ensure_working_context_fits()

            await ensure_working_context_fits()

        answer = "Agent 达到最大步骤数,任务未正常结束."
        pending_turn.assistant_content = answer
        await mark_turn_interrupted("MAX_AGENT_STEPS_REACHED")
        return AgentRunResponse(
            answer=answer,
            session_id=session.id,
            context_compacted=context_compacted,
            sources=[],
            trace=trace,
            usage=current_usage(),
        )


def _memory_result_from_processing(
    processing: MemoryProcessingResult,
) -> AgentMemoryResult:
    active_count = processing.active_saved_count + processing.promoted_count
    if active_count and processing.pending_saved_count:
        status = AgentMemoryStatus.MIXED
    elif active_count or processing.updated_count:
        status = AgentMemoryStatus.SAVED
    elif processing.pending_saved_count:
        status = AgentMemoryStatus.PENDING
    elif processing.filtered_count:
        status = AgentMemoryStatus.FILTERED
    else:
        status = AgentMemoryStatus.NO_CANDIDATES

    return AgentMemoryResult(
        status=status,
        saved_count=active_count,
        extracted_count=processing.extracted_count,
        active_saved_count=processing.active_saved_count,
        pending_saved_count=processing.pending_saved_count,
        promoted_count=processing.promoted_count,
        updated_count=processing.updated_count,
        filtered_count=processing.filtered_count,
    )


def _memory_error_code(error: Exception) -> str:
    name = error.__class__.__name__
    status_code = getattr(error, "status_code", None)
    return f"{name}:{status_code}" if isinstance(status_code, int) else name

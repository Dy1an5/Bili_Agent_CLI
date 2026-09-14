# Bili Agent CLI

所有命令都在项目根目录中执行。

## 扫码登录

```bash
uv run bili-agent-cli login
```

终端会显示二维码。使用哔哩哔哩手机客户端扫码并确认后，Cookie 会写入项目根目录下的 `secret/profile.txt`。该文件已加入 Git 忽略，程序会将权限设置为仅当前用户可读写。

## 检查登录资料

```bash
uv run bili-agent-cli profile
```

该命令只输出 Cookie 字段是否齐全和当前用户 ID，不输出任何 Cookie 值。

## 调试 B站 GET 接口

```bash
uv run bili-agent-cli get /x/web-interface/nav
uv run bili-agent-cli get /x/web-interface/view --param bvid=BV1xx411c7mD
```

多个查询参数可以重复传入 `--param`：

```bash
uv run bili-agent-cli get /x/v3/fav/folder/created/list-all \
    --param up_mid=当前用户ID
```

调试入口只允许访问 `https://api.bilibili.com` 下的绝对路径，并且只发送 `SESSDATA`，不会输出请求 Cookie。

## 启动 Agent 对话

直接在终端启动多轮对话：

```bash
uv run bili-agent-cli agent
```

对话过程中输入 `/new` 开始新会话，输入 `/context` 查看当前 Session 保存的摘要、轮次、工具证据、实际引用来源和未完成轮次，输入 `/memory` 管理长期记忆，输入 `/exit` 退出。会话会持久化到 `privacy/conversations/`；Agent HTTP 接口在程序重启后仍可传入原 `session_id` 载入。CLI 暂未提供 `/resume` 选择入口。

## 长期记忆

长期记忆保存在 `privacy/memory.db`。用户明确表达的长期偏好会直接生效；从一次任务中推断出的偏好只进入 `pending`，同一偏好由两个不同轮次支持后才会自动激活。只有与当前主题或任务意图匹配的 active 记忆会加入模型上下文，pending 不参与回答。

完整的架构、数据表、调用链路、状态机、写入去重评分、读取召回评分、安全边界和维护说明见 [`docs/memory.md`](docs/memory.md)。

交互式 Agent 支持以下命令：

```text
/memory list [active|pending|all]
/memory pending
/memory show <id>
/memory confirm <id>
/memory reject <id>
/memory edit <id> <新内容>
/memory delete <id>
/memory restore <id>
/memory clear --yes
```

`delete`、`reject` 和 `clear` 都是可恢复的软删除；`restore` 会把指定记录恢复为用户明确确认的 active 记忆。候选证据只来自用户原文，不使用助手回复或工具结果。凭据、API key、Cookie、手机号、证件号、邮箱和精确地址不会被自动保存。

交互输入由 `prompt_toolkit` 接管：汉字等宽字符的退格按显示宽度重绘，不会留下半个字，另外支持 ↑/↓ 调出本次会话的历史和多行粘贴。标准输入或标准输出不是终端时（管道、重定向、测试）自动退回内置 `input`，行为与以前一致。需要注意：uv 管理的 CPython 里 `readline` 是内建 libedit 模块（`sys.builtin_module_names` 里有它，且没有 `__file__`），它按字符数而不是显示宽度计算重绘位置，汉字退格仍会残留，所以不要试图用 `import readline` 修这个问题；后续新增交互提示请统一走 `cli/agent.py` 的 `_read_task`，不要再直接调用 `input`。

每个会话使用独立目录：

```text
privacy/conversations/{session_id}/
├── session.json
└── transcript.md
```

`session.json` 是程序使用的结构化真实数据；`transcript.md` 是每次保存时自动生成的阅读视图，不应手动作为数据源编辑。两个文件权限均设为仅当前用户可读写。`AGENT_SESSION_TTL_SECONDS` 只控制内存缓存时间，不会删除磁盘会话；调用删除会话接口会删除对应的整个会话目录。

Agent 会保留最近的完整对话；上下文接近预算时，会把较早轮次压缩成摘要。每次工具调用保存为独立 EvidenceBatch，内容与实际发送给模型的裁剪结果一致，并受单轮 Evidence 总预算约束。响应中的 `sources` 只包含最终回答实际引用且经过代码校验的视频；同一视频来自多个工具时会合并 `source_tools`。旧轮次被摘要后，其 EvidenceBatch 不再参与后续来源校验。

发给模型的工具证据里，`published_at` 和 `favorited_at` 已由代码统一换算成 UTC+8 文本（例如 `2026-09-12 11:00 (UTC+8)`），模型不需要也不能自行换算。FastAPI 接口返回的时间仍是 UTC 的 ISO 8601 格式。

当前轮次会先以 `pending_turn` 保存。工具执行后立即更新其中的 Evidence 和分页状态；模型调用中断时该轮标记为 `interrupted`，下一次交互仍能看到已经取得的结果。

可以通过环境变量调整上下文策略：

```bash
export AGENT_MAX_OUTPUT_TOKENS=32000
export AGENT_CONTEXT_MAX_UNITS=850000
export AGENT_CONTEXT_SUMMARIZE_AT_UNITS=650000
export AGENT_CONTEXT_RECENT_TURNS=3
export AGENT_CONTEXT_SUMMARY_MAX_TOKENS=8000
export AGENT_CONTEXT_TOOL_RESULT_UNITS=90000
export AGENT_CONTEXT_TURN_EVIDENCE_UNITS=180000
export AGENT_SESSION_TTL_SECONDS=3600
export AGENT_SESSION_CAPACITY=100
```

这里的 `units` 是针对中英文和 JSON 的保守估算单位，不是模型返回的精确 token 数。摘要失败时会使用本地回退摘要；压缩后仍超过输入预算时，本次请求会被拒绝。

## 启动 FastAPI

```bash
uv run fastapi dev src/bili_agent_cli/main.py
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

Agent HTTP 接口第一次调用不传 `session_id`，后续调用复用响应中的 UUID。删除会话：

```bash
curl -X DELETE http://127.0.0.1:8000/agent/sessions/会话UUID
```

获取第一屏关注动态：

```bash
curl http://127.0.0.1:8000/api/following/feed
```

获取下一屏时传入上一屏原始响应中的 `data.offset`：

```bash
curl --get http://127.0.0.1:8000/api/following/feed \
    --data-urlencode 'offset=上一屏的offset'
```

FastAPI 会把 B站的异构动态响应转换为最小视频列表模型，只返回视频动态。输出包含动态 ID、UP主、视频和下一页信息。动态接口没有提供 `cid` 时返回 `null`，进入视频详情后再通过 `bvid` 补齐。

获取指定 UP 主的第一页动态：

```bash
curl http://127.0.0.1:8000/api/users/指定UP主MID/dynamics
```

继续读取时传入上一页返回的 `next_offset`：

```bash
curl --get http://127.0.0.1:8000/api/users/指定UP主MID/dynamics \
    --data-urlencode 'offset=上一页的next_offset'
```

该接口使用 WBI 签名，每次只读取一个上游分页，返回视频、图文、转发等可解析的动态类型。响应中的 `has_more` 和 `next_offset` 用于继续分页，`pages_fetched` 固定为 `1`，`skipped_count` 是当前页缺少动态 ID 或模块结构而无法解析的记录数。Agent 会保存下一页参数：用户只要最新内容时停在当前页，明确要求“更多”或“全部”时再由模型决定继续调用。此读取操作不使用 CSRF，只会发送登录资料中的 `SESSDATA`。

分页获取当前账号关注的 UP 主：

```bash
curl --get http://127.0.0.1:8000/api/following/users \
    --data-urlencode 'page=1' \
    --data-urlencode 'page_size=20' \
    --data-urlencode 'sort=recent'
```

`sort` 可取 `recent`（最近关注）或 `frequent`（最常访问）。响应包含 UP 主资料、关注时间、互关/特别关注状态、认证信息和分页数据。

## 获取收藏夹和收藏视频

获取当前账号创建的收藏夹：

```bash
curl http://127.0.0.1:8000/api/favorites/folders
```

分页获取指定收藏夹中的视频：

```bash
curl --get http://127.0.0.1:8000/api/favorites/folders/收藏夹ID/videos \
    --data-urlencode 'page=1' \
    --data-urlencode 'page_size=20'
```

收藏夹视频响应同时包含 `folder` 和 `videos`；每个视频也带有 `folder_id`，用于明确收藏夹与视频的归属关系。

把视频加入指定收藏夹；名称精确匹配的收藏夹不存在时会先创建：

```bash
curl -X POST http://127.0.0.1:8000/api/favorites/folders/save-videos \
    -H 'Content-Type: application/json' \
    -d '{
      "folder_title": "UP 主动态",
      "bvids": ["BV1xx411c7mD"],
      "privacy": "private"
    }'
```

`privacy` 只在新建收藏夹时生效，可取 `private`（默认）或 `public`。写操作会先解析并校验全部 BVID，再逐条调用收藏接口；每尝试写入 5 个视频固定暂停 2 秒。遇到 B站限频码 `-702` 时，当前视频会按 3、6、12 秒退避重试；仍失败则停止本次写入，将当前及剩余视频交给确认计划的后续重试。请求使用登录 Cookie 中的 `SESSDATA`、`bili_jct` 和 CSRF 表单字段，并通过 `added_count` 和 `retry_videos` 区分已确认成功与待重试的视频。若新建收藏夹成功但添加视频失败，收藏夹会保留，接口返回 HTTP 207 和 `status=partial`；网络中断等无法确认上游是否完成时返回 HTTP 207 和 `status=outcome_unknown`。失败响应中的 `upstream_code` 和 `upstream_message` 保留 B站业务错误，调用方应据此核对收藏夹后再决定是否重试。

Agent 写入使用两个工具以隔离确认步骤：`prepare_save_videos_to_favorite_folder` 只接受当前会话中读取工具真实返回的 `source_id`，完成预检后返回 30 分钟有效的 `confirmation_id`；Agent 必须展示目标收藏夹、视频列表和是否新建，并结束当前回答。只有用户在下一轮明确确认后，才能调用 `commit_save_videos_to_favorite_folder`。确认 ID 绑定预检计划，不能修改目标或视频，完整成功后即失效；若返回 `retryable=true`，用户下一轮要求重试时可以沿用该 ID，程序只重试尚未确认成功的视频。同一用户轮次中的提交仍会被拒绝。

## 获取稍后再看

```bash
curl --get http://127.0.0.1:8000/api/watch-later \
    --data-urlencode 'page=1' \
    --data-urlencode 'page_size=20' \
    --data-urlencode 'ascending=false'
```

稍后再看接口使用 WBI 签名。`published_at` 和 `favorited_at` 会输出为 ISO 8601 时间。

## 获取观看历史

获取最近一页普通视频观看历史：

```bash
curl --get http://127.0.0.1:8000/api/history \
    --data-urlencode 'page_size=20'
```

响应中的 `next_max` 和 `next_view_at` 是下一页所需的双游标：

```bash
curl --get http://127.0.0.1:8000/api/history \
    --data-urlencode 'page_size=20' \
    --data-urlencode 'max=上一页的next_max' \
    --data-urlencode 'view_at=上一页的next_view_at'
```

观看历史只返回普通视频记录。`progress_seconds=-1` 表示已经看完；
`viewed_at` 在 HTTP API 中使用 UTC ISO 8601 时间，在 Agent 上下文中转换为 UTC+8 文本。

## 搜索视频

```bash
curl --get http://127.0.0.1:8000/api/search/videos \
    --data-urlencode 'keyword=Python 教程' \
    --data-urlencode 'page=1' \
    --data-urlencode 'page_size=20' \
    --data-urlencode 'order=totalrank' \
    --data-urlencode 'duration=0'
```

搜索支持排序、时长、内容分区和发布时间范围筛选。接口使用 WBI 签名；如果 B站返回 Gaia 风控凭证，API 会明确提示需要完成人机验证。

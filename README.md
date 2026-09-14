# Bili Agent CLI

一个在命令行中读取、分析和整理 Bilibili 账号内容的 Agent 工具。所有命令都在项目根目录执行。

## 登录与资料

扫码登录：

```bash
uv run bili-agent-cli login
```

终端会显示二维码。使用哔哩哔哩手机客户端扫码并确认后，Cookie 会写入项目根目录下的 `secret/profile.txt`。该文件已加入 Git 忽略，权限仅当前用户可读写。

检查登录资料：

```bash
uv run bili-agent-cli profile
```

该命令只输出 Cookie 字段是否齐全和当前用户 ID，不输出 Cookie 值。

## 调试 B 站 GET 接口

```bash
uv run bili-agent-cli get /x/web-interface/nav
uv run bili-agent-cli get /x/web-interface/view --param bvid=BV1xx411c7mD
```

多个查询参数可以重复传入 `--param`。调试入口只允许访问 `https://api.bilibili.com` 下的绝对路径，只发送 `SESSDATA`，不会输出请求 Cookie。

## 启动 Agent 对话

```bash
uv run bili-agent-cli agent
```

对话中支持：

```text
/new       开始新会话
/context   查看当前会话上下文
/memory    管理长期记忆
/persona   查看或刷新用户画像
/exit      退出
```

会话持久化到 `privacy/conversations/`，每个会话包含结构化的 `session.json` 和仅供阅读的 `transcript.md`。会话文件权限仅当前用户可读写。

Agent 可以用自然语言：

- 读取关注动态和指定 UP 主动态。
- 查看关注用户、收藏夹、稍后再看和观看历史。
- 按关键词、排序、时长、分区和时间范围搜索视频。
- 按 `bvid + cid` 读取具体分 P 的 Bilibili 字幕，并按 cue 分页分析。
- 分析用户画像，并将已读取的可信视频保存到收藏夹。

分页工具会在会话中保留下一页游标。用户明确要求“更多”或“全部”时，Agent 才会继续翻页。

收藏写入使用预检与提交两个工具隔离确认步骤。Agent 必须先展示目标收藏夹和视频计划，只有用户在下一轮明确确认后才能写入。

## 统一视频数据与内容数据库

关注动态、指定用户动态、收藏夹、稍后再看、观看历史和搜索仍各自解析 B 站响应，但其中的视频在进入 Agent 前统一转换为 `VideoRecord`：

```text
VideoRecord
├── identity       bvid、cid，以及包含二者的 source_id
├── author         mid、name、avatar_url
├── detail         aid、标题、简介、封面、时长、发布时间、是否默认分 P
├── feedback       播放、弹幕、收藏、评论、点赞数
└── contexts       favorite、watch_later、dynamic、history、search
```

视频身份使用 `(bvid, cid)` 复合键，新 `source_id` 格式为 `bilibili:video:{bvid}:part:{cid}`。旧会话保存的 `bilibili:video:{bvid}` 仍可读取和用于已有确认流程。列表缺少 CID 时，程序先查询已确认的默认 CID，否则调用视频详情接口；多 P 视频只绑定详情返回的默认 CID，不自动展开全部分 P。

字段缺失使用 `null`，含义是“当前来源没有提供或尚未确认”，不是空字符串或零。例如收藏日期只存在于收藏上下文，其他来源的 `contexts.favorite` 为 `null`；计数 `0` 是有效数据。重复读取时，新 `null` 不会覆盖数据库中已有的非空值。

Agent 返回的发布时间、收藏时间、动态发布时间、观看时间和数据观测时间统一转换为北京时间，格式如 `2026-09-14 16:00 (UTC+8)`。数据库内部仍使用带时区的 UTC 时间，读取结果序列化时再转换，避免存储和排序产生时区歧义。

统一核心数据保存在 `privacy/content.db`，与 `privacy/memory.db` 分离。`videos` 保存视频本体，来源特有信息分别保存在收藏夹关系、稍后再看、动态、历史和搜索表中，因此同一视频可以同时属于多个收藏夹或来源。数据库目录权限为 `0700`，文件权限为 `0600`，不应提交或分享。

除字幕外，当前策略是“读取即入库”：每次 Agent 读取都先实时请求 B 站，再执行规范化、CID 补全和事务写入，最后返回结果。当前不会定时刷新、不会新增后台同步任务，也不会为了回答“今天的动态”等请求优先读取旧数据库。单个视频无法补全 CID 时，其余视频仍会入库并返回 `status=partial` 和 `skipped_count`，失败项会记录在 `ingestion_failures` 供后续排查。

字幕采用文档缓存策略：`subtitle_documents` 以 `(bvid, cid, language, source)` 标识字幕轨道，保存来源、内容哈希、可用语言和抓取时间；`subtitle_cues` 逐条保存规范化后的开始时间、结束时间与文本。默认缓存有效期为 24 小时，也可强制刷新。工具一次最多向 Agent 返回 200 条 cue，并通过 `next_offset` 继续读取，避免把完整长字幕重复塞进模型上下文。字幕是可重新抓取的内容事实，不会写入 `memory.db`；只有当前回答所需的分页证据会进入会话上下文。

数据库还保存画像所需的行为事件、视频主题分类状态和偏好分数。画像刷新只分析已经入库的数据，不会暗中请求 B 站或启动后台同步；实时内容仍由对应读取工具获取并顺便入库。

## 用户画像

画像由两类信号合并：`memory.db` 中用户明确表达的偏好、约束和目标，以及 `content.db` 中收藏、稍后再看、观看历史和搜索产生的行为证据。代码负责事件去重、时间衰减、聚合和打分；DeepSeek 只负责把视频元数据和搜索词映射到固定主题，并给出置信度。

```bash
uv run bili-agent-cli persona show
uv run bili-agent-cli persona refresh --max-new-videos 60
```

交互式 Agent 中可使用 `/persona` 和 `/persona refresh`。Agent 也能按需调用 `get_user_profile`；`refresh=false` 只读取最近快照，`refresh=true` 增量分类变化或尚未分类的视频。画像刷新当前仍只使用标题、简介、UP 主、时长和收藏夹名称，不会自动读取字幕；字幕工具用于用户明确要求的视频内容分析，未来可作为高评分视频的第二阶段证据。

评分、置信度、数据表和调用流程详见 [`docs/persona.md`](docs/persona.md)。

## 长期记忆

长期记忆保存在 `privacy/memory.db`。用户明确表达的长期偏好会直接生效；从一次任务中推断的偏好先进入 `pending`，由两个不同轮次支持后才会自动激活。只有与当前主题或任务意图匹配的 active 记忆会加入模型上下文。

详细架构、状态机、去重、召回和安全边界见 [`docs/memory.md`](docs/memory.md)。

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

`delete`、`reject` 和 `clear` 都是可恢复的软删除。凭据、API key、Cookie、手机号、证件号、邮箱和精确地址不会被自动保存。

## 上下文配置

Agent 保留最近的完整对话，接近输入预算时将较早轮次压缩成摘要。工具证据、实际引用来源和未完成轮次均会持久化。

可通过环境变量调整上下文预算：

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

`units` 是针对中英文和 JSON 的保守估算单位，不是模型返回的精确 token 数。

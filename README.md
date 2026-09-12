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

对话过程中输入 `/new` 开始新会话，输入 `/context` 查看当前 Session 保存的摘要、轮次、工具证据、实际引用来源和未完成轮次，输入 `/exit` 退出。会话会持久化到 `privacy/conversations/`；Agent HTTP 接口在程序重启后仍可传入原 `session_id` 载入。CLI 暂未提供 `/resume` 选择入口。

每个会话使用独立目录：

```text
privacy/conversations/{session_id}/
├── session.json
└── transcript.md
```

`session.json` 是程序使用的结构化真实数据；`transcript.md` 是每次保存时自动生成的阅读视图，不应手动作为数据源编辑。两个文件权限均设为仅当前用户可读写。`AGENT_SESSION_TTL_SECONDS` 只控制内存缓存时间，不会删除磁盘会话；调用删除会话接口会删除对应的整个会话目录。

Agent 会保留最近的完整对话；上下文接近预算时，会把较早轮次压缩成摘要。每次工具调用保存为独立 EvidenceBatch，内容与实际发送给模型的裁剪结果一致，并受单轮 Evidence 总预算约束。响应中的 `sources` 只包含最终回答实际引用且经过代码校验的视频；同一视频来自多个工具时会合并 `source_tools`。旧轮次被摘要后，其 EvidenceBatch 不再参与后续来源校验。

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

## 获取收藏夹和收藏视频

获取当前账号创建的收藏夹：

```bash
curl http://127.0.0.1:8000/api/favorites/folders
```

分页获取指定收藏夹中的视频：

```bash
curl --get http://127.0.0.1:8000/api/favorites/folders/167862349/videos \
    --data-urlencode 'page=1' \
    --data-urlencode 'page_size=20'
```

收藏夹视频响应同时包含 `folder` 和 `videos`；每个视频也带有 `folder_id`，用于明确收藏夹与视频的归属关系。

## 获取稍后再看

```bash
curl --get http://127.0.0.1:8000/api/watch-later \
    --data-urlencode 'page=1' \
    --data-urlencode 'page_size=20' \
    --data-urlencode 'ascending=false'
```

稍后再看接口使用 WBI 签名。`published_at` 和 `favorited_at` 会输出为 ISO 8601 时间。

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

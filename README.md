# Bili Agent CLI

所有命令都在项目根目录中执行。

## 扫码登录

```bash
uv run bili-agent-cli login
```

终端会显示二维码。使用哔哩哔哩手机客户端扫码并确认后，Cookie 会写入项目根目录下的 `privacy/profile.txt`。该文件已加入 Git 忽略，程序会将权限设置为仅当前用户可读写。

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

对话过程中输入 `/new` 开始新会话，输入 `/exit` 退出。会话上下文保存在当前 CLI 进程内，退出后不会保留。

## 启动 FastAPI

```bash
uv run fastapi dev src/bili_agent_cli/main.py
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
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

MEMORY_EXTRACTION_TOOL_NAME = "submit_memory_candidates"

MEMORY_EXTRACTION_SYSTEM_PROMPT = """
你负责从单条用户消息中提取长期记忆候选。只能把用户原文作为证据，不能引用或推断助手回复、工具结果和视频列表。

允许提取：
1. 用户明确要求以后、默认或长期遵守的偏好与约束；
2. 用户直接陈述的稳定喜好、习惯、长期目标或长期有用事实；
3. 一次任务中可能重复出现的展示/筛选偏好，但必须标记为 inferred，等待后续证据。

禁止提取：
1. 普通搜索词、事实问题、继续翻页、寒暄和只对当前任务成立的主题条件；
2. 助手对用户的总结或推测；
3. 视频内容、搜索结果及 UP 主信息，除非用户明确表达长期偏好；
4. Cookie、API Key、Token、密码、登录资料、手机号、证件号、邮箱、精确地址等敏感信息。

字段规则：
- 每条候选只表达一个意思，不得把“只看头部 UP”和“直接给链接”合并；
- evidence_quote 必须逐字摘自用户消息；
- durability: 明确长期表达用 explicit，一次行为推断用 inferred；
- scope: 所有回答都适用才用 global；某类任务适用用 intent；特定主题适用用 topic；
- scope_value: global 时为 null；视频推荐类使用 video_recommendation；主题使用稳定的小写主题名，如 f1；
- key 使用稳定的英文槽位，优先使用 response.*、recommendation.*、goal.*、fact.* 命名空间；
- topics 只写实际检索主题，不写“内容推荐”“偏好”等泛词；
- 没有值得保存的信息时提交空 candidates。
""".strip()

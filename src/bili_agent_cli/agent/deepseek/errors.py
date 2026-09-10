class ModelCallError(Exception):
    """所有模型调用相关错误的根异常。"""

class ConfigurationError(ModelCallError):
    """本地配置错误，例如缺少 API Key。"""

class ProviderTimeoutError(ModelCallError):
    """请求超过时间预算。"""

class ProviderNetworkError(ModelCallError):
    """DNS、连接失败、连接中断等网络错误。"""

class ProviderHTTPError(ModelCallError):
    """供应商返回了非成功 HTTP 状态码。"""

    def __init__(
            self,
            status_code: int,
            category: str,
    ) -> None:
        self.status_code = status_code
        self.category = category

        super().__init__(
            f"provider error: category={category},"
            f"status_code={status_code}"
        )

class ProviderResponseError(ModelCallError):
    """HTTP 成功，但响应 JSON 或结构不符合预期。"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。所有值来自环境变量(前缀 CYSH_)或 .env,密钥不设默认值。"""

    model_config = SettingsConfigDict(env_prefix="CYSH_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://cyshuhe:change-me@localhost:5432/cyshuhe"
    db_pool_size: int = 25
    db_max_overflow: int = 35
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800
    jwt_secret: str = Field(min_length=32)
    jwt_ttl_minutes: int = 60
    portal_sso_secret: str | None = None
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
    ]
    # 部署在反向代理之后时，填代理的地址；审计记录才会采用其 X-Forwarded-For 里的客户端 IP
    trusted_proxies: list[str] = []

    # 采集:原文归档目录、礼貌抓取参数
    archive_dir: str = "./data/archive"
    crawl_user_agent: str = (
        "CheyanShuheBot/0.1 (+compliance research; contact: set CYSH_CRAWL_CONTACT)"
    )
    crawl_delay_seconds: float = 2.0
    crawl_timeout_seconds: float = 30.0
    crawl_max_bytes: int = 100 * 1024 * 1024
    worker_poll_seconds: int = 60
    # OCR(扫描件):tesseract 可执行文件与语言包目录;不配置则尝试 PATH,找不到就只标记"需要 OCR"
    tesseract_cmd: str | None = None
    tessdata_dir: str | None = None
    ocr_dpi: int = 300
    # 独立 Python 环境里的解释器（装有 paddleocr）。
    # 配置后阿拉伯文字文档优先走 PaddleOCR，未配置则只用 Tesseract
    paddle_python: str | None = None
    # 允许「域名解析结果」落入的内网网段(默认空=一律拦截)。仅用于开发机:
    # 代理软件的 fake-IP 模式会把所有域名解析到 198.18.0.0/15,此时可设为 ["198.18.0.0/15"]
    crawl_allow_resolved_cidrs: list[str] = []

    # 可插拔大模型适配层(OpenAI 兼容接口);未配置密钥时使用本地规则与术语引擎
    llm_api_key: str | None = None
    llm_redaction: Literal["on", "off"] = "on"
    citation_policy: Literal["allow_machine_with_notice", "reviewed_only"] = (
        "allow_machine_with_notice"
    )
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-flash"
    llm_timeout_seconds: float = 90.0
    llm_translation_thinking: Literal["enabled", "disabled"] = "disabled"
    llm_kg_extraction_thinking: Literal["enabled", "disabled"] = "disabled"
    # 多智能体合规分析单次调用允许更长的生成时间
    llm_analysis_timeout_seconds: float = 150.0
    # 咨询层:在已核验证据之上写结论、差距、风险与建议;关闭后只生成证据版报告
    consulting_enabled: bool = True
    # 旧的“逐项问齐信息→生成八维度评估报告”对话。已由“生成合规文件 + 咨询助手 + 国家合规简报”取代，
    # 用户端不再生成；保留开关只为评测脚本复现指标（2.4）
    assessment_chat_enabled: bool = False
    # 译文语义核验：模型对齐原文与译文并标注规范类型，规则裁决；
    # 失败的译文自动重译，仍不过则不进入引用
    translation_judge_enabled: bool = True
    # 语义核验是否让模型先推理再标注（enabled 更准但更慢）；默认关闭，由校准结果决定
    llm_judge_thinking: str = "disabled"
    # 入库把关：判定文件是不是法律文书，非法律资料不进入法律库
    legal_nature_screening_enabled: bool = True
    # 咨询版报告的导出是否带“待核对/机翻/复核状态”类提示；现阶段只关注流程与报告质量，默认不显示
    report_review_notices: bool = False

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url 必须使用 postgresql+asyncpg:// 驱动")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

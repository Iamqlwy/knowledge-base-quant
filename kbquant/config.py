from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = ""
    database_read_url: str = ""
    database_url_sync: str = ""
    api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "Qwen/Qwen3-Embedding-4B"
    embedding_dimension: int = 1536
    embedding_max_concurrent: int = 2000
    embedding_rpm: int = 2000
    embedding_batch_size: int = 50
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index_prefix: str = "quant_kb"
    pipeline_worker_enabled: bool = True
    pipeline_worker_poll_interval: float = 0.1
    pipeline_worker_batch_size: int = 200
    pipeline_worker_max_concurrency: int = 64
    pipeline_matcher_max_workers: int = 64
    llm_model: str = "deepseek-v4-flash"
    preference_rewrite_threshold: int = 5
    log_level: str = "INFO"
    log_dir: str = "logs"
    log_file: str = "kbquant_api.log"
    log_file_max_bytes: int = 10 * 1024 * 1024
    log_file_backup_count: int = 5
    # Concurrency / connection pool
    # 配合 pgbouncer transaction 模式使用，app 侧池子连接到 pgbouncer
    # pgbouncer 再复用少量 PG 真实连接，所以 app 池可以开大
    database_pool_size: int = 30
    database_max_overflow: int = 10
    database_read_pool_size: int = 50
    database_read_max_overflow: int = 10
    database_bg_pool_size: int = 3
    database_bg_max_overflow: int = 2    
    database_pool_timeout: int = 30
    database_command_timeout: int = 30
    database_connect_timeout: int = 10
    uvicorn_workers: int = 4
    es_connections_per_node: int = 50
    es_request_timeout: float = 10.0
    es_max_retries: int = 2
    es_search_max_concurrent: int = 100
    embedding_global_limiter_enabled: bool = False
    embedding_global_limiter_name: str = "kbquant_embedding_api"
    embedding_global_limiter_timeout: float = 300.0
    embedding_batch_wait: float = 0.05
    embedding_batch_worker_count: int = 4
    embedding_cache_maxsize: int = 16384
    llm_cache_maxsize: int = 2048
    llm_max_concurrent: int = 50
    # Per uvicorn worker. Extra requests wait in middleware instead of
    # opening DB/ES connections immediately.
    search_max_concurrent: int = 200
    search_recall_limit_max: int = 200
    search_queue_timeout: float = 25.0
    admission_timeout: float = 5.0
    admission_max_concurrent: int = 1000
    background_task_max_concurrent: int = 200
    # 启动时预热 embedding 缓存的常用查询词
    embedding_warmup_queries: list[str] = [
        "美联储加息", "通货膨胀", "GDP增长", "失业率", "货币政策",
        "财政政策", "经济衰退", "股市行情", "债券收益率", "汇率波动",
        "原油价格", "黄金价格", "大宗商品", "贸易逆差", "消费者信心",
        "房地产市场", "科技股", "银行业", "制造业PMI", "零售销售",
    ]

    # Rerank API
    rerank_model: str = "qwen3-rerank"
    rerank_api_url: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    rerank_timeout_seconds: float = 2.0
    rerank_max_queue_depth: int = 50
    siliconflow_api_key: str = ""
    gitee_api_key: str = ""
    dashscope_api_key: str = ""

    # Snippet
    snippet_reranker_max_length: int = 512
    snippet_display_max_length: int = 200

    # Hard Filter
    vector_low_threshold: float = 0.3
    keyword_coverage_min_count: int = 3
    demote_multiplier: float = 0.5

    # Feature flags
    feature_flags: dict = {
        "entity_resolver": True,
        "query_rewriter": True,
        "hard_filter": True,
        "rerank": True,
        "rerank_fallback_on_error": True,
        "dynamic_weights": True,
        "empty_content_filter": True,
        "reranker_threshold_filter": True,
        "analysis_query_type_boost": True,
        "market_region_boost": True,
    }

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

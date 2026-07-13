"""Application settings, loaded from environment (and `.env` locally)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Postgres ---
    postgres_user: str = "rag"
    postgres_password: str = "rag_dev_password"
    postgres_db: str = "handbook"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # --- Generation provider (DeepSeek cloud API) ---
    # The one cloud call in the system; embeddings stay local. Swappable behind
    # the app.provider interface — point base_url/model at any OpenAI-compatible
    # endpoint. Empty key means generation is unconfigured: /ask still retrieves
    # and can refuse, but a non-refused answer will error until a key is set.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Token pricing (USD per 1M tokens) used only to estimate per-query cost for
    # query_logs. Defaults track DeepSeek's published chat rates; override via env
    # if they change or the model swaps. Wrong values mis-report cost, nothing more.
    deepseek_price_input_per_mtok: float = 0.27
    deepseek_price_output_per_mtok: float = 1.10

    # --- Retrieval / refusal ---
    # How many fused chunks to hand the generator as grounding context.
    retrieval_top_k: int = 5
    # Below this fused RRF score for the top chunk, skip generation and refuse
    # with the nearest matches. Placeholder — tuned against the golden set in
    # Phase 5. Reference RRF math (k=60): both retrievers ranking a chunk #1
    # scores ~0.033; a single retriever's lone #1 scores ~0.0164. Observed
    # in-domain questions land ~0.031 (both halves agree near the top) while an
    # out-of-domain probe ("capital of France") scored 0.0164 (one weak lexical
    # hit). This floor sits in that gap so a lone weak match refuses; Phase 5
    # trades precision vs recall against real labels.
    refusal_threshold: float = 0.02

    @property
    def database_url(self) -> str:
        """DSN for asyncpg / SQLAlchemy (async driver added where needed)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

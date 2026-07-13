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
    # with the nearest matches. Tuned against the golden set in Phase 5
    # (`scripts/eval.py` threshold sweep). Reference RRF math (k=60): both
    # retrievers ranking a chunk #1 scores ~0.033; a single retriever's lone #1
    # scores ~0.0164. The sweep's balanced-accuracy optimum was 0.0306 (keeps
    # 26/28 answerable, refuses 4/4 off-corpus), but that buys the last off-corpus
    # refusal ("what ATAR to study computing at UNSW" — lexically/semantically
    # overlaps CS content, ~0.0305) at the cost of two genuinely-answerable
    # questions. We sit just under it at 0.030 to keep answerable recall at 28/28
    # and refuse 3/4 clean off-corpus; the citation-enforcing prompt is the second
    # net for the residual course-named-but-missing-attribute questions (measured
    # 2/2 correct declines in the groundedness eval). See eval/results.md.
    refusal_threshold: float = 0.030

    @property
    def database_url(self) -> str:
        """DSN for asyncpg / SQLAlchemy (async driver added where needed)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

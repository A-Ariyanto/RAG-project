"""Phase 0 hello-world: connect to Postgres and confirm pgvector is available.

Run inside the app container:
    docker compose exec app python -m scripts.healthcheck
"""

import asyncio

import asyncpg

from app.config import settings


async def main() -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        version = await conn.fetchval("SELECT version();")
        print(f"Connected to Postgres @ {settings.postgres_host}:{settings.postgres_port}")
        print(f"  {version}")

        # Enable the extension (idempotent) and report its version.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        ext = await conn.fetchrow(
            "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
        )
        print(f"pgvector extension enabled: {ext['extname']} v{ext['extversion']}")

        # Sanity-check the vector type actually works.
        distance = await conn.fetchval("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector;")
        print(f"vector type OK (sample L2 distance = {distance})")
    finally:
        await conn.close()

    print("Healthcheck passed ✅")


if __name__ == "__main__":
    asyncio.run(main())

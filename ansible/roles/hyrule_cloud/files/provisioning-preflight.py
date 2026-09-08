"""Read-only deployment gate. Never print database credentials or guest rows."""
import asyncio


async def check() -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from hyrule_cloud.config import HyruleConfig

    engine = create_async_engine(HyruleConfig().database_url, poolclass=NullPool)
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("production gate requires PostgreSQL")
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            exists = await connection.scalar(text("SELECT to_regclass('public.vms') IS NOT NULL"))
            if not exists:
                tables = await connection.scalar(text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
                ))
                if tables:
                    raise ValueError("existing schema lacks VM table")
                print("OK: empty database for first installation")
                return 0
            count = await connection.scalar(text("SELECT count(*) FROM vms WHERE status='provisioning'"))
            if count:
                print(f"BLOCKED: {count} provisioning attempt(s); services remain held")
                return 2
            print("OK: no in-flight provisioning attempts")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    try:
        return asyncio.run(asyncio.wait_for(check(), 15))
    except Exception:
        print("ERROR: provisioning preflight unavailable; services remain held")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

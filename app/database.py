from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/music_search"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)
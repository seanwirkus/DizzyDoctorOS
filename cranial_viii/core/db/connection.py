"""
Database Connection & Migration Manager for CranialVIII
"""

import os
from pathlib import Path
from typing import Generator
try:
    import psycopg2
    from psycopg2.extensions import connection as PgConnection
except ImportError:
    psycopg2 = None
    PgConnection = None

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except ImportError:
    create_engine = None
    Session = None
    engine = None
    SessionLocal = None


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_raw_connection() -> PgConnection:
    """Returns raw psycopg2 connection for high-throughput COPY / bulk inserts."""
    return psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD
    )


def run_migrations():
    """Applies all .sql migration files in sequential order."""
    migrations_dir = Path(__file__).parent / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))
    
    conn = get_raw_connection()
    conn.autocommit = True
    cur = conn.cursor()
    
    # Create migration tracking table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(255) PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    
    for f in sql_files:
        version = f.name
        cur.execute("SELECT 1 FROM schema_migrations WHERE version = %s;", (version,))
        if cur.fetchone():
            continue
        
        print(f"Applying migration: {version}")
        sql_content = f.read_text(encoding="utf-8")
        cur.execute(sql_content)
        cur.execute("INSERT INTO schema_migrations (version) VALUES (%s);", (version,))
        print(f"Applied: {version}")
        
    cur.close()
    conn.close()

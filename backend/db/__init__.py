# Copyright (c) 2025 Efstratios Goudelis
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for details.

import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from common.arguments import arguments


# sql alchemy engine
def _build_database_url(db_path: str) -> str:
    if os.path.isabs(db_path):
        return f"sqlite+aiosqlite:///{os.path.abspath(db_path)}"
    return f"sqlite+aiosqlite:///./{db_path}"


DATABASE_URL = _build_database_url(arguments.db)

# Pool sizing. The main API process needs a larger pool; forked worker
# processes (SpyServer/FFT/Tracker) only touch the DB occasionally and must not
# hold a big pool on the shared SQLite file (idle connections cause WAL write
# contention and pool starvation).
_MAIN_POOL_SIZE = 10
_MAIN_MAX_OVERFLOW = 20
_WORKER_POOL_SIZE = 1
_WORKER_MAX_OVERFLOW = 2


def _apply_sqlite_pragmas(async_engine) -> None:
    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            for statement in (
                "PRAGMA busy_timeout = 30000",
                "PRAGMA journal_mode = WAL",
                "PRAGMA synchronous = NORMAL",
            ):
                try:
                    cursor.execute(statement)
                except Exception:
                    pass
        finally:
            cursor.close()


def _create_engine(database_url, pool_size=_MAIN_POOL_SIZE, max_overflow=_MAIN_MAX_OVERFLOW):
    async_engine = create_async_engine(
        database_url,
        echo=False,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=3600,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
    )
    _apply_sqlite_pragmas(async_engine)
    return async_engine


# Capture the main process pid at import time. Forked workers get a different
# pid and lazily build their own small engine + sessionmaker so they never
# inherit/share the parent's connection pool across fork.
_MAIN_PID = os.getpid()


class _PerProcessSessionLocal:
    def __init__(self):
        self._cache: dict = {}

    def _sessionmaker(self):
        pid = os.getpid()
        cached = self._cache.get(pid)
        if cached is None:
            if pid == _MAIN_PID:
                pool_size = _MAIN_POOL_SIZE
                max_overflow = _MAIN_MAX_OVERFLOW
            else:
                pool_size = _WORKER_POOL_SIZE
                max_overflow = _WORKER_MAX_OVERFLOW
            cached = async_sessionmaker(
                bind=_create_engine(DATABASE_URL, pool_size=pool_size, max_overflow=max_overflow),
                expire_on_commit=False,
                class_=AsyncSession,
            )
            self._cache[pid] = cached
        return cached

    def __call__(self, *args, **kwargs):
        return self._sessionmaker()(*args, **kwargs)


engine = _create_engine(DATABASE_URL, pool_size=_MAIN_POOL_SIZE, max_overflow=_MAIN_MAX_OVERFLOW)

AsyncSessionLocal = _PerProcessSessionLocal()


def create_subprocess_engine():
    return _create_engine(DATABASE_URL, pool_size=_WORKER_POOL_SIZE, max_overflow=_WORKER_MAX_OVERFLOW)

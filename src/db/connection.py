from dataclasses import dataclass
from threading import Lock
from urllib.parse import unquote, urlparse

import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool


@dataclass(frozen=True)
class MySQLConfig:
    user: str
    password: str
    host: str
    port: int
    database: str


def parse_mysql_url(database_url: str) -> MySQLConfig:
    normalized = database_url.replace("mysql+mysqlconnector://", "mysql://", 1)
    parsed = urlparse(normalized)
    return MySQLConfig(
        user=parsed.username or "",
        password=unquote(parsed.password or ""),
        host=parsed.hostname or "localhost",
        port=parsed.port or 3306,
        database=(parsed.path or "").lstrip("/"),
    )


_pools: dict = {}
_pools_lock = Lock()


def _get_pool(config: MySQLConfig) -> MySQLConnectionPool:
    key = (config.host, config.port, config.database, config.user)
    with _pools_lock:
        if key not in _pools:
            _pools[key] = MySQLConnectionPool(
                pool_name=f"pool_{config.host}_{config.database}",
                pool_size=10,
                pool_reset_session=True,
                user=config.user,
                password=config.password,
                host=config.host,
                port=config.port,
                database=config.database,
            )
        return _pools[key]


def connect_mysql(config: MySQLConfig):
    """Return a pooled connection. Calling .close() returns it to the pool."""
    return _get_pool(config).get_connection()

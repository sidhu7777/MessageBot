from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import mysql.connector


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


def connect_mysql(config: MySQLConfig):
    return mysql.connector.connect(
        user=config.user,
        password=config.password,
        host=config.host,
        port=config.port,
        database=config.database,
    )

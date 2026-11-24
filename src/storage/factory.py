import sqlite3
from contextlib import ExitStack
from langgraph.checkpoint.sqlite import SqliteSaver
from src.config.settings import Settings
from src.storage.sqlite_storage import SQLiteStorage

def create_storage(settings: Settings):
    if settings.storage_backend=='postgres':
        from src.storage.postgres_storage import PostgresStorage
        storage = PostgresStorage(settings.database_url.get_secret_value())
    else:
        storage = SQLiteStorage(settings.data_dir/'runs.sqlite')
    from src.services.research_budget import micros
    storage.monthly_ceiling = micros(settings.max_monthly_usd)
    return storage

def create_checkpointer(settings: Settings,stack: ExitStack):
    if settings.storage_backend=='postgres':
        from langgraph.checkpoint.postgres import PostgresSaver
        from src.storage.postgres_storage import secure_dsn
        saver=stack.enter_context(PostgresSaver.from_conn_string(secure_dsn(settings.database_url.get_secret_value())))
        saver.setup()
        return saver
    settings.data_dir.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(settings.data_dir/'checkpoints.sqlite',check_same_thread=False,timeout=30)
    stack.callback(connection.close)
    connection.execute('PRAGMA journal_mode=WAL')
    return SqliteSaver(connection)

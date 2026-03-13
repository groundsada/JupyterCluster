"""Database utilities for JupyterCluster.

Follows JupyterHub's dbutil.py pattern:
- new_session_factory() creates an engine + sessionmaker with consistent defaults
- upgrade() wraps alembic to apply pending migrations
- _temp_alembic_ini() generates an alembic.ini on-the-fly (no checked-in ini needed)
"""

import logging
import os
import tempfile
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# Absolute path to the alembic/ directory next to this file
ALEMBIC_DIR = os.path.join(os.path.dirname(__file__), "alembic")

# Minimal alembic.ini template — db URL and script location are substituted at runtime
_ALEMBIC_INI_TEMPLATE = """\
[alembic]
script_location = {alembic_dir}
sqlalchemy.url = {db_url}

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = WARN
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""


@contextmanager
def _temp_alembic_ini(db_url: str):
    """Context manager that writes a temporary alembic.ini and yields its path.

    Mirrors JupyterHub's approach of generating the ini dynamically so that
    alembic.ini does not need to be checked in or shipped with the package.
    """
    # Escape % signs that would be misinterpreted by ConfigParser
    safe_url = str(db_url).replace("%", "%%")
    content = _ALEMBIC_INI_TEMPLATE.format(
        alembic_dir=ALEMBIC_DIR,
        db_url=safe_url,
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write(content)
        fname = f.name
    try:
        yield fname
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass


def upgrade(db_url: str, revision: str = "head") -> None:
    """Run ``alembic upgrade <revision>`` against *db_url*.

    Mirrors JupyterHub's ``dbutil.upgrade()``.  Generates a temporary
    alembic.ini so callers do not need to manage configuration files.
    """
    from subprocess import CalledProcessError, check_call

    logger.info("Running database migrations (alembic upgrade %s)…", revision)
    with _temp_alembic_ini(db_url) as alembic_ini:
        try:
            check_call(["alembic", "-c", alembic_ini, "upgrade", revision])
        except CalledProcessError as e:
            raise RuntimeError(
                f"Alembic upgrade failed (exit {e.returncode}). "
                "Check the database URL and migration scripts."
            ) from e
    logger.info("Database migrations complete.")


def new_session_factory(url: str = "sqlite:///:memory:", **kwargs):
    """Create a SQLAlchemy engine and session factory.

    Follows JupyterHub's ``orm.new_session_factory()`` conventions:

    * ``expire_on_commit=False`` — avoids redundant SELECTs in a single-process
      app where no other writer can change rows between commits.
    * ``pool_pre_ping=True`` — pessimistic disconnect handling; transparently
      reconnects stale pooled connections (equivalent to JupyterHub's
      ``register_ping_connection()``).
    * SQLite gets ``check_same_thread=False`` so the async Tornado event loop
      can use the same connection the sync session was opened on.
    * MySQL gets ``pool_recycle=60`` to avoid "MySQL server has gone away".
    """
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    elif url.startswith("mysql"):
        kwargs.setdefault("pool_recycle", 60)

    kwargs.setdefault("pool_pre_ping", True)

    engine = create_engine(url, **kwargs)
    # expire_on_commit=False: mirrors JupyterHub — single process, no concurrent writers
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, session_factory

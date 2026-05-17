from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Alembic config object
config = context.config

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import app settings so we can override the sqlalchemy.url from .env
from app.config import settings  # noqa: E402

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Import Base + all models so autogenerate can detect them
from app.database import Base  # noqa: E402
import app.models.user          # noqa: F401, E402
import app.models.member        # noqa: F401, E402
import app.models.event         # noqa: F401, E402
import app.models.trial         # noqa: F401, E402
import app.models.event_pdf     # noqa: F401, E402
import app.models.registration  # noqa: F401, E402
import app.models.dog           # noqa: F401, E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER support
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER support
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

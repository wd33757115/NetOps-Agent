from alembic import context

config = context.config
context.configure(
    connection=config.attributes["connection"],
    version_table=config.attributes["version_table"],
    transactional_ddl=True,
)
with context.begin_transaction():
    context.run_migrations()

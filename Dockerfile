FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7 AS resolver
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHON_DOTENV_DISABLED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends mariadb-client tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-container.txt requirements-docker.lock ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-container.txt -c requirements-docker.lock && pip check

FROM resolver AS runtime
RUN groupadd --gid 10001 conejo && useradd --uid 10001 --gid 10001 --no-create-home conejo \
    && mkdir -p /app/instance /app/uploads && chown -R conejo:conejo /app/instance /app/uploads
COPY app/ ./app/
COPY docker_support/ ./docker_support/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY scripts/migrate_historical_imports.py scripts/migrate_reconciliation_cuts.py scripts/migrate_operational_separation.py scripts/migrate_tutor_cycle.py scripts/run_scheduled_backup.py scripts/run_scheduled_reconciliation.py scripts/take_inventory_snapshot.py ./scripts/
COPY scripts/migrate_replenishment_source_order.py scripts/purchase_e2e_support.py scripts/test_demo_scope_20261005.py scripts/test_historical_invoice_reference.py scripts/test_legacy_suites_vigente_20261017.py scripts/test_purchase_followup_20261017.cjs scripts/test_purchase_followup_20261017.py scripts/test_purchase_quote_selection_vigente_20261017.cjs scripts/test_purchase_tutor_20261005.cjs scripts/test_purchase_tutor_20261005.py scripts/test_purchase_tutor_concurrency.py scripts/migrate_purchase_orders.py ./scripts/
USER 10001:10001
EXPOSE 5000
CMD ["python", "-m", "docker_support.start"]

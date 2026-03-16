from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from notifier.notifications_template import (
    get_failure_notifier,
    get_start_notifier,
    get_success_notifier,
)

from database import init_graph_schema, populate_graph_from_postgres

default_args = {
    "owner": "airflow",
    "start_date": datetime(2025, 8, 1),
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    "KNOWLEDGE_GRAPH",
    default_args=default_args,
    schedule=None,
    catchup=False,
    max_active_runs=1,
    description=(
        "FalkorDB knowledge graph initialisation and back-fill DAG. "
        "Initialises the graph schema and populates it from data already "
        "stored in PostgreSQL (LEGI, JADE, BOFIP)."
    ),
    tags=["mediatech", "knowledge_graph", "falkordb", "graphrag"],
) as dag:
    init_schema = PythonOperator(
        task_id="init_graph_schema",
        python_callable=init_graph_schema,
        on_execute_callback=get_start_notifier(),
        on_success_callback=get_success_notifier(),
        on_failure_callback=get_failure_notifier(),
    )

    backfill_graph = PythonOperator(
        task_id="populate_graph_from_postgres",
        python_callable=populate_graph_from_postgres,
        on_execute_callback=get_start_notifier(),
        on_success_callback=get_success_notifier(),
        on_failure_callback=get_failure_notifier(),
    )

    init_schema >> backfill_graph

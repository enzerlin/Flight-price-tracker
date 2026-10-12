from datetime import datetime
import pendulum

from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator


with DAG(
    dag_id="flight_price_tracker",
    start_date=pendulum.datetime(2026, 9, 28, tz="Asia/Taipei"),
    schedule="0 21 * * *",
    catchup=False,
    tags=["flight", "price"],
) as dag:

    run_pipeline = BashOperator(
        task_id="run_all_routes",
        bash_command="cd /app && python src/run_pipeline.py",
    )
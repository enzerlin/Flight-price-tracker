from datetime import timedelta
import pendulum
from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.python import ShortCircuitOperator


def _is_on_time(**context):
    """排程太晚才被執行（例如電腦關機後才開機補跑）就跳過，手動觸發一律執行。"""
    dag_run = context["dag_run"]
    if dag_run.run_type == "manual":
        return True
    late = pendulum.now("UTC") - dag_run.run_after
    return late < timedelta(minutes=30)


with DAG(
    dag_id="flight_price_tracker",
    start_date=pendulum.datetime(2026, 10, 1, tz="Asia/Taipei"),
    schedule="0 21 * * *",
    catchup=False,        # 不補跑錯過的排程
    max_active_runs=1,    # 同時只允許一次執行，避免重複消耗 ScraperAPI 點數
    tags=["flight", "price"],
) as dag:

    check_on_time = ShortCircuitOperator(
        task_id="check_on_time",
        python_callable=_is_on_time,
    )

    run_pipeline = BashOperator(
        task_id="run_flight_pipeline",
        bash_command="""
            cd /app
            python src/run_pipeline.py
        """,
        retries=0,                                  # 爬蟲內部已有重試，避免整批重抓
        execution_timeout=timedelta(minutes=60),    # 避免卡住無限期掛著
    )

    check_on_time >> run_pipeline
"""An example for using Teradata Parallel Transporter (TPT) with Composer."""
import datetime

import airflow
from airflow import models
from airflow.operators.bash import BashOperator
from airflow.providers.cncf.kubernetes.operators.kubernetes_pod import (
    KubernetesPodOperator,
)
from airflow.providers.cncf.kubernetes.secret import Secret
from kubernetes.client import models as k8s

"""Variables for Reading from Teradata."""
TERADATA_HOSTNAME = "10.128.0.26"
TERADATA_USERNAME = "dbc"
SELECT_STMT = "SELECT * FROM tpch.biglineitem;"
# If the number of instances exceeds the number of available sessions, the job aborts.
# Therefore, when specifying multiple instances make sure the MaxSessions attribute
# is set to a high enough value that there is at least one session per instance.
TD_NUM_READ_INSTANCES = 2
# The maximum sessions connected can never exceed the number of
# available AMPs in the system, even if a larger number is specified.
# The default is one session per available AMP.
TD_MAX_SESSIONS = 2
TD_MIN_SESSIONS = 1

"""Variables for writing to GCS."""
GCS_BUCKET = "dannybq"
GCS_PREFIX = "orders"
GCS_OBJECT_NAME = "data.csv"
# (Optional) This parameter applies only when writing to GCS. This parameter controls the sizes of GCS objects.
GCS_MAX_OBJECT_SIZE = "64M"
# Specifies the number of TCP connections to GCS API.
# Must be between 1 and 256
GCS_CONNECTION_COUNT = "256"
# Controls the size of the in-memory buffer kept before a chunk is uploaded.
# Note that GCS only accepts chunks in multiples of 256KiB,
# so this option is always rounded up to the next such multiple.
# Empirical results show that these improvements tapper off around 32MiB or so.
# https://cloud.google.com/cpp/docs/reference/storage/latest/classgoogle_1_1cloud_1_1storage_1_1ObjectWriteStream#recommendations
# https://github.com/googleapis/google-cloud-cpp/issues/2657
GCS_BUFFER_SIZE = "32M"
# Min should be 2 * GCS_CONNECTION_COUNT
GCS_BUFFER_COUNT = 2 * int(GCS_CONNECTION_COUNT)
# Using multiple instances of the DataConnector operator
# (each instance of the DataConnector operator will use a separate
# copy of the access module) will often improve performance of the
# object creation process. When multiple instances are used, the
# objects in GCS will have the following naming convention:
#
# <base-object-name>-<instance number>
GCS_NUM_WRITE_INSTANCES = 10


"""
Create the following 3 secrets in the Composer GKE namespace
called "composer-user-workloads" before running this DAG:
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             
kubectl create secret generic tpt-secrets \
      --namespace=composer-user-workloads \
      --from-literal=TERADATA_PASSWORD=YOUR_TD_PASSWORD \
      --from-literal=GCS_ACCESS_KEY_ID=srvcacct@YOUR_PROJECT_ID.iam.gserviceaccount.com \
      --from-literal=GCS_SECRET_ACCESS_KEY='-----BEGIN PRIVATE KEY-----\n ... \n-----END PRIVATE KEY-----\n'
"""
TERADATA_PASSWORD = Secret(
    deploy_type="env",
    deploy_target="TERADATA_PASSWORD",
    secret="tpt-secrets",
    key="TERADATA_PASSWORD",
)

GCS_ACCESS_KEY = Secret(
    deploy_type="env",
    deploy_target="GCS_ACCESS_KEY_ID",
    secret="tpt-secrets",
    key="GCS_ACCESS_KEY_ID",
)

GCS_SECRET_ACCESS_KEY = Secret(
    deploy_type="env",
    deploy_target="GCS_SECRET_ACCESS_KEY",
    secret="tpt-secrets",
    key="GCS_SECRET_ACCESS_KEY",
)


def read_export_tpt():
    with open("/home/airflow/gcs/data/export.tpt", "r") as f:
        return f.read().replace("$", r"\$")


with models.DAG(
    dag_id="tpt",
    schedule_interval=None,
    start_date=airflow.utils.dates.days_ago(1),
    max_active_tasks=1,
    default_args={
        "retries": 10,
        "retry_delay": datetime.timedelta(seconds=10),
    },
) as dag:
    for x in range(1):
        task_id = f"tpt{x}"
        tpt = KubernetesPodOperator(
            task_id=task_id,
            name="tpt",
            cmds=["bash"],
            arguments=[
                "-c",
                rf"""
                set -e && \
                echo "{read_export_tpt()}" > export.tpt && \
                more export.tpt && \
                tbuild -f export.tpt -u "\
                  TD_HOSTNAME='{TERADATA_HOSTNAME}', \
                  TD_USERNAME='{TERADATA_USERNAME}', \
                  TD_PASSWORD='$TERADATA_PASSWORD', \
                  TD_NUM_READ_INSTANCES={TD_NUM_READ_INSTANCES}, \
                  GCS_NUM_WRITE_INSTANCES={GCS_NUM_WRITE_INSTANCES}, \
                  TD_MAX_SESSIONS={TD_MAX_SESSIONS}, \
                  TD_MIN_SESSIONS={TD_MIN_SESSIONS}, \
                  SELECT_STMT='{SELECT_STMT}', \
                  ACCESS_MODULE_INIT_STR='\
                  Bucket={GCS_BUCKET} \
                  Prefix={GCS_PREFIX}/task_id={task_id}/try_number=$AIRFLOW_RETRY_NUMBER/ \
                  Object={GCS_OBJECT_NAME} \
                  MaxObjectSize={GCS_MAX_OBJECT_SIZE} \
                  BufferSize={GCS_BUFFER_SIZE} \
                  BufferCount={GCS_BUFFER_COUNT} \
                  ConnectionCount={GCS_CONNECTION_COUNT}'" && \
                echo "{{\"try_number\":\"$AIRFLOW_RETRY_NUMBER\"}}" > /airflow/xcom/return.json
                """,
            ],
            env_vars={"AIRFLOW_RETRY_NUMBER": "{{ task_instance.try_number }}"},
            container_resources=k8s.V1ResourceRequirements(
                requests={
                    "cpu": "1000m",
                    "memory": "1000Mi",
                },
                limits={
                    "cpu": "2000m",
                    "memory": "2000Mi",
                },
            ),
            # Increase pod startup timeout to 10 minutes
            startup_timeout_seconds=600,
            log_events_on_failure=True,
            do_xcom_push=True,
            namespace="composer-user-workloads",
            secrets=[TERADATA_PASSWORD, GCS_ACCESS_KEY, GCS_SECRET_ACCESS_KEY],
            image="teradata/tpt:latest",
            config_file="/home/airflow/composer_kube_config",
            kubernetes_conn_id="kubernetes_default",
        )

    pod_task_xcom_result = BashOperator(
        bash_command="echo \"{{ task_instance.xcom_pull('tpt0') }}\"",
        task_id="pod_task_xcom_result",
    )
    tpt >> pod_task_xcom_result

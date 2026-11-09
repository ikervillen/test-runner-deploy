import os
import sys
import tempfile
from pathlib import Path

DAGS = Path(__file__).resolve().parents[1] / "dags"
sys.path.insert(0, str(DAGS))

# Airflow reads its configuration on first import: isolate it before any test imports it.
os.environ["AIRFLOW_HOME"] = tempfile.mkdtemp(prefix="calidad-airflow-tests-")
os.environ["AIRFLOW__CORE__DAGS_FOLDER"] = str(DAGS)
os.environ["AIRFLOW__CORE__LOAD_EXAMPLES"] = "false"

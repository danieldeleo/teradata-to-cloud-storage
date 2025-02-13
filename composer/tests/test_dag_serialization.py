from pathlib import Path
import sys

import pytest
from airflow.models import DagBag


@pytest.fixture(scope="session")
def dagbag():
    dags_path = Path('../dags')
    sys.path.insert(0, dags_path)
    return DagBag(Path('../dags'), include_examples=False)


def test_dag_import_errors(dagbag):
    assert dagbag.import_errors == {}, "No import errors should be found."

''' Uncomment below if you want to fail on warnings
def test_dag_import_warnings(dagbag):
    assert len(dagbag.captured_warnings) == 0, "No warnings should be found."
'''

def test_filename_matches_dag_id(dagbag):
    """Tests that filename matches dag_id"""
    for dag in dagbag.dags.values():
        assert dag.dag_id == Path(dag.relative_fileloc).stem, (
            "Filename does not match DAG ID."
        )

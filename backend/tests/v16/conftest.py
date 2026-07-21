"""Load the same audited semantic/execution bindings used by production."""

from academic_audit import structured_executor
from academic_audit.execution_service import execute_plan
from swufe_rag import query_understanding, tool_planner
from swufe_rag.query_semantics import (
    build_execution_plan,
    deterministic_understanding,
)


query_understanding.deterministic_understanding = deterministic_understanding
tool_planner.build_execution_plan = build_execution_plan
structured_executor.execute_plan = execute_plan

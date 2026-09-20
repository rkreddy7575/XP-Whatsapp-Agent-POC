"""
Test package initialization.
Ensures TESTING=1 is set across all unit and integration test executions,
strictly preventing any test from writing to the production database.
"""
import os
import sys

os.environ["TESTING"] = "1"

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


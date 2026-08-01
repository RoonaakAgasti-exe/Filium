"""Test configuration and fixtures."""
import os
import sys

# Add backend to path so tests can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

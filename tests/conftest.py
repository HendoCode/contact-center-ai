"""Session-wide test settings.

LangSmith tracing is forced off before any test module imports LangChain or loads
`.env` (`load_dotenv` never overrides a variable that is already set), so no test
sends a trace or needs a key, whatever the shell or `.env` says. tests/test_tracing.py
proves it.
"""

from agent.tracing import disable_tracing

disable_tracing()

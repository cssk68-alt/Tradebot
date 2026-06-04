"""Session-wide test fixtures.

Tests must never touch real production artefacts. The LLM transcript log
(``data/llm_log.jsonl``) is the one that leaked before: unit tests exercising the
logging path appended stub rows ("not json at all", "Market: Will X?") to the real
file. Redirect it to a throwaway path for the whole test session via the
``TRADEBOT_LLM_LOG`` override (read per call in ``llm.client._llm_log_path``)."""
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_llm_log(tmp_path_factory):
    log = tmp_path_factory.mktemp("llmlog") / "llm_log.jsonl"
    prev = os.environ.get("TRADEBOT_LLM_LOG")
    os.environ["TRADEBOT_LLM_LOG"] = str(log)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("TRADEBOT_LLM_LOG", None)
        else:
            os.environ["TRADEBOT_LLM_LOG"] = prev

"""Base class for pipeline agents."""
from __future__ import annotations


class Agent:
    name = "agent"

    def __init__(self, settings, store, log):
        self.settings = settings
        self.store = store
        self.log = log

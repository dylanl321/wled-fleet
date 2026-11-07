from __future__ import annotations


class FleetError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class WledError(FleetError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message, status)

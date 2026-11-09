"""Errors the API can translate into structured responses."""


class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class MissingFact(Exception):
    def __init__(self, key: str):
        self.key = key
        super().__init__(key)


class ToolTransportError(Exception):
    pass

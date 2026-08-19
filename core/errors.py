"""Domain level exceptions.

Messages are written in Hebrew because they are shown directly to the operator,
who works in a Hebrew interface.
"""

from __future__ import annotations


class BloodBankError(Exception):
    """Base class for every expected (non-crash) failure in the system."""


class ValidationError(BloodBankError):
    """Raised when operator input is rejected before it reaches the database.

    Attributes:
        field: Name of the offending input field, used by the UI to mark it.
    """

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field


class OutOfStockError(BloodBankError):
    """Raised when a dispense request cannot supply even a single unit."""


class DataIntegrityError(BloodBankError):
    """Raised when stored data contradicts new input in a life-threatening way."""


class DatabaseUnavailableError(BloodBankError):
    """Raised when SQL Server cannot be reached or the query failed."""


class ConcurrentUpdateError(BloodBankError):
    """Raised when another operator took the same units mid transaction.

    Nothing was dispensed: the transaction is rolled back and the operator is
    asked to repeat the request against the refreshed inventory.
    """

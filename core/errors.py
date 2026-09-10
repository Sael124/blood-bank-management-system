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


class BloodTypeConflictError(DataIntegrityError):
    """Raised when a returning donor's blood type contradicts the stored one.

    The two conflicting values are carried on the exception so that the audit
    trail can record what was on file and what was submitted as separate values,
    rather than leaving the contradiction buried in a sentence.

    Attributes:
        recorded_blood_type: The type already stored for this donor.
        submitted_blood_type: The type the operator entered.
    """

    def __init__(
        self, message: str, recorded_blood_type: str, submitted_blood_type: str
    ) -> None:
        super().__init__(message)
        self.recorded_blood_type = recorded_blood_type
        self.submitted_blood_type = submitted_blood_type


class DatabaseUnavailableError(BloodBankError):
    """Raised when SQL Server cannot be reached or the query failed."""


class ConcurrentUpdateError(BloodBankError):
    """Raised when another operator took the same units mid transaction.

    Nothing was dispensed: the transaction is rolled back and the operator is
    asked to repeat the request against the refreshed inventory.
    """


class AccessDeniedError(BloodBankError):
    """Raised when the signed-in role is not allowed to perform this action."""


class AuthenticationError(BloodBankError):
    """Raised when a login attempt is rejected."""

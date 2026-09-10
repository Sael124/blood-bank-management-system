"""Hebrew labels for the codes the system stores in English.

The database keeps every action, outcome and record type as a language neutral
code, so an exported audit line means the same thing to an inspector who does
not read Hebrew. The translation belongs to the interface, and it lives in its
own module because both the templates and the flashed messages need it - keeping
two copies of a label is how a screen and a message end up disagreeing.

A code with no label is shown as it is stored, so a value added to the system
tomorrow appears in the log today instead of vanishing from it.
"""

from __future__ import annotations

ACTION_LABELS = {
    "DONATION_INTAKE": "קליטת תרומה",
    "DONOR_REGISTERED": "רישום תורם חדש",
    "DONOR_NAME_UPDATED": "עדכון שם תורם",
    "ROUTINE_DISPENSE": "ניפוק בשגרה",
    "EMERGENCY_DISPENSE": "ניפוק אר״ן",
    "DATABASE_INITIALISED": "אתחול בסיס נתונים",
    "AUDIT_TRAIL_EXPORTED": "ייצוא יומן תיעוד",
    "AUDIT_TRAIL_VERIFIED": "בדיקת שלמות היומן",
    "RECORDS_EXPORTED": "ייצוא כל הרשומות",
    "LOGIN_SUCCESS": "התחברות",
    "LOGIN_FAILURE": "התחברות נדחתה",
    "LOGOUT": "התנתקות",
    "USER_CREATED": "יצירת משתמש",
    "USER_ACTIVATED": "הפעלת משתמש",
    "USER_DEACTIVATED": "כיבוי משתמש",
}

OUTCOME_LABELS = {
    "SUCCESS": "הצלחה",
    "PARTIAL": "סופק חלקית",
    "REJECTED": "נדחה",
    "FAILURE": "כשל",
}

MODE_LABELS = {"ROUTINE": "שגרה", "EMERGENCY": "אר״ן"}

STATUS_LABELS = {"IN_STOCK": "במלאי", "DISPENSED": "נופק"}

ENTITY_LABELS = {
    "DONOR": "תורם",
    "BLOOD_UNIT": "מנת דם",
    "DISPENSE": "ניפוק",
    "DATABASE": "בסיס נתונים",
    "AUDIT_TRAIL": "יומן תיעוד",
    "RECORDS": "עותק רשומות",
    "USER": "משתמש",
}

OPERATION_LABELS = {
    "CREATE": "יצירת רשומה",
    "UPDATE": "שינוי רשומה",
    "DELETE": "מחיקת רשומה",
    "READ": "עיון ברשומות",
    "NONE": "לא בוצע שינוי",
}

ROLE_LABELS = {
    "ADMIN": "מנהל מערכת",
    "OPERATOR": "עובד בנק הדם",
    "RESEARCHER": "סטודנט מחקר",
}

#: What each kind of broken hash chain means, in the terms an operator needs in
#: order to decide what to do about it.
CHAIN_FAILURE_LABELS = {
    "CONTENT_ALTERED": "תוכן של רשומה ביומן שונה לאחר שנכתבה",
    "LINK_BROKEN": "רשומה הוסרה מאמצע היומן או הוכנסה אליו",
    "HEAD_MISMATCH": "רשומות הוסרו מסוף היומן",
    "HASH_MISSING": "רשומה ללא חתימת אימות נמצאה בין רשומות מוגנות",
}


def label(labels: dict[str, str], value: object) -> str:
    """Translate a stored code to Hebrew, falling back to the raw code."""
    return labels.get(str(value), str(value))

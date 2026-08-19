"""Entry point of the blood bank application.

Run it with:  python main.py
Then open the address printed in the console.
"""

from __future__ import annotations

import logging
import sys

from app_logging.activity_log import ActivityAction, record_standalone
from app_logging.app_logger import configure_logging
from config import get_config
from core.errors import BloodBankError
from core.models import ActivityOutcome
from data.schema import initialise_database
from web import create_app

logger = logging.getLogger(__name__)


def build_application():
    """Prepare logging and the database, then build the Flask application."""
    configure_logging()
    database_was_created = initialise_database()
    if database_was_created:
        record_standalone(
            ActivityAction.DATABASE_INITIALISED,
            ActivityOutcome.SUCCESS,
            "בסיס הנתונים של בנק הדם נוצר ואותחל בהרצה הראשונה.",
        )
    return create_app()


def main() -> int:
    """Start the web server, reporting startup failures in plain language."""
    try:
        application = build_application()
    except BloodBankError as error:
        configure_logging()
        logger.error("Startup failed: %s", error)
        print(f"\nהפעלת המערכת נכשלה: {error}\n", file=sys.stderr)
        return 1

    web_config = get_config().web
    print(f"\nמערכת בנק הדם פועלת בכתובת: http://{web_config.host}:{web_config.port}\n")
    application.run(host=web_config.host, port=web_config.port, debug=web_config.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())

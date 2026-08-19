"""Logging for the blood bank, in two separate channels.

The package is deliberately not named "logging" so it can never shadow the
standard library module.

* app_logger  - technical log written to a rotating file, for developers.
* activity_log - clinical audit trail stored in the database, for the operator
  and for any later investigation of a specific unit of blood.
"""

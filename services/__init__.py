"""Application layer: orchestrates domain rules, storage and auditing.

A service function is the only place where a clinical decision, a database
transaction and an audit entry meet. The web layer calls services and never
touches SQL directly.
"""

"""Persistent storage for normalized market data (v0.1.17).

Provider              API response       -> HistoricalBar     (app/providers/*.py)
Storage (this package) HistoricalBar      -> database row      (db.py, historical_bar_repository.py)
Quant Lab / API routes database row (via repository) -> HistoricalBar back out again

Nothing outside this package touches SQL -- see historical_bar_repository.py's
module docstring for the actual rule and why it matters.
"""

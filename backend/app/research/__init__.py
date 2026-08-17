"""Research v1 -- the hypothesis-testing engine (app/research/).

Turns a Condition (app/models/research.py) into a search over the
existing normalized historical bar dataset (never raw data -- see
app/storage/historical_bar_repository.py), an Outcome into the forward
measurement taken at every qualifying signal, and a list of
ExperimentEvent into aggregate ExperimentResults.

Deliberately narrow, per this feature's spec: one condition, one
outcome, per experiment; no Features/Backtesting/ML/strategy
optimization/paper trading. See app/api/research.py for the HTTP layer
and app/storage/research_repository.py for persistence -- this package
itself is pure computation, no I/O.
"""

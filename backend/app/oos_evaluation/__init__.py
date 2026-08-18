"""OOS Evaluation v1 (v0.1.31): the actual OOS evaluation operation the
OOS / Holdout Partition Framework (app/oos/) and Experiment Freeze &
Provenance (app/research/lifecycle.py) were both built toward.

    app/oos_evaluation/warmup.py    pure: how much DEVELOPMENT context (and which calendar
                                      range) is read purely to warm up trailing features at
                                      the first holdout bar -- never to create eligible
                                      signal/entry/outcome observations
    app/oos_evaluation/engine.py    the orchestrator: frozen snapshot -> holdout bars
                                      (via app.oos.access.get_holdout_bars(...,
                                      confirm_oos_validation_use=True), the sole holdout
                                      access path) -> Feature Engine -> Backtesting v1's
                                      run_backtest() -> OOSEvaluationResult
    app/storage/oos_evaluation_repository.py   append-only persistence
    app/api/oos_evaluation.py        POST /research/experiments/{id}/oos-evaluate

Does not implement strategy optimization, parameter search, or ML --
this is exactly one deterministic run of an already-frozen hypothesis
against its already-reserved holdout data, orchestrating the existing
Feature Engine / Research condition evaluation / Backtesting v1 engine,
never a second implementation of any of them.
"""

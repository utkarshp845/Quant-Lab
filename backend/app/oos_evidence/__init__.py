"""OOS Evidence Accumulation V1 (v0.1.33): lets an already-FROZEN
experiment accumulate MORE THAN ONE independent OOS evaluation period
over time, without ever modifying the hypothesis, the
ExperimentFreezeSnapshot, any prior OOSEvaluationResult, or development
data.

    app/oos_evidence/period.py         pure: registration/leakage rules for linking an
                                         additional, already-existing OOSPartition to a
                                         frozen experiment as a new evaluation period
    app/oos_evidence/evaluation.py      orchestrator: period-level "not already evaluated"
                                         guard, then app.oos_evaluation.engine.
                                         evaluate_oos_for_partition() (the SAME pipeline
                                         OOS Evaluation v1 already runs, reused UNMODIFIED)
    app/oos_evidence/aggregation.py     pure: the read-only evidence-accumulation summary
                                         across every COMPLETED evaluation of every period
    app/storage/oos_evidence_repository.py   the (experiment_id, oos_partition_id) link table
    app/api/oos_evidence.py             HTTP routes

Does not implement OOS statistical significance testing (p-values,
confidence intervals, a significant/not-significant verdict) -- that is
a later, separate "OOS Statistical Review" step, explicitly out of this
feature's scope. Does not implement parameter optimization or
hypothesis mutation of any kind -- every period is evaluated against
the SAME immutable ExperimentFreezeSnapshot OOS Evaluation v1 already
established, never a re-derived or re-entered one.
"""

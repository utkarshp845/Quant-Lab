"""OOS Statistical Review V1 (v0.1.34): a formal, READ-ONLY statistical
review layer over OOS Evidence Accumulation V1's own accumulated
evidence (app/oos_evidence/) -- "given all COMPLETED OOS periods
accumulated for this frozen hypothesis, is there sufficient statistical
evidence that the observed effect differs from the appropriate
baseline?"

    app/oos_statistical_review/baseline.py   the OOS-scoped unconditional baseline --
                                               app.statistical_validation.baseline's own
                                               CONTROL_CONDITION, reused unmodified, run
                                               through the unmodified Backtesting v1 engine
                                               over the SAME holdout bars each COMPLETED
                                               evaluation already used -- NEVER development data
    app/oos_statistical_review/verdict.py    pure: the deterministic SUPPORTED/NOT_SUPPORTED/
                                               INCONCLUSIVE/INSUFFICIENT_DATA rule
    app/oos_statistical_review/engine.py     the orchestrator: provenance verification (fail
                                               closed on any mismatch), episode-level pooling
                                               across periods, both of Statistical Validation
                                               V2's dependence-aware methods (reused unmodified),
                                               power/MDE, per-period consistency, verdict
    app/storage/oos_statistical_review_repository.py   append-only persistence
    app/api/oos_statistical_review.py         POST/GET routes -- no endpoint here can ever
                                               modify the frozen hypothesis, an
                                               ExperimentFreezeSnapshot, an OOS partition, an
                                               OOS evaluation, an OOS signal, a historical bar,
                                               or a historical feature; this feature writes
                                               ONLY new, immutable review rows

Reuses, never modifies: app.statistical_validation.episodes
(group_into_episodes/episode_representatives), app.statistical_validation.
baseline.CONTROL_CONDITION, app.statistical_validation.resampling
(bootstrap_mean_difference_ci/bootstrap_win_rate_ci/
permutation_test_mean_difference/cohens_d), app.statistical_validation.
v2.baseline.non_overlapping_baseline (Method A), app.statistical_validation.
v2.resampling (moving_block_bootstrap_* -- Method B), app.statistical_validation.
v2.power.minimum_detectable_effect_size, app.backtesting.engine.run_backtest(),
app.backtesting.aggregation.aggregate_results(), app.features.engine.
compute_features(), app.oos.access.get_holdout_bars(), app.oos_evaluation.
warmup.warmup_range(), app.research.metrics.bars_for_window() -- and every
storage/model module this feature only ever READS from (research_repository,
experiment_freeze_repository, oos_partition_repository, oos_evaluation_repository,
historical_bar_repository).

Introduces NO parameter optimization, threshold searching, hypothesis
modification, or additional data mining -- the frozen hypothesis'
own, single, pre-specified primary horizon is always what this review
tests; every other horizon (were one ever computable from ALREADY-
persisted OOS evidence -- see app/oos_statistical_review/engine.py's
own docstring for why none currently is) would be reported as
exploratory only, and NEVER used to select or override the primary
verdict.
"""

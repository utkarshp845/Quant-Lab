"""OOS / Holdout Partition Framework v1 (v0.1.29).

    app/models/oos_partition.py   the partition shape + deterministic id + structural range validation
    app/oos/partition.py          THIS PACKAGE's pure logic: classifying a date range against a
                                   partition, and the leakage-guard functions built on top of it
    app/oos/access.py             the explicit development-vs-holdout bar-reading boundary
    app/storage/oos_partition_repository.py   persistence (SQLite -- app/storage/db.py's
                                   `oos_partitions` table)
    app/api/oos_partitions.py     HTTP routes

Scope: this establishes the partition/provenance boundary only -- no
OOS statistical test, optimizer, ML, strategy construction, or paper
trading reads a holdout window through anything in this package yet.
See app/oos/access.py's module docstring for exactly what is, and is
not, technically prevented today.
"""

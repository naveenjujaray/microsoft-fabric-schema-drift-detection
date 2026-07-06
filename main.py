#!/usr/bin/env python
"""Compatibility shim: `python main.py ...` still works after packaging.

The implementation lives in ``fabric_drift_detective.cli``; installed
users get the ``fabric-drift`` console script or ``python -m
fabric_drift_detective``.
"""

from fabric_drift_detective.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

"""Domain-agnostic governance spine — reusable in P7.

This package MUST NOT import from ``src.domain`` and MUST NOT contain
domain-specific string literals (tenant/lease/rent/maintenance/evict/...).
``tests/test_governance_no_domain_imports.py`` enforces this so the P7
transfer (swapping in a SOC ``domain/`` package) cannot break silently.
"""
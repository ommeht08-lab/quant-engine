# Model transition periods explicitly and retain calendar lineage

SEC 10-KT/10-QT stub periods are represented by exact fiscal-transition definitions rather than being inferred as irregular quarters or years. Every classified fact retains the immutable fiscal-calendar version that produced its period labels, so later policy corrections coexist as distinguishable append-only facts and callers can select a deliberate calendar version.

The fundamentals store is not yet wired to production, so this decision changes its pre-production schema in place rather than inventing a migration for data that has never been published. Any populated experimental store created from the earlier schema must be recreated before publishing; once production publishing begins, later schema changes require an explicit migration.

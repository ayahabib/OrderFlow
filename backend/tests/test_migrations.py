"""Static checks on migration files (no database needed)."""

from scripts.migrate import migration_files


def test_migrations_are_numbered_and_unique():
    files = migration_files()
    assert files, "no migration files found"
    numbers = [f.name.split("_", 1)[0] for f in files]
    assert numbers == sorted(numbers)
    assert len(numbers) == len(set(numbers)), "duplicate migration numbers"


def test_every_store_table_has_store_id():
    """Hard rule 3: every table (except stores itself) has store_id."""
    import re

    sql = "\n".join(f.read_text(encoding="utf-8") for f in migration_files())
    tables = re.findall(r"create table (\w+) \((.*?)\n\);", sql, flags=re.S)
    assert tables
    for name, body in tables:
        if name == "stores":
            continue
        assert re.search(r"^\s*store_id\s+uuid not null", body, flags=re.M), name

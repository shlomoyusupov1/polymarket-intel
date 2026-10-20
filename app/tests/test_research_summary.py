import os

import pytest

DB = os.environ.get("PMINTEL_TEST_DB")


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_report_prints_every_section_and_never_fills_in_missing_results():
    from pmintel import db, research_summary as R
    conn = db.connect(DB)
    db.init_schema(conn)
    conn.autocommit = True
    conn.execute("DELETE FROM research_reports WHERE kind IN ('stocklab_v1', 'cryptolab_v1')")
    out = R.build(conn)
    for head in ("## Data by domain", "## Research runs", "## Stocks lab", "## Crypto lab", "## Forward paper tracking",
                 "## Polymarket research", "## Tip sources"):
        assert head in out
    assert "INSUFFICIENT DATA: no stocklab_v1 report yet" in out and "INSUFFICIENT DATA: no cryptolab_v1 report yet" in out
    conn.close()

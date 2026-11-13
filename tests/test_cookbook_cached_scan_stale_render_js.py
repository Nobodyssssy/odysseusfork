"""The Launch tab must never sit behind a cached-model scan.

The scan itself is ~1s (down from 6.28s). Returning users already had a
localStorage entry, but it was only honoured inside a 6h TTL — after that the
list cleared and showed the full "Scanning cached models…" spinner for the whole
scan. A persisted entry is now rendered immediately, fresh or stale, and a
stale one is refreshed in the background while the rows stay on screen.

An entry that claims a model is still downloading (or has an incomplete shard)
while nothing is actually downloading it is still discarded — that is the one
case where stale rows would be actively wrong.
"""
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookServe.js"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_persisted_scan_is_rendered_even_when_stale():
    source = _source()

    assert "_readCachedModelScan(scanSig, { allowStale: true })" in source
    assert "return { data, fresh };" in source
    # Stale rows stay visible; only a fresh entry short-circuits the refresh.
    assert "if (cachedEntry.fresh) return;" in source


def test_stale_scan_still_refreshes_in_background():
    source = _source()

    # preserveRows keeps the rows (and yields an inline refresh error) while the
    # background scan runs.
    assert "preserveRows = !!cached || (hasModelRows && list.dataset.cookbookScanSig === scanSig);" in source
    assert "// Stale: rows stay visible; the scan below refreshes them silently." in source


def test_stale_downloading_entry_is_still_discarded():
    source = _source()

    assert "const staleDownloading = models.some(m =>" in source
    assert "delete all[sig];" in source

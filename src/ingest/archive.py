"""Public Bybit archive discovery and streaming download; no trading credentials."""
from pathlib import Path
from urllib.parse import quote
import hashlib
import requests

BASE = "https://public.bybit.com"

def archive_url(market: str, symbol: str, filename: str) -> str:
    if market not in {"trading", "spot"}:
        raise ValueError("Unsupported archive market")
    if not symbol or "/" in symbol or ".." in symbol:
        raise ValueError("Invalid symbol")
    if not filename.endswith(".csv.gz") or "/" in filename or ".." in filename:
        raise ValueError("Invalid archive filename")
    return f"{BASE}/{market}/{quote(symbol)}/{quote(filename)}"

def download(url: str, destination: Path, session=None) -> dict:
    """Atomic download. Resume is deliberately not claimed until HTTP range support is verified."""
    session = session or requests.Session()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".partial")
    digest = hashlib.sha256()
    size = 0
    try:
        with session.get(url, stream=True, timeout=(15, 120)) as response:
            response.raise_for_status()
            with temp.open("wb") as output:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if block:
                        output.write(block)
                        digest.update(block)
                        size += len(block)
        temp.replace(destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return {"path": str(destination), "bytes": size, "sha256": digest.hexdigest(), "source": url}

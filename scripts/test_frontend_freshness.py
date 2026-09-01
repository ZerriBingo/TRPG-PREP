"""HTTP contract preventing stale local workbench assets."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.main import app  # noqa: E402


async def main() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        root = await client.get("/", follow_redirects=False)
        assert root.status_code == 307, root.text
        assert root.headers.get("cache-control") == "no-store"
        for path in ("/workbench.html", "/workbench.js", "/workbench.css"):
            response = await client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert response.headers.get("cache-control") == "no-store", (
                path,
                response.headers.get("cache-control"),
            )

        empty = await client.get("/api/domain/workbench")
        assert empty.status_code == 200, empty.text
        payload = empty.json()
        assert "source_checks" not in payload
        assert "coverage" not in payload

    print("PASS: workbench assets always refresh in the local application")


if __name__ == "__main__":
    asyncio.run(main())

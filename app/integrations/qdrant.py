from typing import Any
from urllib.parse import urlsplit

import httpx

MANAGED_BY = "career_m2_v1"


class VectorBackendUnavailable(RuntimeError):
    pass


class QdrantStore:
    def __init__(self, client: httpx.AsyncClient, collection: str, dimension: int):
        self.client = client
        self.collection = collection
        self.dimension = dimension
        self.local = urlsplit(str(client.base_url)).hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
            "qdrant",
        }

    async def _call(self, method: str, suffix: str = "", **kwargs: Any) -> Any:
        try:
            response = await self.client.request(
                method, f"/collections/{self.collection}{suffix}", **kwargs
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or body.get("status") != "ok":
                raise ValueError("Invalid vector backend result")
            return body["result"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise VectorBackendUnavailable("Vector backend unavailable or incompatible") from exc

    async def ensure_collection(self) -> None:
        try:
            response = await self.client.get(f"/collections/{self.collection}")
        except httpx.HTTPError as exc:
            raise VectorBackendUnavailable("Vector backend unavailable") from exc
        if response.status_code == 404:
            await self._call(
                "PUT", json={"vectors": {"size": self.dimension, "distance": "Cosine"}}
            )
        else:
            config = await self._call("GET")
            vectors = config["config"]["params"]["vectors"]
            if vectors.get("size") != self.dimension or vectors.get("distance") != "Cosine":
                raise VectorBackendUnavailable(
                    "Existing collection has incompatible vector geometry"
                )

    async def existing_ids(self, point_ids: list[str]) -> set[str]:
        result: set[str] = set()
        for start in range(0, len(point_ids), 256):
            rows = await self._call(
                "POST",
                "/points",
                json={
                    "ids": point_ids[start : start + 256],
                    "with_payload": False,
                    "with_vector": False,
                },
            )
            result.update(str(row["id"]) for row in rows)
        return result

    async def vectors(self, point_ids: list[str]) -> list[list[float]]:
        """Stored vectors for the given points, in request order; missing points are skipped."""
        rows = await self._call(
            "POST",
            "/points",
            json={"ids": point_ids[:256], "with_payload": False, "with_vector": True},
        )
        by_id = {str(row["id"]): row.get("vector") for row in rows}
        result = []
        for point_id in point_ids[:256]:
            vector = by_id.get(point_id)
            if vector is None:
                continue
            if (
                not isinstance(vector, list)
                or len(vector) != self.dimension
                or not all(isinstance(value, int | float) for value in vector)
            ):
                raise VectorBackendUnavailable("Invalid stored vector")
            result.append([float(value) for value in vector])
        return result

    async def upsert(self, points: list[dict[str, Any]]) -> None:
        for start in range(0, len(points), 64):
            result = await self._call(
                "PUT",
                "/points",
                params={"wait": "true"},
                json={"points": points[start : start + 64]},
            )
            if result.get("status") != "completed":
                raise VectorBackendUnavailable("Vector write was not confirmed")

    async def query(
        self, vector: list[float], version_keys: list[str], limit: int
    ) -> list[dict[str, Any]]:
        if not version_keys:
            return []
        result = await self._call(
            "POST",
            "/points/query/groups",
            json={
                "query": vector,
                "filter": {
                    "must": [
                        {"key": "version_key", "match": {"any": version_keys}},
                        {"key": "managed_by", "match": {"value": MANAGED_BY}},
                    ]
                },
                "group_by": "job_id",
                "group_size": 1,
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
            },
        )
        try:
            return [group["hits"][0] for group in result["groups"] if group["hits"]]
        except (KeyError, TypeError) as exc:
            raise VectorBackendUnavailable("Invalid vector group response") from exc

    async def managed_ids(self) -> set[str]:
        result: set[str] = set()
        offset: str | int | None = None
        seen_offsets: set[str | int] = set()
        while True:
            body: dict[str, Any] = {
                "limit": 256,
                "with_payload": False,
                "with_vector": False,
                "filter": {"must": [{"key": "managed_by", "match": {"value": MANAGED_BY}}]},
            }
            if offset is not None:
                body["offset"] = offset
            page = await self._call("POST", "/points/scroll", json=body)
            result.update(str(point["id"]) for point in page["points"])
            offset = page.get("next_page_offset")
            if offset is None:
                return result
            if offset in seen_offsets or len(seen_offsets) > 5000:
                raise VectorBackendUnavailable("Vector scroll did not converge")
            seen_offsets.add(offset)

    async def delete_ids(self, ids: list[str]) -> None:
        for start in range(0, len(ids), 256):
            result = await self._call(
                "POST",
                "/points/delete",
                params={"wait": "true"},
                json={"points": ids[start : start + 256]},
            )
            if result.get("status") != "completed":
                raise VectorBackendUnavailable("Vector deletion was not confirmed")

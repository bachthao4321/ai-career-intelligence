"""CI/operator smoke with an in-memory, explicitly synthetic CV; prints no bearer token."""

import httpx

from tests.cv_helpers import pdf_bytes


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=20) as client:
        response = client.post(
            "/cv", content=pdf_bytes(), headers={"content-type": "application/pdf"}
        )
        response.raise_for_status()
        uploaded = response.json()
        path = "/cv/" + uploaded["id"]
        headers = {"Authorization": "Bearer " + uploaded["access_token"]}
        try:
            response = client.post(path + "/matches", headers=headers, json={"top_k": 3})
            response.raise_for_status()
            report = response.json()
            assert report["items"], "Import synthetic jobs before this smoke check"
            assert {s["slug"] for s in uploaded["profile"]["skills"]} == {"python", "postgresql"}
            assert all(
                "job_evidence" in skill
                for item in report["items"]
                for skill in item["matched_skills"]
            )
            print(
                {
                    "profile_skills": len(uploaded["profile"]["skills"]),
                    "eligible_listings": report["eligible_listings"],
                    "matches": len(report["items"]),
                }
            )
        finally:
            response = client.delete(path, headers=headers)
            response.raise_for_status()
        assert client.get(path, headers=headers).status_code == 404
        print("Synthetic CV upload/match/delete: OK")


if __name__ == "__main__":
    main()

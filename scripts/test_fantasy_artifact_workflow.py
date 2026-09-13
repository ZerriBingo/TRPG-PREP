"""HTTP workflow checks for environment-led fantasy artifact generation."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.domain import (  # noqa: E402
    ExampleBundle,
    ExtractionWindow,
    PageSpan,
    PrepJob,
    PrepScope,
    SourceFact,
    SourceRef,
)


async def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory() as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "fantasy-artifact.db"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": "http://offline.invalid",
                    "api_key": "",
                    "model": "fake-fantasy-artifact",
                    "fake": True,
                }
            )
            source_file = "fixture://fantasy-adventure"
            facts = [
                SourceFact(
                    id="fact_fantasy_premise",
                    text="一只会说话的羊请求冒险者帮助它恢复原形。",
                    kind="event",
                    visibility="explicit",
                    source_refs=[SourceRef(file=source_file, page=1)],
                ),
                SourceFact(
                    id="fact_fantasy_environment",
                    text="法师庄园的仪式、围墙和林地共同构成最后的危险局势。",
                    kind="location",
                    visibility="explicit",
                    source_refs=[SourceRef(file=source_file, page=2)],
                ),
                SourceFact(
                    id="fact_fantasy_enemy",
                    text="法师想拖延闯入者并完成仪式，也可能在失败后逃走。",
                    kind="threat",
                    visibility="explicit",
                    source_refs=[SourceRef(file=source_file, page=2)],
                ),
                SourceFact(
                    id="fact_fantasy_clock",
                    text="仪式持续推进，完成后受害者将无法恢复。",
                    kind="timeline",
                    visibility="explicit",
                    source_refs=[SourceRef(file=source_file, page=2)],
                ),
                SourceFact(
                    id="fact_fantasy_faction",
                    text="两方势力都在追寻失落的仪式记录。",
                    kind="npc",
                    visibility="explicit",
                    source_refs=[SourceRef(file=source_file, page=2)],
                ),
                SourceFact(
                    id="fact_fantasy_reversal",
                    text="被救出的学徒知道仪式真正的代价。",
                    kind="clue",
                    visibility="hidden",
                    source_refs=[SourceRef(file=source_file, page=2)],
                ),
                SourceFact(
                    id="fact_fantasy_aftermath",
                    text="庄园受损会改变附近聚落对法师的态度。",
                    kind="event",
                    visibility="explicit",
                    source_refs=[SourceRef(file=source_file, page=2)],
                ),
            ]
            bundle = ExampleBundle(
                id="fantasy_artifact_workflow",
                name="奇幻产物工作流",
                profile_ids=["daggerheart"],
                facts=facts,
                cards=[],
                plans=[],
            )
            storage.save_domain_bundle(
                bundle.id, bundle.model_dump(mode="json", by_alias=True)
            )
            job = PrepJob(
                id="prep_job_fantasy_artifact",
                status="completed",
                scope=PrepScope(
                    source_file=source_file,
                    source_version="v1",
                    page_spans=[PageSpan(start=1, end=2)],
                    profile_id="daggerheart",
                    objective="prepare one short fantasy adventure",
                ),
                model_id="fake-fantasy-artifact",
                prompt_version="test",
                schema_version="test",
                workspace_id=bundle.id,
                windows=[
                    ExtractionWindow(
                        id="prep_window_fantasy_artifact",
                        page_span=PageSpan(start=1, end=2),
                        core_span=PageSpan(start=1, end=2),
                        status="succeeded",
                        candidate_count=0,
                        input_chars=10,
                    )
                ],
                candidate_count=0,
                created_at=storage.now(),
                updated_at=storage.now(),
            )
            storage.create_prep_job(job.model_dump(mode="json"))

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                premature = await client.post(
                    f"/api/domain/examples/{bundle.id}/cards/draft"
                )
                assert premature.status_code == 422, premature.text
                assert "冒险章节" in premature.text

                response = await client.post(
                    f"/api/domain/examples/{bundle.id}/structure/draft"
                )
                assert response.status_code == 202, response.text
                structure_job = response.json()["job"]
                for _ in range(80):
                    status = await client.get(
                        f"/api/domain/examples/{bundle.id}/structure/jobs/"
                        f"{structure_job['id']}"
                    )
                    structure_job = status.json()["job"]
                    if structure_job["status"] in {"completed", "failed"}:
                        break
                    await asyncio.sleep(0.02)
                assert structure_job["status"] == "completed", structure_job

                proposed = await client.get(
                    "/api/domain/workbench", params={"example": bundle.id}
                )
                proposed_bundle = proposed.json()["bundle"]
                assert len(proposed_bundle["adventure_chapters"]) == 2
                assert len(proposed_bundle["story_threads"]) == 1
                assert len(proposed_bundle["story_beats"]) == len(proposed_bundle["adventure_chapters"])
                assert len(proposed_bundle["fact_dispositions"]) == len(facts)
                assert {
                    item["fact_id"] for item in proposed_bundle["fact_dispositions"]
                } == {fact.id for fact in facts}

                approved_structure = await client.post(
                    f"/api/domain/examples/{bundle.id}/structure/approve"
                )
                assert approved_structure.status_code == 200, approved_structure.text

                response = await client.post(
                    f"/api/domain/examples/{bundle.id}/cards/draft"
                )
                assert response.status_code == 202, response.text
                artifact_job = response.json()["job"]
                for _ in range(80):
                    status = await client.get(
                        f"/api/domain/examples/{bundle.id}/cards/draft-jobs/{artifact_job['id']}"
                    )
                    artifact_job = status.json()["job"]
                    if artifact_job["status"] in {"completed", "failed"}:
                        break
                    await asyncio.sleep(0.02)
                assert artifact_job["status"] == "completed", artifact_job

                loaded = await client.get(
                    "/api/domain/workbench", params={"example": bundle.id}
                )
                workbench = loaded.json()
                public_profile = workbench["profiles"]["fantasy-adventure"]
                assert public_profile["runtime_anchor_type"] == "environment"
                assert public_profile["runtime_navigation_mode"] == "environment"
                assert len(public_profile["card_definitions"]) == 4
                cards = workbench["bundle"]["cards"]
                assert workbench["bundle"]["prep_units"] == []
                assert all(card["prep_unit_id"] is None for card in cards)
                assert workbench["bundle"]["runtime_brief"] is None
                chapter_ids = {
                    item["id"] for item in workbench["bundle"]["adventure_chapters"]
                }
                assert all(item["review_state"] == "approved" for item in workbench["bundle"]["adventure_chapters"])
                assert all(item["overview"] for item in workbench["bundle"]["adventure_chapters"])
                assert all(set(card["chapter_ids"]) == chapter_ids for card in cards)
                plan_steps = [
                    item
                    for item in storage.list_artifact_job_steps(artifact_job["id"])
                    if item["stage"] == "global_plan"
                ]
                assert len(plan_steps) == len(chapter_ids)
                assert {card["type"] for card in cards} == {
                    "environment",
                    "npc",
                    "enemy",
                    "encounter_clock",
                }
                environment = next(card for card in cards if card["type"] == "environment")
                group_id = environment["runtime_group_ids"][0]
                assert group_id.startswith("encounter_")
                for card_type in ("npc", "enemy", "encounter_clock"):
                    support = next(card for card in cards if card["type"] == card_type)
                    assert support["runtime_group_ids"] == [group_id]

                card_ids = [card["id"] for card in cards]
                approved = await client.post(
                    f"/api/domain/examples/{bundle.id}/cards/review",
                    json={"card_ids": card_ids, "action": "approve"},
                )
                assert approved.status_code == 200, approved.text

                planned = await client.post(
                    f"/api/domain/examples/{bundle.id}/plans/draft"
                )
                assert planned.status_code == 200, planned.text
                plan = planned.json()["plan"]
                assert plan["navigation_mode"] == "environment"
                assert plan["environment_card_ids"] == [environment["id"]]
                assert plan["location_card_ids"] == []
                assert plan["beats"] == []
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()

    print("PASS: fantasy artifact HTTP workflow builds an environment-led runtime plan")


if __name__ == "__main__":
    asyncio.run(main())

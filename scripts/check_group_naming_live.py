"""Live check: runtime group keys stay readable ASCII after the v16 contract change.

Runs the real production path — structure job, approval, artifact job — against an
isolated temporary database. Local digest and global plan call the configured model,
because runtime groups are the planner's output; materialization is delegated to the
fake client since the plan owns runtime grouping and the materializer must not change it.

Spends real model calls, so it is opt-in: set TRPG_LIVE_TESTS=1 to run it.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _live_guard import require_live_tests  # noqa: E402

require_live_tests("runtime group naming live check")

from backend.app import artifacts, llm, storage, structure  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef  # noqa: E402

FACTS = [
    "蜡烛巷仓库是散塔林会在码头区的安全屋，堆满走私货箱，夜间有守卫。",
    "散塔林会打手常在蜡烛巷仓库值守，遇到闯入者先威胁再动手。",
    "下水道藏身处是珊娜萨帮在旧下水道深处的巢穴，入口被一道魔法门封住。",
    "珊娜萨帮成员在下水道藏身处轮班，对任何追踪者都保持警惕。",
    "珊娜萨帮与散塔林会为一件失踪的宝石互相报复，冲突在码头区不断升级。",
    "春季节庆游行每年在码头区举行，人群拥挤，便于跟踪与扒窃。",
    "港口祭司在节庆期间收集捐赠，愿意为捐赠者提供城里的情报。",
    "走私账本记录仓库的货物往来，其中反复出现代号“灰手”。",
    "灰手身份不明，账本显示此人指挥仓库的夜间转运。",
    "魔法门是通往下水道藏身处的石拱门，需要口令才能开启。",
    "口令由散塔林会打手在巡逻时低声重复，可以被偷听。",
    "旧下水道地图标注了三条通往巢穴的路线，其中一条已经部分塌方。",
    "塌方通道是通往巢穴的近路，但通过时会发出声响。",
    "仓库巡逻班次显示，下半夜只有两名守卫。",
    "仓库后门挂着一口铜钟，守卫遇袭时会敲响它。",
    "节庆游行期间，城卫队的巡逻路线会绕开码头区西侧。",
]


def main() -> None:
    original_db_path = storage.DB_PATH
    # A temporary database starts with default LLM settings, which name a placeholder
    # model and carry no key, so a real call would fail with 401. Copy the real
    # configuration across, and never print the credential.
    real_config = storage.get_config()
    if not real_config.get("api_key"):
        print("INCONCLUSIVE: no api_key is configured, so no real call is possible")
        return
    print(
        "CONFIG model=%s base_url=%s has_key=%s"
        % (real_config["model"], real_config["base_url"], bool(real_config["api_key"]))
    )
    recorded: list[list[str]] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "group-naming-live.db"
        try:
            storage.init_db()
            storage.set_config(
                {
                    "base_url": real_config["base_url"],
                    "api_key": real_config["api_key"],
                    "model": real_config["model"],
                }
            )
            facts = [
                SourceFact(
                    id=f"fact_live_{index:03d}",
                    text=text,
                    kind="event",
                    visibility="explicit",
                    source_refs=[SourceRef(file="fixture://group-naming", page=index // 4 + 1)],
                )
                for index, text in enumerate(FACTS)
            ]
            bundle = ExampleBundle(
                id="group_naming_live",
                name="组名实时验证",
                profile_ids=["daggerheart"],
                facts=facts,
                cards=[],
                plans=[],
            )
            storage.save_domain_bundle(bundle.id, bundle.model_dump(mode="json", by_alias=True))

            model_id = str(storage.get_config()["model"])
            structure_job, created = structure.create_structure_job(
                bundle.id, "daggerheart", model_id=model_id, fake_model=False
            )
            assert created
            print(f"STRUCTURE_JOB {structure_job.id} model={model_id}")
            structure.execute_structure_job(structure_job.id)
            finished_structure = structure.get_structure_job(structure_job.id)
            print(f"STRUCTURE_STATUS {finished_structure.status} error={finished_structure.error}")
            if finished_structure.status != "completed":
                print("INCONCLUSIVE: structure stage did not complete")
                return
            structured = structure.approve_structure(bundle.id)
            print(
                "APPROVED chapters=%d threads=%d beats=%d"
                % (
                    len(structured.adventure_chapters),
                    len(structured.story_threads),
                    len(structured.story_beats),
                )
            )
            storage.save_domain_bundle(bundle.id, structured.model_dump(mode="json"))

            artifact_job, created = artifacts.create_artifact_job(
                bundle.id, "daggerheart", model_id=model_id, fake_model=False
            )
            assert created
            real_client = llm.make_client(model_id=model_id)
            fake_client = llm.make_client(model_id=model_id, force_fake=True)

            class RoutingClient:
                """Real model for planning; fake for materialization only."""

                def chat_json(self, messages, **kwargs):
                    content = messages[1]["content"]
                    if "[TASK:prep:artifact_materialize]" in content:
                        return fake_client.chat_json(messages, **kwargs)
                    output = real_client.chat_json(messages, **kwargs)
                    if "[TASK:prep:artifact_global_plan]" in content:
                        for card in output.get("cards") or []:
                            groups = [str(item) for item in card.get("runtime_groups") or []]
                            if groups:
                                recorded.append(groups)
                                print(
                                    "RAW_GROUPS %s | %s"
                                    % (card.get("type"), json.dumps(groups, ensure_ascii=False))
                                )
                    return output

            original_make_client = artifacts.make_client
            artifacts.make_client = lambda *args, **kwargs: RoutingClient()
            try:
                artifacts.execute_artifact_job(
                    artifact_job.id, workspace_id=bundle.id, profile_id="daggerheart"
                )
            finally:
                artifacts.make_client = original_make_client

            finished = artifacts.get_artifact_job(artifact_job.id)
            print(f"ARTIFACT_STATUS {finished.status} error={finished.error}")

            saved = storage.load_domain_bundle(bundle.id)
            generated = ExampleBundle.model_validate(saved[0])
            group_ids = sorted({g for card in generated.cards for g in card.runtime_group_ids})
            placeholders = [g for g in group_ids if g.startswith("encounter_situation_")]
            print("DISTINCT_GROUP_IDS %d" % len(group_ids))
            print("PLACEHOLDER_SLUGS %d" % len(placeholders))
            print("GROUP_IDS " + json.dumps(group_ids, ensure_ascii=False))
            print("RAW_GROUP_TEXTS %d" % len(recorded))
            if not group_ids:
                print("INCONCLUSIVE: the planner emitted no runtime groups for this fixture")
            elif placeholders:
                print("FAIL: %d identifiers fell back to the placeholder slug" % len(placeholders))
            else:
                print("PASS: every runtime group identifier kept a readable slug")
        finally:
            storage.DB_PATH = original_db_path
            storage.init_db()


if __name__ == "__main__":
    main()

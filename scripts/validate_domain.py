from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "backend" / "domain"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--example", default="red_signal_fixture", help="example bundle id under backend/domain/examples")
    parser.add_argument("--write-markdown", type=Path, help="export validated cards to Markdown")
    args = parser.parse_args()

    from backend.domain import (
        ExampleBundle,
        export_cards_markdown,
        load_json,
        load_profiles,
        validate_bundle,
    )

    profiles = load_profiles(DOMAIN / "profiles")
    example_path = DOMAIN / "examples" / f"{args.example}.json"
    if not example_path.is_file():
        parser.error(f"unknown example bundle: {args.example}")
    bundle = ExampleBundle.model_validate(load_json(example_path))
    validate_bundle(bundle, profiles)

    print(f"bundle={bundle.id} profiles={len(profiles)} facts={len(bundle.facts)} cards={len(bundle.cards)}")
    for profile in profiles.values():
        cards = [card for card in bundle.cards if card.profile_id == profile.id]
        print(f"- {profile.id}: {len(cards)} card(s)")

    if args.write_markdown:
        markdown = export_cards_markdown(bundle.cards, bundle.facts, profiles)
        args.write_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.write_markdown.write_text(markdown, encoding="utf-8")
        print(f"markdown={args.write_markdown}")


if __name__ == "__main__":
    main()

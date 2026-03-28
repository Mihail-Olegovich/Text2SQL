import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"

SOURCES = [
    (DATA / "dev_20240627" / "dev.json", DATA / "dev_20240627" / "dev_databases"),
    (DATA / "train" / "train.json", DATA / "train" / "train_databases"),
]


def collect_evidences(json_path: Path) -> dict[str, list[str]]:
    records = json.loads(json_path.read_text())
    db_evidences: dict[str, list[str]] = defaultdict(list)
    for record in records:
        evidence = record.get("evidence", "").strip()
        if not evidence:
            continue
        db_evidences[record["db_id"]].append(evidence)
    return db_evidences


def write_documentation(db_path: Path, evidences: list[str]) -> None:
    desc_dir = db_path / "database_description"
    if not desc_dir.exists():
        print(f"  skipping {db_path.name}: no database_description directory")
        return
    out = desc_dir / "documentation.md"
    lines = ["# Documentation\n"]
    for evidence in evidences:
        lines.append(f"- {evidence}")
    out.write_text("\n".join(lines) + "\n")
    print(f"  wrote {len(evidences)} entries -> {out.relative_to(ROOT)}")


def main() -> None:
    for json_path, databases_dir in SOURCES:
        print(f"Processing {json_path.relative_to(ROOT)}")
        db_evidences = collect_evidences(json_path)
        for db_id, evidences in sorted(db_evidences.items()):
            write_documentation(databases_dir / db_id, evidences)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from text2sql.llm import print_messages
from text2sql.service import Text2SQLService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Text2SQL service request.")
    parser.add_argument("--db-name", required=True, help="Database id from dev set.")
    parser.add_argument("--question", required=True, help="Natural language question.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    service = Text2SQLService()
    response = service.ask(
        db_name=args.db_name,
        question=args.question,
    )
    if response.last_ai_message is None:
        print("No AI message returned.")
        return
    print_messages([response.last_ai_message])


if __name__ == "__main__":
    main()

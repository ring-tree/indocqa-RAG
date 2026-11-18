"""导出配置 JSON Schema 到 server/config/schemas/。"""

from __future__ import annotations

import json
from pathlib import Path

from app.config.models import AnswerConfig, EngineDefaults, KBConfig, UIConfig
from app.contracts.pack import DomainPack

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "config" / "schemas"

SCHEMAS = {
    "engine.schema.json": EngineDefaults,
    "kb.schema.json": KBConfig,
    "answer.schema.json": AnswerConfig,
    "ui.schema.json": UIConfig,
    "pack.schema.json": DomainPack,
}


def export_schemas(output_dir: Path = SCHEMA_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in SCHEMAS.items():
        path = output_dir / filename
        path.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


if __name__ == "__main__":
    for item in export_schemas():
        print(item)

#!/usr/bin/env python3
from pathlib import Path

DEFAULT_MODEL = ""  # Blank means use OpenCode's configured default model


def main() -> None:
    base = Path(__file__).resolve().parent
    env_path = base / ".env"

    prompt = "Enter APP_OPENCODE_MODEL (leave blank to use OpenCode's configured default): "
    model_input = input(prompt).strip()
    model = model_input if model_input else DEFAULT_MODEL

    content = f"APP_OPENCODE_MODEL={model}\n"
    env_path.write_text(content, encoding="utf-8")
    print(f"Wrote {env_path.name} in project directory.")


if __name__ == "__main__":
    main()

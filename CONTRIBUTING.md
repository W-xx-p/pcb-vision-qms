# Contributing

## Development setup

```bash
python -m venv .venv
pip install -e ".[dev]"
pytest -q
ruff check app tests scripts edge performance migrations
node --check app/static/app.js
pip check
docker compose config --quiet
```

## Pull requests

1. Keep changes focused and describe the manufacturing problem being solved.
2. Add or update tests for behavior changes.
3. Never commit keys, customer images, PCB designs, serial numbers, models, or databases.
4. Report model metrics with the dataset, split, hardware, and measurement method.
5. Distinguish simulated integrations from production-validated ones.
6. Keep migrations backward-aware and include a rollback or recovery note.
7. Run `pip check` when changing dependency declarations.

Contributions are licensed under the MIT License.
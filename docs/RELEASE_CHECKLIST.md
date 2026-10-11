# GitHub Release Checklist

## Repository hygiene

- [x] No `.env`, credentials, customer images, serial numbers, databases or model binaries are tracked.
- [x] README commands match the current Compose files and exposed ports.
- [x] LICENSE, SECURITY, CONTRIBUTING and CHANGELOG are present.
- [x] Dashboard screenshot contains no confidential production data.

## Verification

Validated locally on 2026-10-11; GitHub-hosted checks remain in the publication section below.

- [x] `ruff check app tests scripts edge performance migrations`
- [x] `pytest -q`
- [x] `node --check app/static/app.js`
- [x] `pip check`
- [x] `docker compose config --quiet`
- [x] Production overlay validates with an ignored, generated `.env.production`.
- [x] Application and ML Docker images build successfully.
- [x] `/api/ready` returns HTTP 200 in the intended deployment profile.

## GitHub publication

- [ ] Create the repository with the `main` default branch.
- [ ] Confirm all GitHub Actions jobs are green.
- [ ] Enable Dependabot alerts and private vulnerability reporting.
- [ ] Add repository topics such as `industrial-ai`, `anomaly-detection`, `pcb`, `fastapi`, and `mlops`.
- [ ] Create release `v0.1.0` and summarize validated capabilities and known boundaries.

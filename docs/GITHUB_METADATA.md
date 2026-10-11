# GitHub Repository Metadata

## Repository

- Name: `pcb-vision-qms`
- Visibility: Public
- Default branch: `main`
- Description: `Enterprise-oriented few-shot PCB anomaly detection, quality traceability and MLOps platform for electronics manufacturing.`
- Website: leave empty until a verified deployment is available.

## Topics

`industrial-ai`, `anomaly-detection`, `pcb`, `computer-vision`, `manufacturing`, `quality-management`, `fastapi`, `patchcore`, `mlops`, `postgresql`, `redis`, `minio`, `docker`

## Suggested first release

- Tag: `v0.1.0`
- Title: `PCB Vision QMS v0.1.0 — Enterprise PoC`

### Release summary

This release provides an enterprise-oriented PoC for few-shot PCB visual inspection. It
includes inspection and heatmap generation, human review, traceability, SPC and quality alerts,
model governance, dataset lineage, Redis-backed training, PostgreSQL migrations, MinIO artifact
storage, MES/PLC integration, edge acquisition, monitoring and production deployment assets.

The release does not claim factory production accuracy or availability. Those outcomes require
validation with target cameras, lighting, defect samples, PLC/MES protocols and site-specific
acceptance criteria.

## Repository settings after upload

1. Enable Issues and private vulnerability reporting.
2. Require the `Enterprise acceptance` workflow before merging to `main`.
3. Enable Dependabot alerts and dependency graph.
4. Use `docs/screenshots/dashboard.png` as the repository social preview if desired.
5. Create release `v0.1.0` only after the first GitHub Actions run is green.
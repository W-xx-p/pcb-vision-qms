# Architecture

## Scope

PCB Vision QMS is an enterprise-oriented proof of concept for electronic manufacturing
inspection. It separates synchronous inspection traffic, asynchronous model training,
industrial integration and observability so each workload can be scaled and audited
independently.

## Components

| Component | Responsibility | Production dependency |
|---|---|---|
| FastAPI service | Product configuration, inspection, review, traceability and APIs | PostgreSQL, Redis, MinIO |
| ML worker | PatchCore training, validation and artifact publication | Redis Streams, MinIO |
| Integration worker | Idempotent MES webhook and PLC result delivery | Transactional Outbox |
| Drift worker | PSI monitoring and controlled retraining requests | PostgreSQL, Redis |
| Edge agent | Camera/replay acquisition, offline spool and station heartbeat | HTTP API, optional OpenCV |
| PostgreSQL | Transactional quality records, lineage, audit and Outbox | Persistent volume and backups |
| MinIO | Images, heatmaps, model artifacts and backup replicas | Persistent volume or external object store |
| Prometheus stack | Metrics, alert rules, dashboards and alert routing | Restricted management network |

## Critical data flow

1. The edge station captures a product image and sends traceability metadata to the API.
2. The API validates identity, station state and duplicate serial numbers.
3. The active model produces an anomaly score, heatmap and defect regions.
4. Inspection, alerts, audit records and Outbox events commit in one database transaction.
5. Images and signed model artifacts are stored in MinIO; database rows keep immutable references.
6. Integration workers deliver idempotent events to MES/QMS and PLC endpoints.
7. Human review feeds evaluation, threshold calibration, drift analysis and retraining queues.

## Deployment profiles

- Local development: SQLite and local files are supported for fast tests and demonstrations.
- Enterprise Compose: PostgreSQL, Redis, MinIO, workers and monitoring run as separate services.
- Production overlay: requires strong credentials, secure cookies, signed models and TLS ingress.

## Deliberate boundaries

This repository does not claim production accuracy, latency or availability without target-line
acceptance. Camera optics, lighting, defect taxonomy, PLC protocol, MES schema, capacity planning,
high availability and disaster recovery must be validated for each factory.
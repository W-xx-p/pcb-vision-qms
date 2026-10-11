# Security Policy

## Supported versions

Security fixes are applied to the latest version on the default branch.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue. Use GitHub private
vulnerability reporting, or contact the repository owner privately if unavailable.

Include the affected component, reproduction steps, impact, and suggested mitigation.
Never include production credentials, customer images, serial numbers, or confidential data.

## Deployment guidance

- Configure role-scoped keys through `PCB_QMS_API_KEYS`.
- Set `PCB_QMS_ENV=production`; startup validation rejects missing or weak credentials.
- Set `PCB_QMS_SECURE_COOKIES=1` behind HTTPS.
- Use PostgreSQL, authenticated Redis and MinIO with independently rotated credentials.
- Configure `PCB_QMS_MODEL_SIGNING_KEY` to verify model artifacts before loading.
- Store TLS private keys and runtime data outside the source tree on protected volumes.
- Rotate credentials and review audit logs regularly.
- Restrict Grafana, Prometheus, PostgreSQL and MinIO management ports to trusted networks.
- Run backup restoration drills; a backup that has not been restored is not verified.

This industrial AI proof of concept has not undergone an independent penetration test.
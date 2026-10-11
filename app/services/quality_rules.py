from __future__ import annotations

from app.database import DatabaseConnection, connect, fetch_all, utc_now


def evaluate_quality_rules(
    product_id: int,
    inspection_id: int,
    db: DatabaseConnection | None = None,
) -> list[int]:
    """Evaluate rolling anomaly rules and open deduplicated alerts."""

    query_all = db.fetch_all if db else fetch_all
    rules = query_all(
        """
        SELECT * FROM quality_rules
        WHERE enabled = TRUE AND (product_id IS NULL OR product_id = ?)
        """,
        (product_id,),
    )
    created: list[int] = []
    for rule in rules:
        rows = query_all(
            """
            SELECT decision FROM inspections WHERE product_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (product_id, int(rule["window_size"])),
        )
        if not rows:
            continue
        anomaly_count = sum(row["decision"] == "anomaly" for row in rows)
        rate = anomaly_count / len(rows)
        consecutive = 0
        for row in rows:
            if row["decision"] != "anomaly":
                break
            consecutive += 1
        reasons = []
        if len(rows) >= min(5, int(rule["window_size"])) and rate > float(rule["max_anomaly_rate"]):
            reasons.append(f"最近{len(rows)}件异常率{rate:.1%}")
        if consecutive >= int(rule["max_consecutive_anomalies"]):
            reasons.append(f"连续{consecutive}件异常")
        if not reasons:
            continue
        rule_id = int(rule["id"])
        severity = (
            "critical"
            if consecutive >= int(rule["max_consecutive_anomalies"])
            else "warning"
        )
        message = "；".join(reasons)

        def create_alert(
            connection: DatabaseConnection,
            current_rule_id: int = rule_id,
            current_severity: str = severity,
            current_message: str = message,
        ) -> None:
            existing = connection.execute(
                """
                SELECT id FROM quality_alerts
                WHERE rule_id = ? AND product_id = ? AND status = 'open'
                """,
                (current_rule_id, product_id),
            ).fetchone()
            if existing:
                return
            cursor = connection.execute(
                """
                INSERT INTO quality_alerts(
                    rule_id, product_id, inspection_id, severity, message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?) RETURNING id
                """,
                (
                    current_rule_id,
                    product_id,
                    inspection_id,
                    current_severity,
                    current_message,
                    utc_now(),
                ),
            )
            created.append(int(cursor.scalar_one()))
        if db:
            create_alert(db)
        else:
            with connect() as connection:
                create_alert(connection)
    return created


def calculate_spc(product_id: int, limit: int) -> dict:
    rows = fetch_all(
        """
        SELECT id, anomaly_score, decision, created_at FROM inspections
        WHERE product_id = ? ORDER BY id DESC LIMIT ?
        """,
        (product_id, limit),
    )
    rows.reverse()
    scores = [float(row["anomaly_score"]) for row in rows]
    if not scores:
        return {"ready": False, "sample_count": 0, "message": "暂无检测数据"}
    mean = sum(scores) / len(scores)
    variance = sum((value - mean) ** 2 for value in scores) / max(1, len(scores) - 1)
    sigma = variance**0.5
    ucl = min(1.0, mean + 3 * sigma)
    lcl = max(0.0, mean - 3 * sigma)
    points = [{**row, "out_of_control": float(row["anomaly_score"]) > ucl} for row in rows]
    return {
        "ready": len(scores) >= 5,
        "sample_count": len(scores),
        "mean": round(mean, 6),
        "sigma": round(sigma, 6),
        "ucl": round(ucl, 6),
        "lcl": round(lcl, 6),
        "out_of_control_count": sum(point["out_of_control"] for point in points),
        "points": points,
    }

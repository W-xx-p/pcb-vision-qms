"""Edge station agent: camera/folder acquisition, QMS upload and local spooling."""
from __future__ import annotations

import argparse
import json
import logging
import random
import signal
import time
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pcb_qms.edge")
running = True


def _stop(*_: object) -> None:
    global running
    running = False


def validate_config(config: dict) -> dict:
    required = ("server", "api_key", "station_code", "product_id")
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise ValueError(f"边缘工位配置缺少字段: {', '.join(missing)}")
    recipe_name = config.get("recipe")
    if recipe_name:
        recipe = config.get("recipes", {}).get(recipe_name)
        if not recipe:
            raise ValueError(f"配方不存在: {recipe_name}")
        config = {**config, **recipe, "recipe": recipe_name}
    config["product_id"] = int(config["product_id"])
    config["max_spool_bytes"] = int(config.get("max_spool_bytes", 2 * 1024**3))
    return config


def capture_camera(source: str, destination: Path) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("相机模式需要安装opencv-python-headless") from exc
    camera_source = int(source) if source.isdigit() else source
    capture = cv2.VideoCapture(camera_source)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"无法从相机采集: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), frame):
        raise RuntimeError("相机图像写入失败")


def upload(config: dict, image: Path, serial: str) -> dict:
    headers = {"X-API-Key": config["api_key"], "Idempotency-Key": f"{config['station_code']}:{serial}"}
    data = {"station_code": config["station_code"], "serial_number": serial, "lot_number": config.get("lot_number", ""), "shift": config.get("shift", "")}
    with image.open("rb") as stream:
        response = httpx.post(f"{config['server'].rstrip('/')}/api/products/{config['product_id']}/inspect", headers=headers, data=data, files={"file": (image.name, stream, "image/jpeg")}, timeout=30, verify=config.get("ca_file", True))
    response.raise_for_status()
    return response.json()


def heartbeat(config: dict) -> None:
    station_id = config.get("station_id")
    if not station_id:
        return
    response = httpx.post(
        f"{config['server'].rstrip('/')}/api/stations/{station_id}/heartbeat",
        headers={"X-API-Key": config["api_key"]},
        json={"software_version": config.get("software_version", "edge-1.0")},
        timeout=10,
        verify=config.get("ca_file", True),
    )
    response.raise_for_status()


def spool_size(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.glob("*.jpg") if path.is_file())


def run(config: dict) -> None:
    config = validate_config(config)
    spool = Path(config.get("spool_dir", "edge-spool"))
    spool.mkdir(parents=True, exist_ok=True)
    replay = Path(config["replay_dir"]) if config.get("replay_dir") else None
    failures = 0
    last_heartbeat = 0.0
    while running:
        try:
            now = time.monotonic()
            if now - last_heartbeat >= float(config.get("heartbeat_seconds", 30)):
                heartbeat(config)
                last_heartbeat = now
            pending = sorted(spool.glob("*.jpg"), key=lambda path: path.stat().st_mtime)
            if pending:
                image = pending[0]
                serial = image.stem
            else:
                if spool_size(spool) >= config["max_spool_bytes"]:
                    raise RuntimeError("离线缓存达到容量上限，暂停采集以防磁盘耗尽")
                serial = str(int(time.time() * 1000))
                image = spool / f"{serial}.jpg"
                temporary = spool / f".{serial}.capture.jpg"
                if replay:
                    candidate = next(iter(sorted(path for path in replay.glob("*") if path.is_file())))
                    temporary.write_bytes(candidate.read_bytes())
                    temporary.replace(image)
                    candidate.unlink()
                else:
                    capture_camera(str(config.get("camera", "0")), temporary)
                    temporary.replace(image)
            result = upload(config, image, serial)
            logger.info("inspection=%s decision=%s score=%s", result["id"], result["decision"], result["anomaly_score"])
            image.unlink(missing_ok=True)
            failures = 0
        except StopIteration:
            time.sleep(1)
        except Exception:
            failures += 1
            logger.exception("capture/upload failed; image retained in spool")
            delay = min(60.0, 2.0 ** min(failures, 6))
            time.sleep(delay + random.uniform(0, delay * 0.2))
        time.sleep(float(config.get("interval_seconds", 1)))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run(json.loads(Path(args.config).read_text(encoding="utf-8")))

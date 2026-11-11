"""Detecção de layout do VOD: câmeras (rostos estáveis) e a janela da gameplay (região com movimento)."""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from corteviral.vision.frames import grab, video_size

log = logging.getLogger(__name__)
FaceBox = tuple[float, float, float, float, float]  # x, y, w, h (0–1), confiança
YUNET_FILE = "face_detection_yunet_2023mar.onnx"
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
CAM_W, CAM_H, FACE_Y = 3.5, 2.2, 0.45  # câmera ≈ 3,5× a largura e 2,2× a altura do rosto
N_FRAMES = 24
INSET = 0.01


def _clamp_rect(x: float, y: float, w: float, h: float) -> list[float]:
    w, h = min(w, 1.0), min(h, 1.0)
    x = min(max(x, 0.0), 1.0 - w)
    y = min(max(y, 0.0), 1.0 - h)
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def cam_rect(face: list[float]) -> list[float]:
    fx, fy, fw, fh = face[:4]
    w, h = CAM_W * fw, CAM_H * fh
    cx, cy = fx + fw / 2, fy + fh / 2
    return _clamp_rect(cx - w / 2, cy - FACE_Y * h, w, h)


def cluster_faces(per_frame: list[list[FaceBox]], min_share: float = 0.5, tol: float = 0.04) -> list[dict]:
    """Agrupa rostos pela posição; só fica o que aparece em ≥ min_share dos quadros (câmeras fixas)."""
    total = len(per_frame)
    if total == 0:
        return []
    clusters: list[dict] = []
    for fi, dets in enumerate(per_frame):
        for x, y, w, h, conf in dets:
            cx, cy = x + w / 2, y + h / 2
            for c in clusters:
                if abs(c["cx"] - cx) <= tol and abs(c["cy"] - cy) <= tol:
                    break
            else:
                c = {"cx": cx, "cy": cy, "boxes": [], "frames": set(), "confs": []}
                clusters.append(c)
            c["boxes"].append((x, y, w, h))
            c["confs"].append(conf)
            c["frames"].add(fi)
            n = len(c["boxes"])
            c["cx"] += (cx - c["cx"]) / n
            c["cy"] += (cy - c["cy"]) / n
    out = []
    for c in clusters:
        share = len(c["frames"]) / total
        if share >= min_share:
            med = np.median(np.array(c["boxes"]), axis=0)
            out.append({"face": [round(float(v), 4) for v in med], "share": round(share, 3),
                        "conf": round(float(np.mean(c["confs"])), 3)})
    return sorted(out, key=lambda c: (-c["share"], -c["conf"]))


def motion_region(frames: list[np.ndarray], mask_rects: list[list[float]] | None = None) -> list[float] | None:
    """Maior região que se mexe ao longo do vídeo (a janela da gameplay); None se nada se mexe."""
    if len(frames) < 2:
        return None
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames])
    std = gray.std(axis=0)
    H, W = std.shape
    for x, y, w, h in mask_rects or []:
        std[int(y * H):int((y + h) * H) + 1, int(x * W):int((x + w) * W) + 1] = 0
    if std.max() < 2:
        return None
    img = cv2.normalize(std, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, mask = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = max(3, int(min(H, W) * 0.03) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[i]
    if w * h < 0.15 * H * W:
        return None
    # recua um pouco para dentro: a borda/moldura do overlay não entra no recorte
    return _clamp_rect(x / W + INSET, y / H + INSET, w / W - 2 * INSET, h / H - 2 * INSET)


def _yunet(models_dir: Path) -> Path | None:
    path = Path(models_dir) / "face" / YUNET_FILE
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(YUNET_URL, path)
        except OSError as e:
            log.warning("Não consegui baixar o detector YuNet (%s); usando o detector básico.", e)
            return None
    return path


def _detector(models_dir: Path, size: tuple[int, int]):
    model = _yunet(models_dir)
    if model:
        det = cv2.FaceDetectorYN.create(str(model), "", size, 0.7)
        return lambda img: [] if (faces := det.detect(img)[1]) is None else [
            (float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])) for f in faces]
    haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return lambda img: [(float(x), float(y), float(w), float(h), 0.6) for x, y, w, h in
                        haar.detectMultiScale(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(20, 20))]


def analyze_layout(video: Path, duration: float, models_dir: Path) -> dict:
    times = [duration * (0.05 + 0.9 * i / (N_FRAMES - 1)) for i in range(N_FRAMES)]
    frames = grab(video, times, width=960)
    if not frames:
        return {"cams": [], "gameplay": None, "source": list(video_size(video))}
    H, W = frames[0].shape[:2]
    detect = _detector(models_dir, (W, H))
    per_frame = [[(x / W, y / H, w / W, h / H, c) for x, y, w, h, c in detect(f)] for f in frames]
    cams = [{**c, "rect": cam_rect(c["face"])} for c in cluster_faces(per_frame)]
    small = [cv2.resize(f, (320, round(320 * H / W))) for f in frames]
    gameplay = motion_region(small, [c["rect"] for c in cams])
    return {"cams": cams, "gameplay": gameplay, "source": list(video_size(video))}

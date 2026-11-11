import numpy as np

from corteviral.vision.detect import cam_rect, cluster_faces, motion_region
from corteviral.vision.frames import grab

CAMS = [(0.89, 0.22, 0.037, 0.085), (0.90, 0.41, 0.039, 0.09), (0.90, 0.62, 0.038, 0.085), (0.887, 0.83, 0.04, 0.09)]


def test_cluster_keeps_stable_faces_only():
    frames = []
    for i in range(10):
        dets = [(x + 0.003 * (i % 2), y, w, h, 0.92) for x, y, w, h in CAMS]
        if i in (3, 7):  # personagem do jogo aparecendo de vez em quando
            dets.append((0.5, 0.3 + 0.05 * i, 0.1, 0.25, 0.95))
        frames.append(dets)
    clusters = cluster_faces(frames)
    assert len(clusters) == 4
    assert all(c["share"] == 1.0 for c in clusters)
    assert all(c["face"][0] > 0.85 for c in clusters)


def test_cluster_orders_by_share_then_conf():
    frames = [[(0.1, 0.1, 0.05, 0.1, 0.80), (0.8, 0.1, 0.05, 0.1, 0.95)] for _ in range(6)]
    frames += [[(0.1, 0.1, 0.05, 0.1, 0.80)] for _ in range(4)]  # câmera da esquerda aparece mais
    clusters = cluster_faces(frames)
    assert clusters[0]["face"][0] < 0.2 and clusters[0]["share"] == 1.0
    assert clusters[1]["share"] == 0.6


def test_cam_rect_expands_and_clamps():
    r = cam_rect([0.95, 0.05, 0.04, 0.09])
    x, y, w, h = r
    assert 0 <= x and 0 <= y and x + w <= 1.0001 and y + h <= 1.0001
    r2 = cam_rect([0.5, 0.5, 0.04, 0.09])
    assert abs(r2[2] - 0.14) < 0.005 and abs(r2[3] - 0.198) < 0.005
    # rosto fica um pouco acima do centro da câmera
    face_cy = 0.5 + 0.045
    assert r2[1] < face_cy < r2[1] + r2[3]


def test_motion_region_finds_moving_window():
    rng = np.random.default_rng(0)
    H, W = 180, 320
    frames = []
    for _ in range(12):
        img = np.full((H, W, 3), 120, np.uint8)
        y0, y1, x0, x1 = int(0.15 * H), int(0.95 * H), int(0.10 * W), int(0.80 * W)
        img[y0:y1, x0:x1] = rng.integers(0, 255, (y1 - y0, x1 - x0, 3), dtype=np.uint8)
        frames.append(img)
    x, y, w, h = motion_region(frames)
    assert abs(x - 0.10) < 0.03 and abs(y - 0.15) < 0.03
    assert abs(w - 0.70) < 0.03 and abs(h - 0.80) < 0.03


def test_motion_region_none_when_static():
    frames = [np.full((90, 160, 3), 50, np.uint8) for _ in range(5)]
    assert motion_region(frames) is None


def test_grab_returns_frames_from_sample_video(sample_video):
    imgs = grab(sample_video, [1.0, 4.0, 99.0], width=320)
    assert len(imgs) == 2
    assert imgs[0].shape == (180, 320, 3)


def test_motion_region_is_inset_to_skip_border():
    rng = np.random.default_rng(1)
    H, W = 180, 320
    frames = []
    for _ in range(10):
        img = np.full((H, W, 3), 120, np.uint8)
        img[27:171, 32:256] = rng.integers(0, 255, (144, 224, 3), dtype=np.uint8)
        frames.append(img)
    x, y, w, h = motion_region(frames)
    assert x > 0.10 and y > 0.15  # recuado para dentro da borda
    assert x + w < 0.80 and y + h < 0.95

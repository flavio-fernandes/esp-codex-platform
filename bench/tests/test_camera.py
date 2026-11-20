"""bench-camera with a fake v4l2-ctl on PATH. No camera."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from PIL import Image
from benchlib import camera, locks

TESTS = Path(__file__).resolve().parent


def jpeg(colour=(200, 30, 20), size=(64, 48)):
    img = Image.new("RGB", size, (0, 0, 0))
    img.paste(colour, (0, 0, size[0] // 2, size[1]))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


class Cam:
    def __init__(self, fake_dir, monkeypatch):
        self.dir, self.monkeypatch = fake_dir, monkeypatch

    def mode(self, mode):
        self.monkeypatch.setenv("FAKE_V4L2_MODE", mode)

    def calls(self):
        log = self.dir / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def captures(self):
        return [c for c in self.calls() if any(a.startswith("--stream-to=") for a in c)]


@pytest.fixture
def cam(roots, tmp_path, monkeypatch):
    fake_dir = tmp_path / "fakecam"
    bin_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    bin_dir.mkdir()
    (fake_dir / "frame.jpg").write_bytes(jpeg())
    tool = bin_dir / "v4l2-ctl"
    tool.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(TESTS / 'bench_fake_v4l2.py')!r},"
                    " run_name='__main__')\n")
    tool.chmod(0o755)
    device = tmp_path / "video9"
    device.touch()
    (roots["etc"] / "bench.env").write_text(
        f"BENCH_CAMERA_DEVICE={device}\nBENCH_CAMERA_EXPOSURE=40\nBENCH_CAMERA_WIDTH=640\n"
        "BENCH_CAMERA_HEIGHT=480\nBENCH_CAMERA_SKIP=3\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_V4L2_DIR", str(fake_dir))
    monkeypatch.setenv("BENCH_CAMERA_LOCK_WAIT", "0.2")
    result = Cam(fake_dir, monkeypatch)
    result.device, result.out = str(device), tmp_path / "snap.jpg"
    return result


def test_snap_sets_exposure_then_captures_mjpg(cam):
    assert camera.main(["snap", str(cam.out)]) == 0
    data = cam.out.read_bytes()
    assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")  # padding trimmed
    calls = cam.calls()
    assert calls[0] == ["-d", cam.device, "-c", "auto_exposure=1"]
    assert calls[1] == ["-d", cam.device, "-c", "exposure_time_absolute=40"]
    assert calls[2][:5] == [f"--device={cam.device}", "--set-fmt-video=width=640,height=480,pixelformat=MJPG",
                            "--stream-mmap", "--stream-skip=3", "--stream-count=1"]
    assert not list(cam.out.parent.glob(".snap.*"))


def test_snap_options_override_env_and_auto_exposure_is_set_every_call(cam):
    assert camera.main(["snap", str(cam.out), "exposure=auto", "res=320x240", "skip=0"]) == 0
    calls = cam.calls()
    assert calls[0] == ["-d", cam.device, "-c", "auto_exposure=3"]
    assert "--set-fmt-video=width=320,height=240,pixelformat=MJPG" in calls[1]
    assert "--stream-skip=0" in calls[1]


@pytest.mark.parametrize("opts", [["exposure=-1"], ["exposure=bright"], ["res=640"], ["res=axb"],
                                  ["threshold=5"], ["device=/dev/sda"], ["--device=/dev/sda"], ["exposure="]])
def test_snap_bad_options_usage(cam, opts, capsys):
    assert camera.main(["snap", str(cam.out), *opts]) == 2
    assert cam.calls() == [] and not cam.out.exists()


def test_snap_missing_device_is_usage(cam, capsys):
    os.unlink(cam.device)
    assert camera.main(["snap", str(cam.out)]) == 2
    assert "camera device not found" in capsys.readouterr().err and cam.calls() == []


def test_truncated_jpeg_retried_once_then_ok(cam, capsys):
    cam.mode("truncated-once")
    assert camera.main(["snap", str(cam.out)]) == 0
    assert len(cam.captures()) == 2
    assert "retrying once" in capsys.readouterr().err
    assert cam.out.read_bytes().endswith(b"\xff\xd9")


def test_truncated_jpeg_twice_exits_4(cam, capsys):
    cam.mode("truncated")
    assert camera.main(["snap", str(cam.out)]) == 4
    assert len(cam.captures()) == 2
    assert not cam.out.exists() and not list(cam.out.parent.glob(".snap.*"))


def test_hanging_v4l2_ctl_times_out_with_3(cam, monkeypatch, capsys):
    cam.mode("hang")
    monkeypatch.setenv("BENCH_CAMERA_SNAP_TIMEOUT", "1.5")
    started = time.monotonic()
    assert camera.main(["snap", str(cam.out)]) == 3
    assert time.monotonic() - started < 4
    assert "camera timeout" in capsys.readouterr().err
    assert not cam.out.exists()
    with locks.camera_lock():  # released after the timeout
        pass


def test_hanging_entry_point_kills_the_process_group(cam, monkeypatch):
    cam.mode("hang")
    monkeypatch.setenv("BENCH_CAMERA_SNAP_TIMEOUT", "1")
    script = TESTS.parent / "bin/bench-camera"
    # With a pipe on stdout, an orphaned grandchild would keep communicate() waiting for 300 s.
    result = subprocess.run([sys.executable, str(script), "snap", str(cam.out)], capture_output=True,
                            text=True, timeout=10, check=False)
    assert result.returncode == 3 and "camera timeout" in result.stderr and result.stdout == ""


def test_v4l2_ctl_failure_is_1_without_frame_marks(cam, capsys):
    cam.mode("fail")
    assert camera.main(["snap", str(cam.out)]) == 1
    err = capsys.readouterr().err
    assert "VIDIOC_STREAMON" in err and "<" not in err


def test_camera_lock_held_for_the_capture_and_busy_is_65(cam, capsys):
    with locks.camera_lock():
        assert camera.main(["snap", str(cam.out)]) == 65
    assert "camera busy" in capsys.readouterr().err and cam.calls() == []
    assert camera.main(["snap", str(cam.out)]) == 0


def test_lock_is_held_during_v4l2_calls(cam, monkeypatch):
    seen = []
    real = camera.v4l2

    def probe(args, deadline):
        try:
            with locks.camera_lock():
                seen.append("free")
        except locks.Busy:
            seen.append("held")
        return real(args, deadline)
    monkeypatch.setattr(camera, "v4l2", probe)
    assert camera.main(["snap", str(cam.out)]) == 0
    assert seen == ["held", "held", "held"]


def test_burst_splits_frames(cam, tmp_path, capsys):
    cam.mode("burst")
    outdir = tmp_path / "burst"
    assert camera.main(["burst", str(outdir), "5", "exposure=auto"]) == 0
    assert capsys.readouterr().out.strip() == "5"
    frames = sorted(p.name for p in outdir.iterdir())
    assert frames == [f"frame-{n:03d}.jpg" for n in range(1, 6)]
    assert "--stream-count=5" in cam.captures()[0]
    Image.open(outdir / "frame-003.jpg").verify()


@pytest.mark.parametrize("count", ["0", "301", "x", "-1"])
def test_burst_count_range(cam, tmp_path, count):
    assert camera.main(["burst", str(tmp_path / "b"), count]) == 2 and cam.calls() == []


def test_burst_without_complete_frame_is_4(cam, tmp_path):
    cam.mode("truncated")
    assert camera.main(["burst", str(tmp_path / "b"), "2"]) == 4


def test_stats_matches_old_math(cam, tmp_path, capsys):
    path = tmp_path / "in.jpg"
    path.write_bytes(jpeg((200, 30, 20)))
    assert camera.main(["stats", str(path)]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["dominant"] == "R" and 0.45 < res["lit_fraction"] < 0.55
    assert abs(res["lit_pixels"] - 64 * 48 // 2) < 100
    assert abs(res["mean_rgb"][0] - 200) < 6 and res["purity"] > 0.7
    assert camera.main(["stats", str(path), "threshold=255"]) == 0
    assert json.loads(capsys.readouterr().out) == {"lit_fraction": 0.0, "lit_pixels": 0, "dominant": "none"}


def test_stats_usage(cam, tmp_path):
    assert camera.main(["stats", str(tmp_path / "missing.jpg")]) == 2
    assert camera.main(["stats", str(tmp_path / "x.jpg"), "exposure=5"]) == 2
    assert camera.main([]) == 2 and camera.main(["record"]) == 2

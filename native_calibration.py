"""Offline bridge from saved public RoboDojo observations to sketch geometry.

Reuses the same pinned nominal camera model as robot.camera(). No simulator,
object pose, private reward, or robot command is accessed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image

CHECKOUT = Path("/data/disk_14t/ypwen/zyh/hexarua/upstream/HexaShell")
COMMIT = "e0190ddb757955131578e9f9071f4ee8e7113f7a"


def decode_public(value, arrays):
    if isinstance(value, dict):
        if "npz_key" in value:
            array = arrays[value["npz_key"]]
            if list(array.shape) != value["shape"] or str(array.dtype) != value["dtype"]:
                raise ValueError("Public array does not match its declared shape/dtype")
            return array
        return {key: decode_public(item, arrays) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_public(item, arrays) for item in value]
    return value


def from_observation(folder, *, camera, observation_id, frame_aligned=False):
    folder = Path(folder)
    if camera not in {"head", "left_wrist", "right_wrist"}:
        raise ValueError("Use the native head/left_wrist/right_wrist camera")
    if not frame_aligned and camera != "head":
        raise ValueError("Wrist calibration requires explicitly aligned public robot state and RGB")
    current = subprocess.check_output(["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"], text=True).strip()
    if current != COMMIT:
        raise ValueError("The camera model checkout differs from the pinned runtime")
    source = folder / "observation.json"
    archive = folder / "arrays.npz"
    with np.load(archive, allow_pickle=False) as arrays:
        observation = decode_public(json.loads(source.read_text()), arrays)
    if camera != "head":
        pose = observation.get("state", {}).get(camera.split("_")[0] + "_ee_pose")
        if pose is None or np.asarray(pose).shape != (7,) or not np.isfinite(pose).all():
            raise ValueError("Wrist camera requires the public world-flange xyz+wxyz pose")
    lag = ((observation.get("additional_info") or {}).get("hexashell") or {}).get("pictures_behind", 0)
    if lag and camera != "head":
        raise ValueError("Wrist RGB is explicitly behind the saved state; do not infer a camera pose")
    image = folder / f"current_cam_{camera}.png"
    rgb = np.asarray(Image.open(image).convert("RGB"))
    public_rgb = observation["vision"]["cam_" + camera]["color"]
    if not np.array_equal(rgb, public_rgb):
        raise ValueError("Current PNG differs from the public observation RGB")
    sys.path.insert(0, str(CHECKOUT))
    from hexashell.state import parse_obs
    from hexashell.vision.cameras import cameras_for
    model = cameras_for(parse_obs(observation, seq=0))[camera]
    if (rgb.shape[1], rgb.shape[0]) != (model.width, model.height):
        raise ValueError("Image resolution differs from the nominal runtime camera model")
    # Native axes: x right, y up, z backwards. OpenCV: x right, y down,
    # z forwards. This proper 180-degree rotation is a convention change.
    cv_pose = model.pose @ np.diag([1., -1., -1., 1.])
    k = [[model.fx, 0., model.cx], [0., model.fy, model.cy], [0., 0., 1.]]
    camera_source = CHECKOUT / "hexashell/vision/cameras.py"
    calibration = {"camera": camera, "observation_id": observation_id,
        "image_file_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "convention": "opencv-camera-to-world", "intrinsics": k, "camera_to_world": cv_pose.tolist(),
        "image_size_wh": [model.width, model.height],
        "kind": "same nominal runtime camera model as robot.camera(); not an independent physical calibration",
        "pose_source": "fixed head mount" if camera == "head" else "saved public world-flange pose + nominal wrist mount",
        "frame_alignment": "fixed camera" if camera == "head" else "caller explicitly asserts state/RGB alignment",
        "source_observation_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_arrays_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "camera_model_commit": COMMIT, "camera_model_source_sha256": hashlib.sha256(camera_source.read_bytes()).hexdigest()}
    return calibration, model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--camera", choices=["head", "left_wrist", "right_wrist"], required=True)
    parser.add_argument("--observation-id", required=True)
    parser.add_argument("--frame-aligned", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calibration, _ = from_observation(args.observation, camera=args.camera,
        observation_id=args.observation_id, frame_aligned=args.frame_aligned)
    args.output.write_text(json.dumps(calibration, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"camera": calibration["camera"], "kind": calibration["kind"], "output": str(args.output)}))


if __name__ == "__main__":
    main()

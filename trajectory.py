"""Human sketch -> frozen-student conditioning, with explicit depth ambiguity."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def validate(sketch: dict) -> dict:
    if sketch.get("schema") != "rss-trajectory-sketch-v1":
        raise ValueError("Unsupported sketch schema")
    image = sketch["image"]
    w, h = image["width"], image["height"]
    if type(w) is not int or type(h) is not int or min(w, h) <= 0:
        raise ValueError("Image dimensions must be positive integers")
    if sketch.get("arm") not in ["left", "right"]:
        raise ValueError("Select left or right arm")
    if sketch.get("camera") not in ["head", "left_wrist", "right_wrist"]:
        raise ValueError("Select the native observation camera")
    if sketch.get("point_semantics", "unspecified") not in ["unspecified", "gripper_tcp", "object_reference_point"]:
        raise ValueError("Declare whether points refer to the gripper TCP, object, or remain unspecified")
    if not sketch.get("observation_id") or not image.get("file_sha256"):
        raise ValueError("Sketch must identify its original public observation")
    digest = image["file_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Image SHA256 must contain 64 lowercase hex characters")
    if not sketch.get("waypoints"):
        raise ValueError("At least one waypoint is required")
    for point in sketch["waypoints"]:
        x, y = point["x"], point["y"]
        if not all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) for v in [x, y]):
            raise ValueError("Waypoint coordinates must be finite numbers")
        if not 0 <= x < w or not 0 <= y < h:
            raise ValueError("Waypoint lies outside original image dimensions")
        if point.get("gripper", "keep") not in ["keep", "open", "close"]:
            raise ValueError("Invalid gripper event")
        height = point.get("world_height_m")
        if height is not None and (not isinstance(height, (float, int)) or isinstance(height, bool) or not math.isfinite(height)):
            raise ValueError("Height must be explicitly supplied as finite world metres")
    return sketch


def pixel_to_plane(xy, height, intrinsics, camera_to_world):
    """OpenCV camera axes; do not infer a plane from a 2D trajectory."""
    k, transform = np.asarray(intrinsics, float), np.asarray(camera_to_world, float)
    if k.shape != (3, 3) or transform.shape != (4, 4) or not np.isfinite(k).all() or not np.isfinite(transform).all():
        raise ValueError("Provide finite 3x3 K and 4x4 camera-to-world transform")
    if not np.allclose(transform[3], [0, 0, 0, 1]):
        raise ValueError("Invalid homogeneous camera transform")
    rotation = transform[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5) or not np.isclose(np.linalg.det(rotation), 1., atol=1e-5):
        raise ValueError("camera_to_world must use a proper rotation")
    ray = rotation @ np.linalg.solve(k, np.r_[xy, 1.])
    if abs(ray[2]) < 1e-10:
        raise ValueError("Ray is parallel to requested plane")
    scale = (float(height) - transform[2, 3]) / ray[2]
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Requested plane is behind the camera")
    return (transform[:3, 3] + scale * ray).tolist()


def validate_source_image(sketch, path):
    from PIL import Image
    validate(sketch)
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != sketch["image"]["file_sha256"]:
        raise ValueError("Sketch and supplied source image differ")
    with Image.open(path) as image:
        if image.size != (sketch["image"]["width"], sketch["image"]["height"]):
            raise ValueError("Sketch dimensions differ from its actual source image")


def retained_points(sketch, tolerance):
    """Reduce a polyline, retaining directives, height changes and reversals.

    This is ordinary geometric preprocessing, not a learned trajectory policy.
    Tolerance is measured in original-image pixels, never display pixels.
    """
    if not isinstance(tolerance, (float, int)) or isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Polyline tolerance must be finite and nonnegative")
    points = sketch["waypoints"]
    if tolerance == 0 or len(points) <= 2:
        return list(range(len(points)))
    xy = np.asarray([[p["x"], p["y"]] for p in points], dtype=float)
    anchors = {0, len(points) - 1}
    for i in range(1, len(points)):
        before, after = points[i-1], points[i]
        if (before.get("gripper", "keep"), before.get("world_height_m")) != (after.get("gripper", "keep"), after.get("world_height_m")):
            anchors.update([i-1, i])
    previous_delta, previous_endpoint = None, None
    for i in range(1, len(points)):
        delta = xy[i] - xy[i-1]
        if np.dot(delta, delta) == 0:
            continue
        if previous_delta is not None and np.dot(previous_delta, delta) < 0:
            # Repeated clicks at a turning point must not hide a reversal.
            anchors.update([previous_endpoint, i-1])
        previous_delta, previous_endpoint = delta, i
    keep = set(anchors)
    pending = list(zip(sorted(anchors)[:-1], sorted(anchors)[1:]))
    while pending:
        left, right = pending.pop()
        if right - left <= 1:
            continue
        segment = xy[right] - xy[left]
        square = float(np.dot(segment, segment))
        offsets = xy[left+1:right] - xy[left]
        if square == 0:
            distances = np.linalg.norm(offsets, axis=1)
        else:
            fraction = np.clip(offsets @ segment / square, 0., 1.)
            distances = np.linalg.norm(offsets - fraction[:, None] * segment, axis=1)
        farthest = int(np.argmax(distances))
        if distances[farthest] > tolerance:
            middle = left + 1 + farthest
            keep.add(middle)
            pending.extend([(left, middle), (middle, right)])
    return sorted(keep)


def conditioning(sketch, calibration=None, *, simplify_pixels=0.0):
    validate(sketch)
    if calibration:
        for key in ["camera", "observation_id"]:
            if calibration.get(key) != sketch[key]:
                raise ValueError(f"Calibration {key} does not match this observation")
        if calibration.get("image_file_sha256") != sketch["image"]["file_sha256"]:
            raise ValueError("Calibration image hash does not match this scene; observation IDs repeat across episodes")
        if calibration.get("convention") != "opencv-camera-to-world":
            raise ValueError("An explicit camera convention is required")
    indices = retained_points(sketch, simplify_pixels)
    events, previous = {}, "keep"
    for index, point in enumerate(sketch["waypoints"]):
        directive = point.get("gripper", "keep")
        events[index] = directive if directive != "keep" and directive != previous else "keep"
        previous = directive
    points = []
    for number, source_index in enumerate(indices):
        point = sketch["waypoints"][source_index]
        xyz = None
        if calibration and point.get("world_height_m") is not None:
            xyz = pixel_to_plane([point["x"], point["y"]], point["world_height_m"],
                                 calibration["intrinsics"], calibration["camera_to_world"])
        points.append({"order": number, "source_order": source_index, "pixel_xy": [point["x"], point["y"]],
                       "normalized_xy": [point["x"] / sketch["image"]["width"], point["y"] / sketch["image"]["height"]],
                       "world_xyz": xyz, "world_height_m": point.get("world_height_m"),
                       "gripper_directive": point.get("gripper", "keep"), "gripper_event": events[source_index],
                       "geometry_status": "explicit_plane_projection" if xyz else "2d_only_depth_unresolved"})
    return {"schema": "rss-sketch-conditioning-v1", "observation_id": sketch["observation_id"],
            "image": sketch["image"], "camera": sketch["camera"], "arm": sketch["arm"],
            "point_semantics": sketch.get("point_semantics", "unspecified"),
            "world_xyz_semantics": "Projection of the declared sketch point; not a resolved world-flange pose or orientation",
            "waypoints": points, "use": "external conditioning for a parameter-frozen student",
            "target_representation": "ordered image waypoints and gripper intent; optional explicit world-height planes",
            "reduction": {"input_points": len(sketch["waypoints"]), "retained_points": len(points),
                          "tolerance_original_pixels": simplify_pixels,
                          "preserved": "endpoints, gripper/height directive changes, reversals, and polyline deviation above tolerance"},
            "gripper_semantics": "A directive is a requested state; gripper_event marks the start of a new non-keep directive run. Repeated points with close/open do not request repeated full action macros.",
            "model_weight_update": False, "robot_actions_generated": False,
            "execution_note": "Interpret the sketch with current public observations. Re-establish object identity, grasp and support. Pixel paths do not determine orientation, contact or depth."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sketch", type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--native-observation", type=Path)
    parser.add_argument("--frame-aligned", action="store_true")
    parser.add_argument("--simplify-pixels", type=float, default=0.)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.calibration and args.native_observation:
        parser.error("Choose --calibration or --native-observation")
    sketch = json.loads(args.sketch.read_text())
    if args.image:
        validate_source_image(sketch, args.image)
    calibration = json.loads(args.calibration.read_text()) if args.calibration else None
    if args.native_observation:
        from native_calibration import from_observation
        calibration, _ = from_observation(args.native_observation, camera=sketch["camera"],
            observation_id=sketch["observation_id"], frame_aligned=args.frame_aligned)
    args.output.write_text(json.dumps(conditioning(sketch, calibration, simplify_pixels=args.simplify_pixels), ensure_ascii=False, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()

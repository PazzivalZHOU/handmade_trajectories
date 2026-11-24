"""Prepare an offline image-bound sketch input for a future frozen DS run.

No model call, simulator, active-workspace mutation or robot action is performed.
The output is a separate reviewable bundle, not an automatic experiment launch.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from trajectory import conditioning, validate_source_image


def prepare(sketch_path, observation, *, observation_id, output, simplify_pixels=2.,
            use_native_calibration=False, frame_aligned=False):
    sketch = json.loads(sketch_path.read_text())
    if sketch.get("observation_id") != observation_id:
        raise ValueError("Sketch ID differs from the explicitly selected public observation")
    image = observation / f"current_cam_{sketch['camera']}.png"
    validate_source_image(sketch, image)
    calibration = None
    if use_native_calibration:
        from native_calibration import from_observation
        calibration, _ = from_observation(observation, camera=sketch["camera"],
            observation_id=observation_id, frame_aligned=frame_aligned)
    target = conditioning(sketch, calibration, simplify_pixels=simplify_pixels)
    # Validate the complete input before creating the separate destination.
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(sketch_path, output / "sketch.json")
    shutil.copyfile(image, output / "reference.png")
    (output / "conditioning.json").write_text(json.dumps(target, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if calibration:
        (output / "calibration.json").write_text(json.dumps(calibration, ensure_ascii=False, indent=2) + "\n")
    prompt = (
        "Read the supplied conditioning.json and reference.png alongside the native task instruction. "
        "The sketch is ordered human intent for a parameter-frozen student, not a robot action script. "
        f"Point semantics: {sketch.get('point_semantics', 'unspecified')}. An object path and a gripper TCP path require different control targets; unspecified meaning remains unresolved. "
        f"It refers to camera {sketch['camera']}, arm {sketch['arm']}, observation {observation_id}, "
        f"original image SHA256 {sketch['image']['file_sha256']}. "
        "Verify the current observation ID and image hash before treating its coordinates as current. "
        "If the scene changed, treat this as a historical reference and re-establish current target geometry from public evidence. "
        "Follow waypoint order while adapting feasible motion to the native geometry and budgets. "
        "gripper_directive describes intent; gripper_event is a new directive run. A dense close/open segment does not request a full gripper macro at each pixel. "
        "Unresolved depth remains unresolved. world_xyz exists only for an explicit height plane and matching calibration; "
        "nominal camera calibration is not an independent accuracy certificate. "
        "Use current public perception to establish identity, orientation, grasp and placement. "
        "The sketch neither supplies those facts nor overrides native success predicates, reset rules or action limits. "
        "Keep model weights frozen. Choose control code under the current run's task instruction and native feedback.\n"
    )
    (output / "student_prompt.txt").write_text(prompt)
    receipt = {"schema": "rss-sketch-handoff-v1", "source_observation_id": observation_id,
        "source_sketch_sha256": hashlib.sha256(sketch_path.read_bytes()).hexdigest(),
        "source_image_sha256": sketch["image"]["file_sha256"],
        "implementation_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                  for name in ["trajectory_handoff.py", "trajectory.py", "native_calibration.py"]},
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()},
        "reduction": target["reduction"], "model_calls": 0, "robot_actions": 0,
        "human_data_collected_by_adapter": False, "active_episode_modified": False,
        "scope": "Offline input bundle only; no claim of trajectory-following performance"}
    (output / "manifest.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sketch", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--observation-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--simplify-pixels", type=float, default=2.)
    parser.add_argument("--native-calibration", action="store_true")
    parser.add_argument("--frame-aligned", action="store_true")
    args = parser.parse_args()
    receipt = prepare(args.sketch, args.observation, observation_id=args.observation_id, output=args.output_dir,
        simplify_pixels=args.simplify_pixels, use_native_calibration=args.native_calibration, frame_aligned=args.frame_aligned)
    print(json.dumps({"output": str(args.output_dir), "reduction": receipt["reduction"], "model_calls": 0, "robot_actions": 0}), flush=True)


if __name__ == "__main__":
    main()

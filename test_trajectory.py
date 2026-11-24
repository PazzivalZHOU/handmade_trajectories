"""Trajectory regression tests extracted from the source project."""
import unittest
import json
from pathlib import Path
import tempfile
import numpy as np
from trajectory import conditioning, pixel_to_plane, validate_source_image
from trajectory_handoff import prepare as prepare_handoff

class SketchTests(unittest.TestCase):
    def sketch(self):
        return {"schema": "rss-trajectory-sketch-v1", "arm": "right", "camera": "head", "observation_id": "000000",
                "image": {"width": 640, "height": 480, "file_sha256": "a" * 64},
                "waypoints": [{"x": 320, "y": 240, "gripper": "close"}]}

    def test_2d_sketch_keeps_unresolved_depth(self):
        output = conditioning(self.sketch())
        self.assertIsNone(output["waypoints"][0]["world_xyz"])
        self.assertFalse(output["robot_actions_generated"])
        self.assertFalse(output["model_weight_update"])

    def test_point_meaning_is_preserved_without_assuming_flange_targets(self):
        sketch = self.sketch()
        self.assertEqual(conditioning(sketch)["point_semantics"], "unspecified")
        sketch["point_semantics"] = "object_reference_point"
        self.assertEqual(conditioning(sketch)["point_semantics"], "object_reference_point")
        self.assertIsNone(conditioning(sketch)["waypoints"][0]["world_xyz"])
        sketch["point_semantics"] = "world_flange_pose"
        with self.assertRaises(ValueError):
            conditioning(sketch)

    def test_dense_segment_keeps_one_new_gripper_event(self):
        sketch = self.sketch()
        sketch["waypoints"] = [{"x": x, "y": 100, "gripper": "close"} for x in range(100)]
        output = conditioning(sketch, simplify_pixels=2.)
        self.assertEqual([p["source_order"] for p in output["waypoints"]], [0, 99])
        self.assertEqual([p["gripper_event"] for p in output["waypoints"]], ["close", "keep"])
        self.assertEqual(len(sketch["waypoints"]), 100)

    def test_reduction_retains_reversals_gripper_and_height_boundaries(self):
        sketch = self.sketch()
        sketch["waypoints"] = [{"x": x, "y": 100, "gripper": "keep" if i < 4 else "close",
                               "world_height_m": None if i < 6 else .8}
                              for i, x in enumerate([0, 8, 4, 10, 11, 12, 13, 14])]
        output = conditioning(sketch, simplify_pixels=100.)
        kept = {p["source_order"] for p in output["waypoints"]}
        self.assertTrue({0, 1, 2, 3, 4, 5, 6, 7} <= kept)

    def test_repeated_click_at_turn_does_not_erase_backtracking(self):
        sketch = self.sketch()
        sketch["waypoints"] = [{"x": x, "y": 100} for x in [0, 8, 8, 4, 10]]
        output = conditioning(sketch, simplify_pixels=100.)
        self.assertEqual([p["pixel_xy"][0] for p in output["waypoints"]], [0, 8, 8, 4, 10])

    def test_source_image_dimensions_must_match_even_when_hash_matches(self):
        import hashlib
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "source.png"
            Image.new("RGB", (4, 3)).save(image)
            sketch = self.sketch()
            sketch["image"]["file_sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "dimensions"):
                validate_source_image(sketch, image)

    def test_handoff_binds_exact_image_and_rejects_wrong_observation(self):
        import hashlib
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observation = root / "observation"
            observation.mkdir()
            image = observation / "current_cam_head.png"
            Image.new("RGB", (640, 480)).save(image)
            sketch = self.sketch()
            sketch["image"]["file_sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
            path = root / "sketch.json"
            path.write_text(json.dumps(sketch))
            with self.assertRaisesRegex(ValueError, "ID"):
                prepare_handoff(path, observation, observation_id="000001", output=root / "wrong")
            self.assertFalse((root / "wrong").exists())
            receipt = prepare_handoff(path, observation, observation_id="000000", output=root / "bundle")
            self.assertEqual(receipt["source_image_sha256"], sketch["image"]["file_sha256"])
            self.assertEqual(receipt["model_calls"], 0)
            self.assertEqual(receipt["robot_actions"], 0)
            self.assertFalse(receipt["active_episode_modified"])
            self.assertEqual(set(receipt["files"]), {"reference.png", "sketch.json", "conditioning.json", "student_prompt.txt"})

    def test_explicit_plane_has_expected_metric_projection(self):
        k = [[100, 0, 50], [0, 100, 50], [0, 0, 1]]
        self.assertTrue(np.allclose(pixel_to_plane([60, 70], 2, k, np.eye(4)), [.2, .4, 2]))

    def test_calibration_from_another_observation_is_rejected(self):
        with self.assertRaises(ValueError):
            conditioning(self.sketch(), {"camera": "head", "observation_id": "000001"})

    def test_same_observation_number_from_a_different_scene_is_rejected(self):
        with self.assertRaises(ValueError):
            conditioning(self.sketch(), {"camera": "head", "observation_id": "000000",
                "convention": "opencv-camera-to-world", "image_file_sha256": "b" * 64})

    def test_display_scaled_coordinates_cannot_exceed_original_image(self):
        sketch = self.sketch()
        sketch["waypoints"][0]["x"] = 640
        with self.assertRaises(ValueError):
            conditioning(sketch)

    def test_behind_camera_plane_is_rejected(self):
        with self.assertRaises(ValueError):
            pixel_to_plane([0, 0], -1, np.eye(3), np.eye(4))

if __name__ == "__main__":
    unittest.main()

"""Headless environment checks for the MG400 MuJoCo simulation.

Run from the repository root:
    python -m unittest tests.test_simulation_environment

The MuJoCo model-load test is skipped when the optional mujoco package is not
installed. Set MG400_STRICT_DEPS=1 to make missing simulation dependencies fail.
"""

from __future__ import annotations

import importlib.util
import os
import py_compile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_XML = ROOT / "MG400_urdf.xml"
MESH_DIR = ROOT / "urdf_meshes"


class SimulationEnvironmentTest(unittest.TestCase):
    def test_required_project_files_exist(self) -> None:
        required = [
            MODEL_XML,
            ROOT / "simulate_slider.py",
            ROOT / "control_panel.py",
            ROOT / "workspace_analyzer.py",
            ROOT / "generate_workspace_stl.py",
        ]
        missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
        self.assertEqual(missing, [], f"Missing required project files: {missing}")

    def test_python_sources_compile(self) -> None:
        for source in [
            "simulate_slider.py",
            "control_panel.py",
            "workspace_analyzer.py",
            "generate_workspace_stl.py",
        ]:
            with self.subTest(source=source):
                py_compile.compile(str(ROOT / source), doraise=True)

    def test_model_xml_references_existing_mesh_assets(self) -> None:
        tree = ET.parse(MODEL_XML)
        compiler = tree.find("compiler")
        meshdir = compiler.attrib.get("meshdir", "urdf_meshes") if compiler is not None else "urdf_meshes"
        mesh_base = ROOT / meshdir

        missing = []
        for mesh in tree.findall(".//asset/mesh"):
            mesh_file = mesh.attrib.get("file")
            if mesh_file and not (mesh_base / mesh_file).exists():
                missing.append(mesh_file)

        self.assertTrue(MESH_DIR.exists(), "urdf_meshes directory is missing")
        self.assertEqual(missing, [], f"Missing mesh files referenced by MG400_urdf.xml: {missing}")

    def test_model_xml_contains_expected_robot_contract(self) -> None:
        tree = ET.parse(MODEL_XML)

        joints = {joint.attrib["name"] for joint in tree.findall(".//joint") if "name" in joint.attrib}
        for joint_name in ["J1", "J2", "J3", "J4", "J22", "J31", "J32", "J41", "J42"]:
            with self.subTest(joint=joint_name):
                self.assertIn(joint_name, joints)

        sites = {site.attrib["name"] for site in tree.findall(".//site") if "name" in site.attrib}
        self.assertIn("tcp", sites)

        equality_names = {
            eq.attrib["name"]
            for eq in tree.findall(".//equality/joint")
            if "name" in eq.attrib
        }
        self.assertEqual(
            {"eq_J22", "eq_J32", "eq_J31", "eq_J42", "eq_J41"} - equality_names,
            set(),
            "Mimic/equality constraints are incomplete",
        )

        actuated_joints = {
            actuator.attrib.get("joint")
            for actuator in tree.findall(".//actuator/jointpos")
        }
        self.assertGreaterEqual({"J1", "J2", "J3"}, actuated_joints)

        tcp_sensors = [
            sensor
            for sensor in tree.findall(".//sensor/framepos")
            if sensor.attrib.get("objtype") == "site" and sensor.attrib.get("objname") == "tcp"
        ]
        self.assertEqual(len(tcp_sensors), 1, "Expected one TCP frame position sensor")

    def test_simulation_dependencies_are_discoverable_or_explicitly_skipped(self) -> None:
        deps = ["numpy", "mujoco", "glfw"]
        missing = [dep for dep in deps if importlib.util.find_spec(dep) is None]

        if missing and os.environ.get("MG400_STRICT_DEPS") == "1":
            self.fail(f"Missing simulation dependencies: {missing}. Install with: pip install -r requirements.txt")
        if missing:
            self.skipTest(
                "Missing optional simulation dependencies "
                f"{missing}. Install with: pip install -r requirements.txt"
            )

    def test_mujoco_can_load_model_when_installed(self) -> None:
        if importlib.util.find_spec("mujoco") is None:
            self.skipTest("mujoco is not installed")

        import mujoco  # type: ignore[import-not-found]

        old_cwd = Path.cwd()
        try:
            os.chdir(ROOT)
            # Match simulate_slider.load_model(): loading from a string avoids
            # Windows path-encoding issues when the project lives under a
            # non-ASCII directory.
            model = mujoco.MjModel.from_xml_string(MODEL_XML.read_text(encoding="utf-8"))
            data = mujoco.MjData(model)
            mujoco.mj_forward(model, data)
        finally:
            os.chdir(old_cwd)

        self.assertGreaterEqual(model.njnt, 9)
        self.assertGreaterEqual(model.nu, 3)
        self.assertGreaterEqual(model.ngeom, 1)
        self.assertGreaterEqual(
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "tcp"),
            0,
            "MuJoCo model should expose a tcp site",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

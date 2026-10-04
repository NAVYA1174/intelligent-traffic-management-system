import os
import sys
import unittest
import numpy as np
import time

# Add root directory to python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.detector import VehicleDetector, TrackedVehicle
from src.rl_controller import RLTrafficSignalController, TrafficSignalState
from src.incident_manager import IncidentManager
from src.traffic_simulator import IntersectionSimulator


class TestIntelligentTrafficSystem(unittest.TestCase):
    def setUp(self):
        self.incident_mgr = IncidentManager(snapshot_dir="data/snapshots")
        self.sim = IntersectionSimulator(
            intersection_id="TEST-01",
            name="Test Junction",
            lat=12.97, lon=77.59,
            incident_mgr=self.incident_mgr
        )

    def test_detector_initialization(self):
        detector = VehicleDetector(conf_thresh=0.3)
        self.assertIsNotNone(detector)
        # Test synthetic frame detection
        dummy_frame = np.zeros((600, 800, 3), dtype=np.uint8)
        tracks, meta = detector.detect_and_track(dummy_frame)
        self.assertIn("fps", meta)
        self.assertIn("queues", meta)
        self.assertIn("active_vehicles", meta)

    def test_rl_controller_actions_and_rewards(self):
        controller = RLTrafficSignalController(intersection_id="TEST-01", min_green=5, max_green=30)
        # Simulate heavy North-South queues
        queues = {"north": 8, "south": 7, "east": 1, "west": 0}
        
        status = controller.update(queues, dt=1.0)
        self.assertIn("signal_heads", status)
        self.assertIn("metrics", status)
        self.assertIn("efficiency_gain_pct", status["metrics"])
        
        # Check that metrics improve over baseline
        for _ in range(10):
            controller.update(queues, dt=1.0)
        status = controller.get_status()
        self.assertGreaterEqual(status["metrics"]["efficiency_gain_pct"], 0.0)

    def test_incident_manager_sla(self):
        # 1. Create accident incident
        inc = self.incident_mgr.create_incident(
            incident_type="ACCIDENT",
            severity="CRITICAL",
            intersection_id="TEST-01",
            intersection_name="Test Junction",
            description="Unit test collision alert"
        )
        self.assertEqual(inc.status, "DETECTED")
        self.assertGreater(inc.sla_seconds_remaining, 25.0)
        self.assertFalse(inc.is_sla_breached)

        # 2. Dispatch police patrol unit within SLA
        dispatched = self.incident_mgr.dispatch_unit(inc.incident_id, "Police Unit Alpha-1")
        self.assertIsNotNone(dispatched)
        self.assertEqual(dispatched.status, "DISPATCHED")
        self.assertFalse(dispatched.sla_breached)

        # 3. Resolve incident
        resolved = self.incident_mgr.resolve_incident(inc.incident_id)
        self.assertEqual(resolved.status, "RESOLVED")

    def test_simulator_render_and_events(self):
        frame = self.sim.step()
        self.assertEqual(frame.shape, (600, 800, 3))
        
        # Test emergency injection
        self.sim.trigger_emergency()
        self.assertTrue(self.sim.active_emergency)
        
        # Test accident injection
        self.sim.trigger_accident()
        self.assertTrue(self.sim.has_active_accident)
        
        # Run 5 steps to verify stability
        for _ in range(5):
            f = self.sim.step()
            self.assertEqual(f.shape, (600, 800, 3))


if __name__ == "__main__":
    unittest.main()

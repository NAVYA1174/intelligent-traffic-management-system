import cv2
import numpy as np
import time
import math
import random
from typing import Dict, List, Any, Optional
from src.detector import VehicleDetector
from src.rl_controller import RLTrafficSignalController, TrafficSignalState
from src.incident_manager import IncidentManager


class SimulatedVehicle:
    def __init__(self, veh_id: int, vtype: str, direction: str, speed: float, color: tuple, is_emergency: bool = False):
        self.id = veh_id
        self.vtype = vtype  # car, bus, truck, motorcycle, ambulance
        self.direction = direction  # 'NS' (North to South), 'SN' (South to North), 'EW' (East to West), 'WE' (West to East)
        self.speed = speed
        self.base_speed = speed
        self.color = color
        self.is_emergency = is_emergency
        self.width = 38 if vtype in ["bus", "truck"] else (16 if vtype == "motorcycle" else 26)
        self.length = 75 if vtype in ["bus", "truck"] else (30 if vtype == "motorcycle" else 48)

        # Initial coordinates based on direction
        if direction == 'NS':
            self.x = 345 + random.randint(0, 1) * 35
            self.y = -self.length - random.randint(0, 80)
            self.vx = 0
            self.vy = self.speed
        elif direction == 'SN':
            self.x = 420 + random.randint(0, 1) * 35
            self.y = 600 + self.length + random.randint(0, 80)
            self.vx = 0
            self.vy = -self.speed
        elif direction == 'WE':
            self.x = -self.length - random.randint(0, 80)
            self.y = 330 + random.randint(0, 1) * 35
            self.vx = self.speed
            self.vy = 0
        else:  # EW
            self.x = 800 + self.length + random.randint(0, 80)
            self.y = 230 + random.randint(0, 1) * 35
            self.vx = -self.speed
            self.vy = 0

        self.stopped = False
        self.is_parked_illegally = False
        self.is_crashed = False
        self.parked_time = 0.0

    def update(self, signal_ns: str, signal_ew: str, vehicles_ahead: List['SimulatedVehicle'], dt: float = 0.05):
        if self.is_parked_illegally or self.is_crashed:
            self.speed = 0
            return

        # Check signal state
        must_stop_for_signal = False
        if self.direction in ['NS', 'SN']:
            if signal_ns in ['RED', 'YELLOW']:
                if self.direction == 'NS' and 190 < self.y < 235:
                    must_stop_for_signal = True
                elif self.direction == 'SN' and 365 < self.y < 410:
                    must_stop_for_signal = True
        else:
            if signal_ew in ['RED', 'YELLOW']:
                if self.direction == 'WE' and 260 < self.x < 305:
                    must_stop_for_signal = True
                elif self.direction == 'EW' and 495 < self.x < 540:
                    must_stop_for_signal = True

        # Check vehicle ahead (anti-collision headway)
        too_close = False
        for other in vehicles_ahead:
            if other.id == self.id:
                continue
            if self.direction == 'NS' and abs(self.x - other.x) < 25 and 0 < (other.y - self.y) < (self.length + 22):
                too_close = True
                break
            elif self.direction == 'SN' and abs(self.x - other.x) < 25 and 0 < (self.y - other.y) < (self.length + 22):
                too_close = True
                break
            elif self.direction == 'WE' and abs(self.y - other.y) < 25 and 0 < (other.x - self.x) < (self.length + 22):
                too_close = True
                break
            elif self.direction == 'EW' and abs(self.y - other.y) < 25 and 0 < (self.x - other.x) < (self.length + 22):
                too_close = True
                break

        if must_stop_for_signal or too_close:
            self.speed = max(0.0, self.speed - 8.0 * dt)
            self.stopped = (self.speed < 0.5)
        else:
            self.speed = min(self.base_speed, self.speed + 6.0 * dt)
            self.stopped = False

        if self.direction == 'NS':
            self.y += self.speed * dt * 30
        elif self.direction == 'SN':
            self.y -= self.speed * dt * 30
        elif self.direction == 'WE':
            self.x += self.speed * dt * 30
        elif self.direction == 'EW':
            self.x -= self.speed * dt * 30

    @property
    def bbox(self) -> List[float]:
        if self.direction in ['NS', 'SN']:
            w = self.width
            h = self.length
        else:
            w = self.length
            h = self.width
        return [self.x - w / 2, self.y - h / 2, self.x + w / 2, self.y + h / 2]


class IntersectionSimulator:
    """
    Simulates a high-traffic urban smart city intersection.
    Renders realistic CCTV camera views with road markings, moving vehicles,
    traffic lights, and incident injection.
    """
    def __init__(self, intersection_id: str, name: str, lat: float, lon: float, incident_mgr: IncidentManager):
        self.intersection_id = intersection_id
        self.name = name
        self.lat = lat
        self.lon = lon
        self.incident_mgr = incident_mgr

        self.width = 800
        self.height = 600

        # Subsystems
        self.detector = VehicleDetector(conf_thresh=0.35)
        self.controller = RLTrafficSignalController(intersection_id=intersection_id)

        # Vehicles in simulation
        self.vehicles: List[SimulatedVehicle] = []
        self.next_veh_id = 1
        self.last_spawn_time = time.time()
        self.spawn_interval = 1.2  # Every 1.2s

        # No-parking restricted zone (Curb on West approach)
        self.no_parking_zone = np.array([
            [120, 390], [280, 390], [280, 435], [120, 435]
        ], np.int32)

        # Simulation state
        self.active_emergency = False
        self.emergency_timer = 0
        self.has_active_accident = False
        self.has_illegal_parker = False

    def trigger_accident(self):
        """Simulate a collision event in the intersection box"""
        if self.has_active_accident:
            return
        self.has_active_accident = True
        
        # Spawn two colliding vehicles in the middle
        v1 = SimulatedVehicle(self.next_veh_id, "car", "WE", 12.0, (40, 50, 220))
        self.next_veh_id += 1
        v1.x = 385
        v1.y = 290
        v1.is_crashed = True
        v1.speed = 0

        v2 = SimulatedVehicle(self.next_veh_id, "truck", "NS", 10.0, (200, 120, 40))
        self.next_veh_id += 1
        v2.x = 405
        v2.y = 305
        v2.is_crashed = True
        v2.speed = 0

        self.vehicles.extend([v1, v2])

        # Register incident immediately in manager with 30s SLA
        self.incident_mgr.create_incident(
            incident_type="ACCIDENT",
            severity="CRITICAL",
            intersection_id=self.intersection_id,
            intersection_name=self.name,
            description=f"CRITICAL: Multi-vehicle collision at {self.name} central junction blocking 2 lanes!",
            bbox=[v1.x - 30, v1.y - 30, v2.x + 30, v2.y + 30],
            vehicle_info={"types": ["car", "truck"]}
        )

    def trigger_illegal_parking(self):
        """Simulate a vehicle parking in the red/yellow curb tow-away zone"""
        if self.has_illegal_parker:
            return
        self.has_illegal_parker = True

        v = SimulatedVehicle(self.next_veh_id, "car", "WE", 0.0, (20, 220, 240))
        self.next_veh_id += 1
        v.x = 200
        v.y = 412
        v.is_parked_illegally = True
        v.speed = 0
        self.vehicles.append(v)

        self.incident_mgr.create_incident(
            incident_type="ILLEGAL_PARKING",
            severity="MEDIUM",
            intersection_id=self.intersection_id,
            intersection_name=self.name,
            description=f"Tow-Away Zone Violation: Vehicle #{v.id} parked illegally in restricted curb zone.",
            bbox=v.bbox,
            vehicle_info={"type": "car"}
        )

    def trigger_emergency(self):
        """Simulate an approaching ambulance needing dynamic RL preemption"""
        self.active_emergency = True
        self.emergency_timer = time.time()
        
        amb = SimulatedVehicle(self.next_veh_id, "ambulance", "NS", 20.0, (255, 255, 255), is_emergency=True)
        self.next_veh_id += 1
        amb.x = 345
        amb.y = -60
        self.vehicles.insert(0, amb)

        self.incident_mgr.create_incident(
            incident_type="EMERGENCY_PREEMPTION",
            severity="HIGH",
            intersection_id=self.intersection_id,
            intersection_name=self.name,
            description=f"Ambulance Preemption: Rapid green-wave signal corridor cleared for emergency vehicle!",
            bbox=amb.bbox,
            vehicle_info={"type": "ambulance"},
            auto_dispatch=True
        )

    def step(self) -> np.ndarray:
        now = time.time()

        # Check emergency timeout (12s corridor pass)
        if self.active_emergency and (now - self.emergency_timer) > 12.0:
            self.active_emergency = False

        # Spawn normal traffic
        if now - self.last_spawn_time > self.spawn_interval and len(self.vehicles) < 22:
            self.last_spawn_time = now
            dirs = ['NS', 'SN', 'WE', 'EW']
            types = ['car', 'car', 'car', 'bus', 'truck', 'motorcycle']
            colors = [
                (220, 50, 50), (45, 180, 50), (30, 120, 230),
                (200, 200, 20), (180, 50, 190), (220, 220, 220), (60, 60, 60)
            ]
            d = random.choice(dirs)
            t = random.choice(types)
            spd = random.uniform(8.0, 14.0)
            c = random.choice(colors)
            veh = SimulatedVehicle(self.next_veh_id, t, d, spd, c)
            self.next_veh_id += 1
            self.vehicles.append(veh)

        # Get signals
        status = self.controller.get_status()
        sig_ns = status["signal_heads"]["north_south"]
        sig_ew = status["signal_heads"]["east_west"]

        # Update vehicle positions
        surviving = []
        for veh in self.vehicles:
            veh.update(sig_ns, sig_ew, self.vehicles, dt=0.04)
            # Filter vehicles that drove off screen
            if -120 <= veh.x <= 920 and -120 <= veh.y <= 720:
                surviving.append(veh)
            else:
                if veh.is_crashed:
                    self.has_active_accident = False
                if veh.is_parked_illegally:
                    self.has_illegal_parker = False
        self.vehicles = surviving

        # Render CCTV frame
        frame = self._render_scene(sig_ns, sig_ew, status["countdown_seconds"])

        # Run CV & YOLO Detector
        detected_vehs, cv_meta = self.detector.detect_and_track(frame, [self.no_parking_zone])

        # If CV detected an incident, register it
        for inc in cv_meta.get("incidents", []):
            self.incident_mgr.create_incident(
                incident_type=inc["type"],
                severity=inc["severity"],
                intersection_id=self.intersection_id,
                intersection_name=self.name,
                description=inc["description"],
                frame=frame,
                bbox=inc.get("bbox")
            )

        # Update RL controller with real-time queue states
        self.controller.update(cv_meta["queues"], emergency_vehicle=self.active_emergency, dt=0.04)

        # Annotate Frame with high-tech CCTV Operations Center Overlay
        annotated_frame = self._draw_cctv_overlay(frame, status, cv_meta)

        return annotated_frame

    def _render_scene(self, sig_ns: str, sig_ew: str, countdown: int) -> np.ndarray:
        # Base background: Asphalt road with surrounding sidewalks
        frame = np.full((self.height, self.width, 3), (45, 65, 50), dtype=np.uint8)  # Grass surroundings

        # Sidewalk concrete
        cv2.rectangle(frame, (280, 0), (520, self.height), (120, 120, 120), -1)
        cv2.rectangle(frame, (0, 180), (self.width, 420), (120, 120, 120), -1)

        # Road asphalt
        cv2.rectangle(frame, (300, 0), (500, self.height), (40, 42, 45), -1)
        cv2.rectangle(frame, (0, 200), (self.width, 400), (40, 42, 45), -1)

        # Yellow No-Parking Zone (Diagonal hazard stripes)
        cv2.fillPoly(frame, [self.no_parking_zone], (30, 35, 45))
        for step in range(self.no_parking_zone[0][0], self.no_parking_zone[1][0], 18):
            cv2.line(frame, (step, self.no_parking_zone[0][1]), (step + 14, self.no_parking_zone[2][1]), (0, 215, 255), 2)
        cv2.putText(frame, "NO PARKING / TOW ZONE", (self.no_parking_zone[0][0] + 5, self.no_parking_zone[0][1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 215, 255), 1)

        # Lane divider markings (dashed white)
        # N-S lanes
        for y in range(0, 180, 25):
            cv2.line(frame, (400, y), (400, y + 15), (240, 240, 240), 2)
        for y in range(420, self.height, 25):
            cv2.line(frame, (400, y), (400, y + 15), (240, 240, 240), 2)
        # E-W lanes
        for x in range(0, 280, 25):
            cv2.line(frame, (x, 300), (x + 15, 300), (240, 240, 240), 2)
        for x in range(520, self.width, 25):
            cv2.line(frame, (x, 300), (x + 15, 300), (240, 240, 240), 2)

        # Stop lines (solid thick white lines)
        cv2.line(frame, (300, 195), (500, 195), (255, 255, 255), 4)  # North approach
        cv2.line(frame, (300, 405), (500, 405), (255, 255, 255), 4)  # South approach
        cv2.line(frame, (295, 200), (295, 400), (255, 255, 255), 4)  # West approach
        cv2.line(frame, (505, 200), (505, 400), (255, 255, 255), 4)  # East approach

        # Zebra pedestrian crosswalks
        for x in range(305, 495, 20):
            cv2.rectangle(frame, (x, 175), (x + 12, 190), (250, 250, 250), -1)
            cv2.rectangle(frame, (x, 410), (x + 12, 425), (250, 250, 250), -1)
        for y in range(205, 395, 20):
            cv2.rectangle(frame, (275, y), (290, y + 12), (250, 250, 250), -1)
            cv2.rectangle(frame, (510, y), (525, y + 12), (250, 250, 250), -1)

        # Draw vehicles
        for v in self.vehicles:
            x, y = int(v.x), int(v.y)
            w, h = int(v.width), int(v.length)
            
            # Shadow
            cv2.rectangle(frame, (int(v.x - w/2 + 4), int(v.y - h/2 + 4)),
                          (int(v.x + w/2 + 4), int(v.y + h/2 + 4)), (20, 20, 20), -1)

            # Body
            color = (0, 0, 255) if v.is_crashed else v.color
            if v.direction in ['NS', 'SN']:
                cv2.rectangle(frame, (x - w//2, y - h//2), (x + w//2, y + h//2), color, -1)
                # Windshield & roof
                cv2.rectangle(frame, (x - w//2 + 3, y - h//4), (x + w//2 - 3, y + h//4), (25, 25, 30), -1)
                # Headlights
                headlight_y = y + h//2 if v.direction == 'NS' else y - h//2
                cv2.circle(frame, (x - w//3, headlight_y), 3, (200, 255, 255), -1)
                cv2.circle(frame, (x + w//3, headlight_y), 3, (200, 255, 255), -1)
            else:
                cv2.rectangle(frame, (x - h//2, y - w//2), (x + h//2, y + w//2), color, -1)
                # Windshield & roof
                cv2.rectangle(frame, (x - h//4, y - w//2 + 3), (x + h//4, y + w//2 - 3), (25, 25, 30), -1)
                # Headlights
                headlight_x = x + h//2 if v.direction == 'WE' else x - h//2
                cv2.circle(frame, (headlight_x, y - w//3), 3, (200, 255, 255), -1)
                cv2.circle(frame, (headlight_x, y + w//3), 3, (200, 255, 255), -1)

            # Emergency beacon flasher
            if v.is_emergency:
                flash_col = (0, 0, 255) if int(time.time() * 8) % 2 == 0 else (255, 0, 0)
                cv2.circle(frame, (x, y), 8, flash_col, -1)
                cv2.putText(frame, "AMBULANCE", (x - 35, y - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 2)

            # Collision smoke / debris if crashed
            if v.is_crashed:
                for k in range(5):
                    ox = random.randint(-15, 15)
                    oy = random.randint(-15, 15)
                    cv2.circle(frame, (x + ox, y + oy), random.randint(4, 10), (140, 140, 140), -1)

        return frame

    def _draw_cctv_overlay(self, frame: np.ndarray, status: Dict[str, Any], cv_meta: Dict[str, Any]) -> np.ndarray:
        h, w = frame.shape[:2]

        # Top Dark HUD Bar
        cv2.rectangle(frame, (0, 0), (w, 55), (15, 18, 22), -1)
        cv2.line(frame, (0, 55), (w, 55), (0, 200, 255), 2)

        # Camera Header
        cam_text = f"CCTV CAM #{self.intersection_id} | {self.name.upper()} | LIVE 1080p"
        cv2.putText(frame, cam_text, (15, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        # Live timestamp & FPS
        fps_text = f"YOLO FPS: {cv_meta['fps']} | Latency: 14ms | {time.strftime('%Y-%m-%d %H:%M:%S')}"
        cv2.putText(frame, fps_text, (15, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 180, 180), 1)

        # Traffic Signal HUD in Top Right
        sig_ns = status["signal_heads"]["north_south"]
        sig_ew = status["signal_heads"]["east_west"]
        countdown = status["countdown_seconds"]
        mode = status["mode"]

        col_ns = (0, 255, 0) if sig_ns == "GREEN" else ((0, 255, 255) if sig_ns == "YELLOW" else (0, 0, 255))
        col_ew = (0, 255, 0) if sig_ew == "GREEN" else ((0, 255, 255) if sig_ew == "YELLOW" else (0, 0, 255))

        # NS Indicator
        cv2.circle(frame, (w - 180, 25), 8, col_ns, -1)
        cv2.putText(frame, f"N-S: {sig_ns}", (w - 165, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1)

        # EW Indicator
        cv2.circle(frame, (w - 85, 25), 8, col_ew, -1)
        cv2.putText(frame, f"E-W: {sig_ew}", (w - 70, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1)

        # Countdown & Controller Mode
        cv2.putText(frame, f"RL CYCLE: {countdown}s | MODE: {mode}", (w - 200, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1)

        # Bottom HUD Bar: Approach Queues & Live Metrics
        cv2.rectangle(frame, (0, h - 35), (w, h), (15, 18, 22), -1)
        queues = cv_meta.get("queues", {})
        q_text = f"QUEUES -> North: {queues.get('north',0)} | South: {queues.get('south',0)} | East: {queues.get('east',0)} | West: {queues.get('west',0)} | Active: {cv_meta.get('active_vehicles',0)}"
        cv2.putText(frame, q_text, (15, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 180), 1)

        eff = status["metrics"]["efficiency_gain_pct"]
        eff_text = f"RL Delay Reduction: +{eff}%"
        cv2.putText(frame, eff_text, (w - 220, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

        # Draw vehicle tracking bounding boxes
        for tid, veh in self.detector.tracked_vehicles.items():
            x1, y1, x2, y2 = [int(p) for p in veh.bbox]
            
            box_col = (0, 255, 0)
            status_tag = ""
            if veh.status == "ACCIDENT":
                box_col = (0, 0, 255)
                status_tag = "! ACCIDENT !"
            elif veh.status == "ILLEGAL_PARKING":
                box_col = (0, 165, 255)
                status_tag = "NO-PARKING VIOLATION"
            elif veh.status == "BREAKDOWN":
                box_col = (0, 100, 255)
                status_tag = "STALLED"

            cv2.rectangle(frame, (x1, y1), (x2, y2), box_col, 2)
            label = f"#{tid} {veh.class_name.upper()} {int(veh.speed_kmh)} km/h {status_tag}".strip()
            
            # Label background
            (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
            cv2.rectangle(frame, (x1, max(0, y1 - lh - 6)), (x1 + lw + 6, y1), box_col, -1)
            cv2.putText(frame, label, (x1 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1)

        return frame

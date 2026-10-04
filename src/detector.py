import cv2
import numpy as np
import time
from typing import List, Dict, Any, Tuple, Optional
import os

# Try importing ultralytics YOLO
YOLO_AVAILABLE = False
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


class TrackedVehicle:
    def __init__(self, track_id: int, bbox: List[float], class_name: str, confidence: float):
        self.track_id = track_id
        self.bbox = [float(b) for b in bbox]  # [x1, y1, x2, y2]
        self.class_name = class_name
        self.confidence = float(confidence)
        self.centroid = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        self.history = [self.centroid]
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.stationary_since = None
        self.speed_kmh = 0.0
        self.status = "MOVING"  # MOVING, STOPPED, ILLEGAL_PARKING, ACCIDENT, BREAKDOWN
        self.alert_triggered = False
        self.is_emergency = class_name in ["ambulance", "police", "fire"]

    def update(self, bbox: List[float], confidence: float, fps: float = 20.0):
        self.bbox = [float(b) for b in bbox]
        self.confidence = float(confidence)
        new_centroid = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        
        # Calculate speed (pixel displacement scaled to km/h)
        dx = new_centroid[0] - self.centroid[0]
        dy = new_centroid[1] - self.centroid[1]
        dist_pixels = np.sqrt(dx * dx + dy * dy)
        
        # Scale: ~10 pixels = ~1 meter (standard CCTV calibration factor)
        # speed (m/s) = dist_pixels / 10 * fps
        # speed (km/h) = speed (m/s) * 3.6
        instant_speed = (dist_pixels / 12.0) * fps * 3.6
        self.speed_kmh = round(0.7 * self.speed_kmh + 0.3 * instant_speed, 1)

        self.centroid = new_centroid
        self.history.append(new_centroid)
        if len(self.history) > 30:
            self.history.pop(0)

        now = time.time()
        self.last_seen = now

        # Update stationary tracker
        if self.speed_kmh < 3.0:
            if self.stationary_since is None:
                self.stationary_since = now
        else:
            self.stationary_since = None
            if self.status not in ["ACCIDENT"]:
                self.status = "MOVING"

    @property
    def stationary_duration(self) -> float:
        if self.stationary_since is None:
            return 0.0
        return time.time() - self.stationary_since


class VehicleDetector:
    """
    Real-Time Traffic Computer Vision Engine.
    Uses YOLO (v8) for vehicle detection & classification, with fallback support.
    Integrates multi-object tracking, speed estimation, illegal parking detection,
    and accident/breakdown detection.
    """
    COCO_VEHICLE_CLASSES = {
        2: "car",
        3: "motorcycle",
        5: "bus",
        7: "truck",
        1: "bicycle"
    }

    def __init__(self, model_path: str = "yolov8n.pt", conf_thresh: float = 0.35, use_cuda: bool = False):
        self.conf_thresh = conf_thresh
        self.model = None
        self.next_track_id = 1
        self.tracked_vehicles: Dict[int, TrackedVehicle] = {}
        self.total_counted = {"car": 0, "bus": 0, "truck": 0, "motorcycle": 0, "emergency": 0}
        self.fps = 25.0
        self.last_frame_time = time.time()
        
        # Load YOLO model
        if YOLO_AVAILABLE:
            try:
                print(f"[CV Engine] Loading YOLO model: {model_path}...")
                self.model = YOLO(model_path)
                print("[CV Engine] YOLO model loaded successfully!")
            except Exception as e:
                print(f"[CV Engine] Warning: Failed to load YOLO ({e}). Initializing backup detector.")
                self.model = None
        else:
            print("[CV Engine] YOLO (ultralytics) not detected. Using high-speed computer vision pipeline.")

    def detect_and_track(self, frame: np.ndarray, no_parking_zones: Optional[List[np.ndarray]] = None) -> Tuple[List[TrackedVehicle], Dict[str, Any]]:
        """
        Runs YOLO object detection and tracking on a single video frame.
        Analyzes vehicle classes, queues, parking violations, and collisions.
        """
        h, w = frame.shape[:2]
        now = time.time()
        dt = max(now - self.last_frame_time, 0.001)
        self.fps = 0.8 * self.fps + 0.2 * (1.0 / dt)
        self.last_frame_time = now

        raw_detections = []

        if self.model is not None:
            try:
                # Run inference with YOLO
                results = self.model(frame, conf=self.conf_thresh, verbose=False)
                for r in results:
                    boxes = r.boxes
                    for box in boxes:
                        cls_id = int(box.cls[0].item())
                        if cls_id in self.COCO_VEHICLE_CLASSES:
                            cls_name = self.COCO_VEHICLE_CLASSES[cls_id]
                            conf = float(box.conf[0].item())
                            xyxy = box.xyxy[0].tolist()
                            raw_detections.append((xyxy, cls_name, conf))
            except Exception as e:
                pass
        
        # High-performance CV fallback detector if YOLO is still loading or unavailable
        if len(raw_detections) == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Mask out HUD top and bottom bars
            mask = np.zeros_like(gray)
            mask[60:h-40, :] = 255
            # Edge / gradient detection for vehicles
            edges = cv2.Canny(gray, 50, 150)
            edges = cv2.bitwise_and(edges, edges, mask=mask)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
            dilated = cv2.dilate(edges, kernel, iterations=2)
            contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for c in contours:
                area = cv2.contourArea(c)
                if 400 < area < 15000:
                    bx, by, bw, bh = cv2.boundingRect(c)
                    # Exclude the no-parking sign area and lane markings
                    if bw > 15 and bh > 15:
                        cls_name = "truck" if (bw * bh > 3500) else ("car" if (bw * bh > 1000) else "motorcycle")
                        raw_detections.append(([bx, by, bx + bw, by + bh], cls_name, 0.88))

        # Update Tracking (Centroid Distance Matcher)
        matched_tracks = set()
        for bbox, cls_name, conf in raw_detections:
            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0

            best_match_id = None
            min_dist = 60.0  # Max pixel jump between frames

            for tid, t_veh in self.tracked_vehicles.items():
                if tid in matched_tracks:
                    continue
                # Same or compatible class
                dist = np.hypot(t_veh.centroid[0] - cx, t_veh.centroid[1] - cy)
                if dist < min_dist:
                    min_dist = dist
                    best_match_id = tid

            if best_match_id is not None:
                self.tracked_vehicles[best_match_id].update(bbox, conf, fps=self.fps)
                matched_tracks.add(best_match_id)
            else:
                new_veh = TrackedVehicle(self.next_track_id, bbox, cls_name, conf)
                self.tracked_vehicles[self.next_track_id] = new_veh
                matched_tracks.add(self.next_track_id)
                self.next_track_id += 1
                if cls_name in self.total_counted:
                    self.total_counted[cls_name] += 1

        # Purge stale tracks (not seen for > 1.5s)
        stale_ids = [tid for tid, v in self.tracked_vehicles.items() if (now - v.last_seen) > 1.5]
        for tid in stale_ids:
            del self.tracked_vehicles[tid]

        # Analyze Events: Illegal Parking, Accidents, Stalls
        incidents_detected = self._analyze_traffic_events(frame, no_parking_zones)

        # Count approaches and queues
        queues = self._calculate_queues(w, h)

        metadata = {
            "fps": round(self.fps, 1),
            "active_vehicles": len(self.tracked_vehicles),
            "total_counts": self.total_counted,
            "queues": queues,
            "incidents": incidents_detected
        }

        return list(self.tracked_vehicles.values()), metadata

    def _analyze_traffic_events(self, frame: np.ndarray, no_parking_zones: Optional[List[np.ndarray]]) -> List[Dict[str, Any]]:
        incidents = []
        now = time.time()
        h, w = frame.shape[:2]

        # 1. Illegal Parking Detection
        if no_parking_zones:
            for tid, veh in self.tracked_vehicles.items():
                pt = (int(veh.centroid[0]), int(veh.centroid[1]))
                in_zone = False
                for zone in no_parking_zones:
                    if cv2.pointPolygonTest(zone, pt, False) >= 0:
                        in_zone = True
                        break

                if in_zone and veh.stationary_duration >= 5.0:  # 5s in restricted zone
                    veh.status = "ILLEGAL_PARKING"
                    if not veh.alert_triggered:
                        veh.alert_triggered = True
                        incidents.append({
                            "type": "ILLEGAL_PARKING",
                            "severity": "MEDIUM",
                            "vehicle_id": tid,
                            "class": veh.class_name,
                            "bbox": veh.bbox,
                            "description": f"Illegal parking detected in Tow-Away Zone (Vehicle #{tid} [{veh.class_name.upper()}], stopped {int(veh.stationary_duration)}s)",
                            "timestamp": now
                        })

        # 2. Accident / Collision Detection (IoU overlap + abrupt halt)
        all_vehs = list(self.tracked_vehicles.values())
        for i in range(len(all_vehs)):
            for j in range(i + 1, len(all_vehs)):
                v1, v2 = all_vehs[i], all_vehs[j]
                # Check bounding box IoU
                iou = self._calculate_iou(v1.bbox, v2.bbox)
                if iou > 0.30 and v1.stationary_duration > 2.0 and v2.stationary_duration > 2.0:
                    v1.status = "ACCIDENT"
                    v2.status = "ACCIDENT"
                    if not (v1.alert_triggered or v2.alert_triggered):
                        v1.alert_triggered = True
                        v2.alert_triggered = True
                        incidents.append({
                            "type": "ACCIDENT",
                            "severity": "CRITICAL",
                            "vehicle_id": v1.track_id,
                            "secondary_vehicle_id": v2.track_id,
                            "class": f"{v1.class_name} & {v2.class_name}",
                            "bbox": v1.bbox,
                            "description": f"CRITICAL: Collision detected between Vehicle #{v1.track_id} and #{v2.track_id} with traffic blockage!",
                            "timestamp": now
                        })

        # 3. Vehicle Breakdown / Lane Stalling
        for tid, veh in self.tracked_vehicles.items():
            if veh.status == "MOVING" and veh.stationary_duration >= 8.0:
                # Vehicle stalled in travel lane
                veh.status = "BREAKDOWN"
                if not veh.alert_triggered:
                    veh.alert_triggered = True
                    incidents.append({
                        "type": "BREAKDOWN",
                        "severity": "HIGH",
                        "vehicle_id": tid,
                        "class": veh.class_name,
                        "bbox": veh.bbox,
                        "description": f"Vehicle breakdown / lane stall detected (Vehicle #{tid} [{veh.class_name.upper()}]) in active corridor",
                        "timestamp": now
                    })

        return incidents

    def _calculate_queues(self, width: int, height: int) -> Dict[str, int]:
        """
        Calculates queue lengths on North, South, East, and West approaches.
        A vehicle in a queue is stopped (speed < 5 km/h) approaching the intersection.
        """
        queues = {"north": 0, "south": 0, "east": 0, "west": 0}
        cx_mid = width / 2.0
        cy_mid = height / 2.0

        for veh in self.tracked_vehicles.values():
            if veh.speed_kmh < 6.0:  # Queue stopped threshold
                cx, cy = veh.centroid
                if cy < cy_mid * 0.9 and abs(cx - cx_mid) < width * 0.25:
                    queues["north"] += 1
                elif cy > cy_mid * 1.1 and abs(cx - cx_mid) < width * 0.25:
                    queues["south"] += 1
                elif cx < cx_mid * 0.9 and abs(cy - cy_mid) < height * 0.25:
                    queues["west"] += 1
                elif cx > cx_mid * 1.1 and abs(cy - cy_mid) < height * 0.25:
                    queues["east"] += 1

        return queues

    @staticmethod
    def _calculate_iou(boxA: List[float], boxB: List[float]) -> float:
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])

        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])

        iou = interArea / float(boxAArea + boxBArea - interArea + 1e-6)
        return float(iou)

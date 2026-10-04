import time
import os
import cv2
import numpy as np
from typing import List, Dict, Any, Optional
import json


class Incident:
    def __init__(self, incident_id: str, incident_type: str, severity: str,
                 intersection_id: str, intersection_name: str,
                 description: str, bbox: Optional[List[float]] = None,
                 vehicle_info: Optional[Dict[str, Any]] = None,
                 snapshot_path: Optional[str] = None):
        self.incident_id = incident_id
        self.incident_type = incident_type  # ACCIDENT, BREAKDOWN, ILLEGAL_PARKING, EMERGENCY
        self.severity = severity            # CRITICAL, HIGH, MEDIUM, LOW
        self.intersection_id = intersection_id
        self.intersection_name = intersection_name
        self.description = description
        self.bbox = bbox
        self.vehicle_info = vehicle_info or {}
        self.snapshot_path = snapshot_path
        
        self.created_at = time.time()
        self.sla_target_seconds = 30.0  # 30-second Police Dispatch SLA
        self.status = "DETECTED"         # DETECTED, DISPATCHED, ON_SCENE, RESOLVED
        self.dispatched_at: Optional[float] = None
        self.dispatched_unit: Optional[str] = None
        self.resolved_at: Optional[float] = None
        self.sla_breached = False

    @property
    def sla_seconds_remaining(self) -> float:
        if self.status != "DETECTED":
            return 0.0
        elapsed = time.time() - self.created_at
        return max(0.0, round(self.sla_target_seconds - elapsed, 1))

    @property
    def is_sla_breached(self) -> bool:
        if self.status == "DETECTED":
            return (time.time() - self.created_at) > self.sla_target_seconds
        elif self.dispatched_at:
            return (self.dispatched_at - self.created_at) > self.sla_target_seconds
        return False

    def dispatch(self, unit_name: str = "Traffic Patrol Unit #Alpha-7"):
        self.status = "DISPATCHED"
        self.dispatched_at = time.time()
        self.dispatched_unit = unit_name
        self.sla_breached = (self.dispatched_at - self.created_at) > self.sla_target_seconds

    def resolve(self):
        self.status = "RESOLVED"
        self.resolved_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "incident_type": self.incident_type,
            "severity": self.severity,
            "intersection_id": self.intersection_id,
            "intersection_name": self.intersection_name,
            "description": self.description,
            "created_at": time.strftime("%H:%M:%S", time.localtime(self.created_at)),
            "created_timestamp": self.created_at,
            "status": self.status,
            "sla_seconds_remaining": self.sla_seconds_remaining,
            "sla_breached": self.is_sla_breached,
            "dispatched_unit": self.dispatched_unit,
            "dispatched_at": time.strftime("%H:%M:%S", time.localtime(self.dispatched_at)) if self.dispatched_at else None,
            "snapshot_url": f"/snapshots/{os.path.basename(self.snapshot_path)}" if self.snapshot_path else None
        }


class IncidentManager:
    """
    Centralized Smart City Incident Detection & Emergency Dispatch Center.
    Enforces a strict 30-second notification SLA for police and emergency teams.
    """
    def __init__(self, snapshot_dir: str = "data/snapshots"):
        self.snapshot_dir = snapshot_dir
        os.makedirs(self.snapshot_dir, exist_ok=True)
        self.incidents: Dict[str, Incident] = {}
        self.next_incident_num = 101

    def create_incident(self, incident_type: str, severity: str,
                        intersection_id: str, intersection_name: str,
                        description: str, frame: Optional[np.ndarray] = None,
                        bbox: Optional[List[float]] = None,
                        vehicle_info: Optional[Dict[str, Any]] = None,
                        auto_dispatch: bool = False) -> Incident:
        """
        Registers a new incident detected by Computer Vision or Operator.
        Captures evidence snapshot with timestamp and highlighted bounding box.
        """
        incident_id = f"INC-{self.next_incident_num}"
        self.next_incident_num += 1

        snapshot_path = None
        if frame is not None:
            snapshot_path = self._save_evidence_snapshot(frame, bbox, incident_id, description)

        incident = Incident(
            incident_id=incident_id,
            incident_type=incident_type,
            severity=severity,
            intersection_id=intersection_id,
            intersection_name=intersection_name,
            description=description,
            bbox=bbox,
            vehicle_info=vehicle_info,
            snapshot_path=snapshot_path
        )

        if auto_dispatch:
            incident.dispatch(unit_name="Automated Rapid Response Team")

        self.incidents[incident_id] = incident
        print(f"[Incident Alert] {severity} - {incident_type} at {intersection_name}: {description}")
        return incident

    def dispatch_unit(self, incident_id: str, unit_name: str = "Traffic Patrol Unit #04") -> Optional[Incident]:
        incident = self.incidents.get(incident_id)
        if incident:
            incident.dispatch(unit_name)
            return incident
        return None

    def resolve_incident(self, incident_id: str) -> Optional[Incident]:
        incident = self.incidents.get(incident_id)
        if incident:
            incident.resolve()
            return incident
        return None

    def get_active(self) -> List[Dict[str, Any]]:
        """Returns all unresolved incidents sorted by severity & timestamp"""
        active = [inc for inc in self.incidents.values() if inc.status in ["DETECTED", "DISPATCHED"]]
        active.sort(key=lambda x: (0 if x.severity == "CRITICAL" else 1, -x.created_at))
        return [inc.to_dict() for inc in active]

    def get_all(self, limit: int = 50) -> List[Dict[str, Any]]:
        sorted_all = sorted(self.incidents.values(), key=lambda x: -x.created_at)
        return [inc.to_dict() for inc in sorted_all[:limit]]

    def _save_evidence_snapshot(self, frame: np.ndarray, bbox: Optional[List[float]],
                                incident_id: str, description: str) -> str:
        """Draws evidence overlay, timestamp, and saves snapshot for police record"""
        annotated = frame.copy()
        h, w = annotated.shape[:2]

        if bbox is not None and len(bbox) == 4:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            # Draw glowing red alert bounding box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 3)
            cv2.rectangle(annotated, (x1, y1 - 25), (x1 + 160, y1), (0, 0, 255), -1)
            cv2.putText(annotated, f"INCIDENT AREA", (x1 + 5, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

        # Header overlay
        cv2.rectangle(annotated, (0, 0), (w, 40), (20, 20, 20), -1)
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        header_text = f"EVIDENCE LOG | ID: {incident_id} | {timestamp_str} | ITMS TOC"
        cv2.putText(annotated, header_text, (15, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)

        # Bottom banner with description
        cv2.rectangle(annotated, (0, h - 35), (w, h), (10, 10, 10), -1)
        cv2.putText(annotated, description[:90], (15, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        filename = f"{incident_id}_{int(time.time())}.jpg"
        filepath = os.path.join(self.snapshot_dir, filename)
        cv2.imwrite(filepath, annotated)
        return filepath

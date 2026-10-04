import os
import cv2
import time
import json
import asyncio
from typing import Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.incident_manager import IncidentManager
from src.traffic_simulator import IntersectionSimulator
from src.rl_controller import TrafficSignalState

app = FastAPI(title="Intelligent Traffic Management System (ITMS) - Smart City TOC")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "app", "templates")
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "data", "snapshots")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/snapshots", StaticFiles(directory=SNAPSHOTS_DIR), name="snapshots")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Initialize Incident Manager
incident_manager = IncidentManager(snapshot_dir=SNAPSHOTS_DIR)

# Initialize 4 City-Wide Intersections
intersections: Dict[str, IntersectionSimulator] = {
    "INT-101": IntersectionSimulator(
        intersection_id="INT-101",
        name="Downtown Central Square",
        lat=12.9716, lon=77.5946,
        incident_mgr=incident_manager
    ),
    "INT-102": IntersectionSimulator(
        intersection_id="INT-102",
        name="Tech Corridor Expressway",
        lat=12.9352, lon=77.6946,
        incident_mgr=incident_manager
    ),
    "INT-103": IntersectionSimulator(
        intersection_id="INT-103",
        name="Metro Ring Road Cross",
        lat=12.9279, lon=77.6271,
        incident_mgr=incident_manager
    ),
    "INT-104": IntersectionSimulator(
        intersection_id="INT-104",
        name="Harbor Freight Boulevard",
        lat=13.0112, lon=77.5551,
        incident_mgr=incident_manager
    ),
}

# Pre-populate some historical resolved incidents for realistic operations center data
def seed_initial_incidents():
    now = time.time()
    inc1 = incident_manager.create_incident(
        incident_type="ILLEGAL_PARKING",
        severity="MEDIUM",
        intersection_id="INT-102",
        intersection_name="Tech Corridor Expressway",
        description="Delivery van stopped in curbside bus lane for > 45s",
        bbox=[160, 395, 230, 430]
    )
    inc1.created_at = now - 180
    inc1.dispatch("Tow Patrol Unit #3")
    inc1.resolve()

seed_initial_incidents()


@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "intersections": [
            {
                "id": sim.intersection_id,
                "name": sim.name,
                "lat": sim.lat,
                "lon": sim.lon
            }
            for sim in intersections.values()
        ]
    })


def generate_mjpeg_stream(intersection_id: str):
    """Generates continuous MJPEG multipart stream from intersection CCTV simulator"""
    sim = intersections.get(intersection_id)
    if not sim:
        sim = list(intersections.values())[0]

    while True:
        frame = sim.step()
        # Compress to JPEG
        ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if not ret:
            continue
        frame_bytes = jpeg.tobytes()
        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
        time.sleep(0.04)  # ~25 FPS


@app.get("/api/stream/{intersection_id}")
async def video_stream(intersection_id: str):
    return StreamingResponse(
        generate_mjpeg_stream(intersection_id),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/intersections")
async def get_intersections():
    results = []
    for int_id, sim in intersections.items():
        status = sim.controller.get_status()
        active_cnt = len(sim.vehicles)
        congestion_pct = min(100, int((active_cnt / 20.0) * 100))
        results.append({
            "id": int_id,
            "name": sim.name,
            "lat": sim.lat,
            "lon": sim.lon,
            "signal": status["signal_heads"],
            "current_phase": status["phase_name"],
            "countdown": status["countdown_seconds"],
            "mode": status["mode"],
            "active_vehicles": active_cnt,
            "congestion_pct": congestion_pct,
            "has_accident": sim.has_active_accident,
            "has_illegal_parking": sim.has_illegal_parker,
            "has_emergency": sim.active_emergency,
            "metrics": status["metrics"]
        })
    return JSONResponse(results)


@app.get("/api/incidents")
async def get_incidents():
    return JSONResponse({
        "active": incident_manager.get_active(),
        "recent": incident_manager.get_all(30)
    })


@app.post("/api/incidents/{incident_id}/dispatch")
async def dispatch_incident(incident_id: str, patrol_unit: str = Form("Patrol Rapid Unit #Alpha-1")):
    incident = incident_manager.dispatch_unit(incident_id, patrol_unit)
    if not incident:
        return JSONResponse({"status": "error", "message": "Incident not found"}, status_code=404)
    return JSONResponse({
        "status": "success",
        "message": f"Dispatched {patrol_unit} to {incident.intersection_name}",
        "incident": incident.to_dict()
    })


@app.post("/api/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str):
    incident = incident_manager.resolve_incident(incident_id)
    if not incident:
        return JSONResponse({"status": "error", "message": "Incident not found"}, status_code=404)
    
    # Also reset collision/parking flags in the intersection simulator
    sim = intersections.get(incident.intersection_id)
    if sim:
        sim.has_active_accident = False
        sim.has_illegal_parker = False

    return JSONResponse({
        "status": "success",
        "message": f"Incident {incident_id} marked as RESOLVED and cleared",
        "incident": incident.to_dict()
    })


@app.post("/api/simulate/accident")
async def trigger_accident(intersection_id: str = Form("INT-101")):
    sim = intersections.get(intersection_id)
    if sim:
        sim.trigger_accident()
        return JSONResponse({"status": "success", "message": f"Collision scenario injected at {sim.name}"})
    return JSONResponse({"status": "error", "message": "Intersection not found"}, status_code=404)


@app.post("/api/simulate/illegal_parking")
async def trigger_illegal_parking(intersection_id: str = Form("INT-101")):
    sim = intersections.get(intersection_id)
    if sim:
        sim.trigger_illegal_parking()
        return JSONResponse({"status": "success", "message": f"Illegal parking violation injected at {sim.name}"})
    return JSONResponse({"status": "error", "message": "Intersection not found"}, status_code=404)


@app.post("/api/simulate/emergency")
async def trigger_emergency(intersection_id: str = Form("INT-101")):
    sim = intersections.get(intersection_id)
    if sim:
        sim.trigger_emergency()
        return JSONResponse({"status": "success", "message": f"Emergency ambulance priority corridor triggered at {sim.name}"})
    return JSONResponse({"status": "error", "message": "Intersection not found"}, status_code=404)


@app.post("/api/controller/mode")
async def set_controller_mode(intersection_id: str = Form(...), mode: str = Form(...)):
    sim = intersections.get(intersection_id)
    if sim and mode in ["RL_DQN", "FIXED_TIME", "MANUAL"]:
        sim.controller.set_mode(mode)
        return JSONResponse({"status": "success", "mode": mode, "intersection": intersection_id})
    return JSONResponse({"status": "error", "message": "Invalid parameters"}, status_code=400)


@app.post("/api/controller/override")
async def manual_signal_override(intersection_id: str = Form(...), phase: str = Form(...)):
    sim = intersections.get(intersection_id)
    if sim:
        target_phase = TrafficSignalState.NS_GREEN if phase == "NS" else TrafficSignalState.EW_GREEN
        sim.controller.set_mode("MANUAL", override_phase=target_phase)
        return JSONResponse({"status": "success", "override": phase, "intersection": intersection_id})
    return JSONResponse({"status": "error", "message": "Intersection not found"}, status_code=404)


@app.get("/api/analytics")
async def get_analytics():
    total_vehicles = sum(sim.controller.metrics["rl"]["vehicles_passed"] for sim in intersections.values())
    total_co2 = sum(sim.controller.metrics["rl"]["co2_saved_kg"] for sim in intersections.values())
    avg_rl_delay = sum(sim.controller.metrics["rl"]["avg_delay_sec"] for sim in intersections.values()) / len(intersections)
    avg_fixed_delay = sum(sim.controller.metrics["fixed"]["avg_delay_sec"] for sim in intersections.values()) / len(intersections)
    delay_reduction_pct = round(((avg_fixed_delay - avg_rl_delay) / avg_fixed_delay) * 100, 1)

    # Aggregate counts by vehicle class
    v_classes = {"car": 0, "bus": 0, "truck": 0, "motorcycle": 0, "emergency": 0}
    for sim in intersections.values():
        for k, v in sim.detector.total_counted.items():
            if k in v_classes:
                v_classes[k] += v

    return JSONResponse({
        "total_vehicles_processed": int(total_vehicles),
        "avg_rl_delay_sec": round(avg_rl_delay, 1),
        "avg_fixed_delay_sec": round(avg_fixed_delay, 1),
        "delay_reduction_pct": max(0.0, delay_reduction_pct),
        "co2_saved_kg": round(total_co2, 2),
        "active_incidents_count": len(incident_manager.get_active()),
        "total_incidents_count": len(incident_manager.incidents),
        "vehicle_breakdown": v_classes,
        "intersections_count": len(intersections)
    })


# WebSocket for Ultra-Low-Latency Telemetry Streaming to UI
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Construct live state payload
            int_data = {}
            for iid, sim in intersections.items():
                st = sim.controller.get_status()
                int_data[iid] = {
                    "signal_ns": st["signal_heads"]["north_south"],
                    "signal_ew": st["signal_heads"]["east_west"],
                    "countdown": st["countdown_seconds"],
                    "phase_name": st["phase_name"],
                    "mode": st["mode"],
                    "vehicles": len(sim.vehicles),
                    "efficiency": st["metrics"]["efficiency_gain_pct"],
                    "has_accident": sim.has_active_accident,
                    "has_emergency": sim.active_emergency,
                    "has_illegal_parking": sim.has_illegal_parker,
                    "queues": sim.detector._calculate_queues(sim.width, sim.height)
                }

            payload = {
                "timestamp": time.time(),
                "intersections": int_data,
                "active_incidents": incident_manager.get_active()
            }
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(0.2)  # 5 Hz telemetry
    except WebSocketDisconnect:
        pass
    except Exception as e:
        pass

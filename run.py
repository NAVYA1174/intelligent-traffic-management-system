import uvicorn
import sys
import os

if __name__ == "__main__":
    print("=" * 70)
    print("  INTELLIGENT TRAFFIC MANAGEMENT SYSTEM (ITMS) - OPERATIONS CENTER")
    print("  Smart City • Computer Vision • YOLOv8 • Deep RL Adaptive Signals")
    print("=" * 70)
    print("  Server starting at: http://127.0.0.1:8000")
    print("  TOC Dashboard:     http://127.0.0.1:8000")
    print("  API Documentation: http://127.0.0.1:8000/docs")
    print("=" * 70)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False, log_level="info")

import asyncio
import json
import os
import shutil
from datetime import datetime

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput

RECORDINGS_DIR = os.path.expanduser("~/recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

picam2 = Picamera2()
video_config = picam2.create_video_configuration(
    main={"size": (1280, 720)},
    lores={"size": (1280, 720), "format": "YUV420"}
)
picam2.configure(video_config)
picam2.start()

recording = False
h264_encoder = None
current_filename = None

class CameraVideoTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        array = picam2.capture_array("lores")
        frame = VideoFrame.from_ndarray(array, format="yuv420p")
        frame.pts = pts
        frame.time_base = time_base
        return frame

pcs = set()

async def offer(request):
    params = await request.json()
    offer_desc = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    pc.addTrack(CameraVideoTrack())

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        if pc.connectionState == "failed" or pc.connectionState == "closed":
            await pc.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(offer_desc)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })

async def record_start(request):
    global recording, h264_encoder, current_filename
    if recording:
        return web.json_response({"status": "already recording", "file": current_filename})

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    current_filename = os.path.join(RECORDINGS_DIR, f"boat_{timestamp}.h264")

    h264_encoder = H264Encoder()
    picam2.start_encoder(h264_encoder, FileOutput(current_filename))
    recording = True

    return web.json_response({"status": "recording started", "file": current_filename})

async def record_stop(request):
    global recording, h264_encoder, current_filename
    if not recording:
        return web.json_response({"status": "not recording"})

    picam2.stop_encoder(h264_encoder)
    recording = False
    finished_file = current_filename
    h264_encoder = None
    current_filename = None

    return web.json_response({"status": "recording stopped", "file": finished_file})

async def telemetry(request):
    total, used, free = shutil.disk_usage(RECORDINGS_DIR)
    return web.json_response({
        "recording": recording,
        "file": current_filename,
        "storage_free_gb": round(free / (1024**3), 2),
        "storage_total_gb": round(total / (1024**3), 2)
    })

async def viewer(request):
    return web.FileResponse(os.path.expanduser("~/webxr_viewer.html"))

async def three_js(request):
    return web.FileResponse(os.path.expanduser("~/three.module.js"),
                             headers={"Content-Type": "application/javascript"})

latest_control = {"throttle": 0.0, "steer": 0.0, "reverse": False}

# ---- hardware actuation (throttle ESC + steering servo) ----
# Optional: the server still runs (streaming/recording) if GPIO libs are missing.
# For stable ESC/servo pulses use the pigpio pin factory:
#     GPIOZERO_PIN_FACTORY=pigpio python3 webrtc_stream.py   (with `sudo pigpiod` running)
THROTTLE_PIN = 12   # BCM 12 = physical pin 32 (PWM0) -> ESC signal wire
STEER_PIN = 13      # BCM 13 = physical pin 33 (PWM1) -> steering servo signal wire
# Common ground: physical pin 34. Signal + GND only from the Pi; let the ESC's
# BEC power the servo. See HARDWARE.md for the full wiring table.
try:
    from gpiozero import Servo
    throttle_out = Servo(THROTTLE_PIN)
    steer_out = Servo(STEER_PIN)
    HARDWARE = True
except Exception as e:
    throttle_out = steer_out = None
    HARDWARE = False
    print(f"[control] hardware actuation disabled ({e}); running in software-only mode")

def _clamp(v):
    return max(-1.0, min(1.0, v))

def apply_control(throttle, steer):
    """Drive the ESC/servo. throttle/steer are -1..1; reverse is already applied on the wire."""
    if not HARDWARE:
        return
    try:
        throttle_out.value = _clamp(throttle)
        steer_out.value = _clamp(steer)
    except Exception:
        pass

async def control_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                latest_control["throttle"] = float(data.get("throttle", 0.0))
                latest_control["steer"] = float(data.get("steer", 0.0))
                latest_control["reverse"] = bool(data.get("reverse", False))
                apply_control(latest_control["throttle"], latest_control["steer"])
            except Exception:
                pass
    # stop the motor if the control link drops
    apply_control(0.0, 0.0)
    return ws

async def control_status(request):
    return web.json_response(latest_control)

app = web.Application()
app.router.add_post("/offer", offer)
app.router.add_get("/record/start", record_start)
app.router.add_get("/record/stop", record_stop)
app.router.add_get("/telemetry", telemetry)
app.router.add_get("/viewer", viewer)
app.router.add_get("/three.module.js", three_js)
app.router.add_get("/ws/control", control_ws)
app.router.add_get("/control_status", control_status)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=5000)

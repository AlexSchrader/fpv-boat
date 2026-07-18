from flask import Flask, Response, jsonify, send_file
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder, H264Encoder
from picamera2.outputs import FileOutput
import io
import shutil
import os
import time
from datetime import datetime
from threading import Condition

app = Flask(__name__)

RECORDINGS_DIR = os.path.expanduser("~/recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (1280, 720)}))

stream_output = StreamingOutput()
mjpeg_encoder = MJPEGEncoder()

picam2.start()
picam2.start_encoder(mjpeg_encoder, FileOutput(stream_output))

# recording state
recording = False
h264_encoder = None
current_filename = None

def generate():
    while True:
        with stream_output.condition:
            stream_output.condition.wait()
            frame = stream_output.frame
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(generate(),
                     mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    return '<html><body><img src="/video_feed" width="1280" height="720"></body></html>'

@app.route('/record/start', methods=['POST', 'GET'])
def record_start():
    global recording, h264_encoder, current_filename
    if recording:
        return jsonify({"status": "already recording", "file": current_filename})

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    current_filename = os.path.join(RECORDINGS_DIR, f"boat_{timestamp}.h264")

    h264_encoder = H264Encoder()
    picam2.start_encoder(h264_encoder, FileOutput(current_filename))
    recording = True

    return jsonify({"status": "recording started", "file": current_filename})

@app.route('/record/stop', methods=['POST', 'GET'])
def record_stop():
    global recording, h264_encoder, current_filename
    if not recording:
        return jsonify({"status": "not recording"})

    picam2.stop_encoder(h264_encoder)
    recording = False
    finished_file = current_filename
    h264_encoder = None
    current_filename = None

    return jsonify({"status": "recording stopped", "file": finished_file})

@app.route('/record/status')
def record_status():
    return jsonify({"recording": recording, "file": current_filename})

@app.route('/three.module.js')
def three_js():
    return send_file(os.path.expanduser('~/three.module.js'), mimetype='application/javascript')

@app.route('/viewer')
def viewer():
    return send_file(os.path.expanduser('~/webxr_viewer.html'))

@app.route('/telemetry')
def telemetry():
    total, used, free = shutil.disk_usage(RECORDINGS_DIR)
    return jsonify({
        'recording': recording,
        'file': current_filename,
        'storage_free_gb': round(free / (1024**3), 2),
        'storage_total_gb': round(total / (1024**3), 2)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)

import os
import time
import uuid
from flask import Flask, render_template_string, request, Response, session, redirect, jsonify

# ==========================================
# 1. Configuration Management
# ==========================================
CONFIG_FILE = 'config.py'

# Default settings
DEFAULT_CONFIG = {
    "RESOLUTION": "(1280, 720)",
    "MAX_MBPS": "0",
    "PASSWORD": "'admin'",
    "PORT": "6942"
}

def setup_config():
    # Create file if it doesn't exist
    if not os.path.exists(CONFIG_FILE):
        print("Generating default config.py...")
        with open(CONFIG_FILE, 'w') as f:
            for key, value in DEFAULT_CONFIG.items():
                f.write(f"{key} = {value}\n")
    
    # Import the config
    import config
    
    # Check if PORT specifically is missing from an existing config.py
    if not hasattr(config, 'PORT'):
        print("Adding missing PORT setting to config.py...")
        with open(CONFIG_FILE, 'a') as f:
            f.write(f"\nPORT = {DEFAULT_CONFIG['PORT']}\n")
        # Refresh the config object
        import importlib
        importlib.reload(config)
    
    return config

config = setup_config()

try:
    import cv2
    import numpy as np
    import mss
    import pyautogui
except ImportError:
    print("Missing dependencies. Please run: pip install flask opencv-python numpy mss pyautogui")
    exit(1)

# PyAutoGUI safety settings
pyautogui.FAILSAFE = False 
pyautogui.PAUSE = 0

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ==========================================
# 2. State & Connection Management
# ==========================================
state = {
    "active_session": None,
    "last_heartbeat": 0
}
HEARTBEAT_TIMEOUT = 3.0 

def is_session_active():
    if state["active_session"] is None:
        return False
    if time.time() - state["last_heartbeat"] > HEARTBEAT_TIMEOUT:
        state["active_session"] = None
        return False
    return True

# ==========================================
# 3. HTML Templates
# ==========================================
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Remote Login</title></head>
<body style="font-family: Arial; display: flex; justify-content: center; align-items: center; height: 100vh; background: #222; color: white;">
    <form method="POST" action="/login" style="background: #333; padding: 20px; border-radius: 8px;">
        <h2>Login to Remote PC</h2>
        {% if error %}<p style="color: red;">{{ error }}</p>{% endif %}
        <input type="password" name="password" placeholder="Password" style="padding: 10px; width: 200px;" autofocus><br><br>
        <button type="submit" style="padding: 10px 20px; cursor: pointer;">Connect</button>
    </form>
</body>
</html>
"""

REMOTE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Web Remote</title>
    <style>
        body { margin: 0; background: black; overflow: hidden; display: flex; flex-direction: column; height: 100vh; font-family: Arial, sans-serif; }
        #toolbar { background: #333; padding: 10px; display: flex; gap: 10px; align-items: center; border-bottom: 2px solid #222; }
        #toolbar button, #toolbar input { padding: 8px 12px; background: #555; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
        #toolbar button:hover { background: #777; }
        #toolbar input { width: 50px; text-align: center; cursor: text; }
        #toolbar .f-key-group { display: flex; align-items: center; gap: 5px; margin-left: 20px; color: white; }
        #screen-container { flex-grow: 1; display: flex; justify-content: center; align-items: center; overflow: hidden; position: relative; }
        #screen { max-width: 100%; max-height: 100%; cursor: crosshair; }
    </style>
</head>
<body>
    <div id="toolbar">
        <button onclick="sendHotkey('esc')">ESC</button>
        <button onclick="sendHotkey('tab')">TAB</button>
        <button onclick="sendHotkey('printscreen')">PRT SC</button>
        <button onclick="sendHotkey('delete')">DEL</button>
        <button onclick="sendHotkey('ctrl', 'alt', 'delete')">CTRL ALT DEL</button>
        <div class="f-key-group">
            <span>F-</span>
            <input type="number" id="fNum" min="1" max="24" value="1">
            <button onclick="sendFKey()">Send</button>
        </div>
    </div>
    <div id="screen-container">
        <img id="screen" src="/video_feed" draggable="false">
    </div>
    <script>
        const img = document.getElementById('screen');
        setInterval(() => fetch('/heartbeat'), 1000);
        function sendInput(type, data) {
            fetch('/input', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({type: type, ...data})
            });
        }
        function sendHotkey(...keys) { sendInput('hotkey', {keys: keys}); }
        function sendFKey() {
            const num = document.getElementById('fNum').value;
            if(num >= 1 && num <= 24) { sendHotkey('f' + num); }
        }
        function handleMouse(e, type) {
            e.preventDefault();
            const rect = img.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width;
            const y = (e.clientY - rect.top) / rect.height;
            sendInput(type, {x: x, y: y, button: e.button});
        }
        img.addEventListener('mousemove', (e) => handleMouse(e, 'mousemove'));
        img.addEventListener('mousedown', (e) => handleMouse(e, 'mousedown'));
        img.addEventListener('mouseup', (e) => handleMouse(e, 'mouseup'));
        img.addEventListener('contextmenu', (e) => e.preventDefault());
        window.addEventListener('keydown', (e) => {
            if (e.target.tagName !== 'INPUT') {
                e.preventDefault();
                sendInput('keydown', {key: e.key});
            }
        });
        window.addEventListener('keyup', (e) => {
            if (e.target.tagName !== 'INPUT') {
                e.preventDefault();
                sendInput('keyup', {key: e.key});
            }
        });
        window.addEventListener('beforeunload', () => {
            fetch('/disconnect', {method: 'POST', keepalive: true});
        });
    </script>
</body>
</html>
"""

# ==========================================
# 4. Video Streaming Logic
# ==========================================
def generate_frames():
    sct = mss.mss()
    monitor_idx = 1 if len(sct.monitors) > 1 else 0
    monitor = sct.monitors[monitor_idx]
    max_bytes_per_sec = (config.MAX_MBPS * 1024 * 1024) / 8 if config.MAX_MBPS > 0 else 0

    while is_session_active():
        start_time = time.time()
        img = np.array(sct.grab(monitor))
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        img = cv2.resize(img, config.RESOLUTION)
        ret, jpeg = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if not ret: continue
        frame_bytes = jpeg.tobytes()
        if max_bytes_per_sec > 0:
            target_time = len(frame_bytes) / max_bytes_per_sec
            elapsed = time.time() - start_time
            if elapsed < target_time:
                time.sleep(target_time - elapsed)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    yield b''

# ==========================================
# 5. Flask Routes
# ==========================================
@app.route('/', methods=['GET'])
def index():
    if 'sid' in session and session['sid'] == state['active_session'] and is_session_active():
        return render_template_string(REMOTE_HTML)
    return render_template_string(LOGIN_HTML, error=None)

@app.route('/login', methods=['POST'])
def login():
    if is_session_active():
        return render_template_string(LOGIN_HTML, error="Another user is currently connected.")
    pwd = request.form.get('password', '')
    if pwd == config.PASSWORD:
        sid = str(uuid.uuid4())
        session['sid'] = sid
        state['active_session'] = sid
        state['last_heartbeat'] = time.time()
        return redirect('/')
    return render_template_string(LOGIN_HTML, error="Invalid password")

@app.route('/video_feed')
def video_feed():
    if 'sid' not in session or session['sid'] != state['active_session']:
        return "Unauthorized", 401
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/heartbeat')
def heartbeat():
    if 'sid' in session and session['sid'] == state['active_session']:
        state['last_heartbeat'] = time.time()
        return "OK", 200
    return "Unauthorized", 401

@app.route('/disconnect', methods=['POST'])
def disconnect():
    if 'sid' in session and session['sid'] == state['active_session']:
        state['active_session'] = None
        session.clear()
    return "Disconnected", 200

@app.route('/input', methods=['POST'])
def handle_input():
    if 'sid' not in session or session['sid'] != state['active_session']:
        return "Unauthorized", 401
    data = request.json
    event_type = data.get('type')
    try:
        screen_w, screen_h = pyautogui.size()
        if event_type == 'mousemove':
            x, y = int(data['x'] * screen_w), int(data['y'] * screen_h)
            pyautogui.moveTo(x, y)
        elif event_type in ['mousedown', 'mouseup']:
            x, y = int(data['x'] * screen_w), int(data['y'] * screen_h)
            btn = 'left' if data['button'] == 0 else ('middle' if data['button'] == 1 else 'right')
            if event_type == 'mousedown': pyautogui.mouseDown(x=x, y=y, button=btn)
            else: pyautogui.mouseUp(x=x, y=y, button=btn)
        elif event_type in ['keydown', 'keyup']:
            key = data['key'].lower()
            key_map = {'enter': 'enter', 'backspace': 'backspace', 'shift': 'shift', 'control': 'ctrl', 'alt': 'alt', 'escape': 'esc'}
            key = key_map.get(key, key)
            if len(key) == 1 or key in pyautogui.KEYBOARD_KEYS:
                if event_type == 'keydown': pyautogui.keyDown(key)
                else: pyautogui.keyUp(key)
        elif event_type == 'hotkey':
            keys = data.get('keys', [])
            valid_keys = [str(k).lower() for k in keys if str(k).lower() in pyautogui.KEYBOARD_KEYS or len(str(k)) == 1]
            if valid_keys: pyautogui.hotkey(*valid_keys)
    except Exception as e:
        print(f"Input error: {e}")
    return "OK", 200

if __name__ == '__main__':
    print("Starting Remote Desktop Web Server...")
    print(f"Access via http://localhost:{config.PORT}")
    # Listen on all interfaces so other devices can connect
    app.run(host='0.0.0.0', port=config.PORT, threaded=True)

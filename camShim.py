import struct
import time
import threading
from datetime import datetime
import serial
import dearpygui.dearpygui as dpg

# --- Global State ---
ser = None
running = False
is_recording = False
record_start_time = 0.0

MAX_LOG_LINES = 200
log_items = []

# --- Protocol Instruction Dictionary ---
CMD_DESCRIPTIONS = {
    0xF1: "Keep-Alive / Ping",
    0x11: "Set Datetime (YYYYMMDDHHMMSS)",
    0x20: "Start Recording Command",
    0x21: "Camera Sensor Enable/Disable",
    0x22: "Prepare Camera / Lock Pipeline",
    0x25: "Commit Video File / Stop Recording",
    0x35: "Set Target Filename",
    0x58: "Init Handshake Phase A",
    0x59: "Init Handshake Phase B",
    0x5A: "Preview Stream State Toggle",
    0x61: "Init Handshake Phase C",
    0x80: "Network Configuration Request",
    0xB0: "Init Handshake Phase D",
    0x0F: "Timestamp Sync Check",
    0x0B: "Lens/Hardware Metrics Payload",
    0x04: "Frame Processing Pulse",
    0x28: "Camera Model/Serial String"
}

def decode_packet_meaning(packet, is_tx):
    if len(packet) < 5 or packet[0] != 0xE0:
        return "Malformed/Unknown Data Fragment"
    
    cmd_byte = packet[4]
    if not is_tx and cmd_byte == 0x01 and len(packet) > 5:
        target_cmd = packet[5]
        return f"Response -> {CMD_DESCRIPTIONS.get(target_cmd, f'Unknown Command (0x{target_cmd:02X})')}"
    
    return CMD_DESCRIPTIONS.get(cmd_byte, f"Unknown Instruction (0x{cmd_byte:02X})")

# --- Packet Generation ---
def make_packet(command_byte, payload_bytes=b""):
    length = 1 + len(payload_bytes)
    data = bytearray([0x02, 0x01, length, command_byte]) + bytearray(payload_bytes)
    checksum = sum(data) & 0xFF
    return bytearray([0xE0]) + data + bytearray([checksum])

# --- Enhanced Color Logging ---
def system_log(msg, color=[255, 255, 255]):
    """Logs system events (connect, errors) in standard colors."""
    item_id = dpg.add_text(f"[SYS] {msg}", color=color, parent="log_scroll_container")
    _manage_log_buffer(item_id)

def log_packet(packet_bytes, is_tx=True):
    """Logs packets with explicit color coding."""
    prefix = "TX" if is_tx else "RX"
    hex_str = packet_bytes.hex(' ').upper()
    meaning = decode_packet_meaning(packet_bytes, is_tx)
    
    log_line = f"[{prefix}] {hex_str:<50} | {meaning}"
    
    # Assign Colors: Cyan for TX, Orange for RX
    text_color = [100, 200, 255] if is_tx else [255, 200, 100]
    
    item_id = dpg.add_text(log_line, color=text_color, parent="log_scroll_container")
    _manage_log_buffer(item_id)

def _manage_log_buffer(new_item_id):
    """Keeps the UI performant by pruning old text items and handling autoscroll."""
    log_items.append(new_item_id)
    
    # Prune oldest items to prevent memory bloat
    if len(log_items) > MAX_LOG_LINES:
        oldest_item = log_items.pop(0)
        dpg.delete_item(oldest_item)
        
    if dpg.get_value("autoscroll_check"):
        dpg.set_y_scroll("log_scroll_container", -1)

# --- Background Loops ---
def keep_alive_task():
    global running, ser
    while running:
        if ser and ser.is_open:
            try:
                pkt = make_packet(0xF1)
                log_packet(pkt, is_tx=True)
                ser.write(pkt)
                
                time.sleep(0.2)
                if ser.in_waiting:
                    raw_rx = ser.read(ser.in_waiting)
                    chunks = raw_rx.split(b'\xE0')
                    for chunk in chunks:
                        if chunk:
                            log_packet(b'\xE0' + chunk, is_tx=False)
            except Exception as e:
                system_log(f"Comm Loop Broken: {e}", color=[255, 100, 100])
                running = False
                break
        time.sleep(0.5)

def timer_update_task():
    global is_recording, record_start_time
    while True:
        if is_recording:
            elapsed = time.time() - record_start_time
            mins, secs = divmod(elapsed, 60)
            time_str = f"STATUS: RECORDING ● {int(mins):02d}:{secs:04.1f}"
            dpg.set_value("timer_status", time_str)
            dpg.configure_item("timer_status", color=[255, 60, 60])
        else:
            dpg.set_value("timer_status", "STATUS: IDLE")
            dpg.configure_item("timer_status", color=[60, 255, 60])
        time.sleep(0.1)

# --- Control Sequences ---
def toggle_connection():
    global ser, running
    if not running:
        port = dpg.get_value("port_input")
        baud = int(dpg.get_value("baud_input"))
        try:
            ser = serial.Serial(port, baudrate=baud, timeout=0.1)
            running = True
            dpg.set_item_label("connect_btn", "Disconnect")
            system_log(f"Connected to {port} at {baud} baud", color=[100, 255, 100])
            threading.Thread(target=keep_alive_task, daemon=True).start()
        except Exception as e:
            system_log(f"Connection Error: {e}", color=[255, 100, 100])
    else:
        running = False
        if ser: ser.close()
        dpg.set_item_label("connect_btn", "Connect")
        system_log("Disconnected", color=[200, 200, 200])

def action_init():
    def _task():
        pkt = make_packet(0x58, [0x00]); ser.write(pkt); log_packet(pkt)
        time.sleep(0.1)
        pkt = make_packet(0x59, [0x00]); ser.write(pkt); log_packet(pkt)
        time.sleep(0.1)
        time_str = datetime.now().strftime("%Y%m%d%H%M%S").encode('ascii')
        pkt = make_packet(0x11, time_str); ser.write(pkt); log_packet(pkt)
    threading.Thread(target=_task).start()

def action_preview_on():
    def _task():
        pkt1 = make_packet(0x5A, [0x00]); ser.write(pkt1); log_packet(pkt1)
        time.sleep(0.1)
        pkt2 = make_packet(0x21, [0x00, 0x00, 0x00]); ser.write(pkt2); log_packet(pkt2)
    threading.Thread(target=_task).start()

def action_preview_off():
    def _task():
        pkt1 = make_packet(0x5A, [0x00]); ser.write(pkt1); log_packet(pkt1)
        time.sleep(0.1)
        pkt2 = make_packet(0x21, [0x00, 0x00, 0x01]); ser.write(pkt2); log_packet(pkt2)
    threading.Thread(target=_task).start()

def action_start_record():
    global is_recording, record_start_time
    def _task():
        global is_recording, record_start_time
        pkt_prep = make_packet(0x22)
        ser.write(pkt_prep); log_packet(pkt_prep)
        
        time.sleep(2.1)
        
        pkt_start = make_packet(0x20)
        ser.write(pkt_start); log_packet(pkt_start)
        
        record_start_time = time.time()
        is_recording = True
    threading.Thread(target=_task).start()

def action_stop_temp():
    global is_recording
    is_recording = False
    def _task():
        pkt = make_packet(0x22); ser.write(pkt); log_packet(pkt)
        time.sleep(0.2)
        filename = b"nowrec.mp4".ljust(32, b'\x00')
        pkt = make_packet(0x35, filename); ser.write(pkt); log_packet(pkt)
        time.sleep(0.1)
        pkt = make_packet(0x25); ser.write(pkt); log_packet(pkt)
    threading.Thread(target=_task).start()

def action_save_final():
    global is_recording
    is_recording = False
    def _task():
        pkt = make_packet(0x22); ser.write(pkt); log_packet(pkt)
        time.sleep(0.2)
        raw_name = dpg.get_value("filename_input").encode('ascii').ljust(32, b'\x00')
        pkt = make_packet(0x35, raw_name); ser.write(pkt); log_packet(pkt)
        time.sleep(0.1)
        pkt = make_packet(0x25); ser.write(pkt); log_packet(pkt)
    threading.Thread(target=_task).start()

# --- GUI Setup ---
dpg.create_context()

with dpg.window(label="main", width=1000, height=470, no_close=True):
    with dpg.group(horizontal=True):
        dpg.add_text("System Status: ")
        dpg.add_text("STATUS: IDLE", tag="timer_status", color=[60, 255, 60])
    
    dpg.add_separator()
    
    with dpg.group(horizontal=True):
        dpg.add_input_text(tag="port_input", default_value="COM1", width=100)
        dpg.add_combo(tag="baud_input", items=["9600", "38400", "57600", "115200"], default_value="115200", width=90)
        dpg.add_button(tag="connect_btn", label="Connect", callback=toggle_connection)
        
    dpg.add_separator()
    
    with dpg.group(horizontal=True):
        dpg.add_button(label="1. Init Cabinet Sync", callback=action_init, width=150)
        dpg.add_button(label="Preview ON", callback=action_preview_on, width=140)
        dpg.add_button(label="Preview OFF", callback=action_preview_off, width=140)
        
    dpg.add_separator()
    
    with dpg.group(horizontal=True):
        dpg.add_button(label="2. Start Record", callback=action_start_record, width=150)
        dpg.add_button(label="3. Stop (nowrec)", callback=action_stop_temp, width=150)
        
    with dpg.group(horizontal=True):
        dpg.add_input_text(tag="filename_input", default_value="video.mp4", width=220)
        dpg.add_button(label="4. Commit/Upload Final Video", callback=action_save_final)
        
    dpg.add_separator()
    
    with dpg.group(horizontal=True):
        dpg.add_text("Decoded Serial Stream Logic:")
        dpg.add_checkbox(label="Autoscroll Log", tag="autoscroll_check", default_value=True)
        
    # The container that holds our color-coded text lines
    with dpg.child_window(tag="log_scroll_container", width=-1, height=280):
        pass # Empty to start, lines are injected here

# Start background UI updates
threading.Thread(target=timer_update_task, daemon=True).start()

dpg.create_viewport(title='camShim V1', width=1010, height=505)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()
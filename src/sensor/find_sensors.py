import logging
from serial.tools import list_ports

logger = logging.getLogger(__name__)

# Find the serial port for Sendot sensors by checking the USB-serial vid and pid
# Uses FTDI FT230x VID 0x0403 PID 0x6015
VID = 0x0403
PID = 0x6015
serial_ports = [
    p.device for p in list_ports.comports() if p.pid == PID and p.vid == VID
]

if not serial_ports:
    raise RuntimeError("No Sendot sensors found. Please check connection.")
else:
    logger.debug(f"Found {len(serial_ports)} Sendot sensors.")

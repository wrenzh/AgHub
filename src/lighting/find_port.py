from serial.tools import list_ports

# Find and initialize the serial port for LoRa module by checking the USB-serial vid and pid
# EByte-LoRa module uses CH340 VID 0x1A86 PID 0x7523
# USB-RS232 adapter cable uses FTDI FT232R VID 0x0403 PID 0x6001
VID = [0x1A86, 0x0403]
PID = [0x7523, 0x6001]
serial_ports = [
    p.device for p in list_ports.comports() if (p.pid, p.vid) in zip(PID, VID)
]

if not serial_ports:
    raise RuntimeError("No EByte LoRa module found. Please check the connection.")
if len(serial_ports) > 1:
    raise RuntimeError("More than 1 module found. Please connect only one module.")

portname = serial_ports[0]

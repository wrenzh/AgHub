import asyncio
from fastapi.responses import StreamingResponse
import serial
import logging
import datetime
import time
from typing import Annotated, Callable
from .find_sensors import serial_ports
from fastapi import HTTPException, Query, Request, Response, APIRouter
from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)

# Initailize the serial ports
logger.debug(f"Opening serial ports {str(serial_ports)} ...")
ports = [
    serial.Serial(
        port=p,
        baudrate=19200,
        bytesize=8,
        stopbits=1,
        parity=serial.PARITY_NONE,
        timeout=5,
    )
    for p in serial_ports
]


class SensorException(Exception):
    def __init__(self, message):
        super().__init__(message)
        logger.warning(f"SensorException: {message}")


def send(id: int, msg: str):
    ports[id].write(msg.encode(encoding="ascii"))
    logger.debug(f"Port {id} wrote {msg}")


def receive(id: int, terminator: str = "\r\n") -> str:
    msg = (
        ports[id]
        .read_until(terminator.encode(encoding="ascii"))
        .decode(encoding="ascii")
    )
    logger.debug(f"Port {id} read {msg}")
    if msg.startswith("Invalid command"):
        raise SensorException("Invalid command")
    if msg.startswith("No sensor"):
        raise SensorException("No FluoMini sensor connected")
    if msg.startswith("No or invalid response"):
        raise SensorException("Response from FluoMini sensor is incorrect")
    if msg.startswith("Low signal"):
        raise SensorException(
            "FluoMini sensor signal too low, likely due to coating defect or optical fibre issue"
        )
    if msg.startswith("Error opening logfile"):
        raise SensorException("Attempt to open logfile failed")
    if msg.startswith("No SD card"):
        raise SensorException("No SD card inserted or the card cannot be read")
    if not msg.endswith(terminator):
        raise SensorException(f"Port {id} read timeout")
    return msg


sensor_names = []
sensor_types = []
for i in range(len(serial_ports)):
    # Discard any existing message in buffer
    ports[i].reset_output_buffer()
    ports[i].reset_input_buffer()

    # Find sensor type
    send(i, "I\n")
    msg = receive(i)
    [res, sensor_name, sensor_type] = msg.split(":")
    if res != "ID":
        raise SensorException(
            f"Sensor response header incorrect, expected ID, got {res}"
        )
    sensor_names.append(sensor_name)
    sensor_types.append(int(sensor_type))

    # Set string format to include multiline with units
    send(i, "SSF,1\n")
    # Read sensor string format setting acknowledgement
    ack = ports[i].read(1)
    if ack != b"\x06":
        raise SensorException(
            f"Sensor string format setting ackowledgement incorrect, expected 0x06, got {str(ack)}"
        )

    # Set sensor clock
    clock_time = datetime.datetime.now()
    send(i, f"T,{clock_time.strftime('%y,%m,%d,%H,%M,%S')}\n")
    # Return is without new line and always 28 bytes long
    res = ports[i].read(28).decode(encoding="ascii").rsplit(" ")[2]
    set_time = datetime.datetime.strptime(res, "%y-%m-%dT%H:%M:%S")

    if clock_time - set_time > datetime.timedelta(seconds=2):
        raise SensorException(
            f"Sensor clock setting response incorrect, expected {clock_time.strftime('%y-%m-%dT%H:%M:%S')}, got {res}"
        )

    # Set measurement unit, always default
    send(i, "U,1\n")
    msg = receive(i)
    if msg.startswith("Value invalid"):
        raise SensorException(f"Sensor measurement unit setting invalid")

    # Wait for any remaining command to finish
    # Previous command may or may not return multple lines depending on sensor type
    time.sleep(1)
    # Discard any existing message in buffer
    ports[i].reset_output_buffer()
    ports[i].reset_input_buffer()

logger.debug("Opening serial ports successful")


class BlockingRoute(APIRoute):
    """
    Make sure each sensor serial port finishes reading/writing before
    responding to the next request
    """

    def __init__(self, *args, **kargs):
        super().__init__(*args, **kargs)
        self.locks = [asyncio.Lock() for _ in serial_ports]

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            if (not request.query_params) or (not "id" in request.query_params):
                response: Response = await original_route_handler(request)
                return response

            id = int(request.query_params["id"])
            if id >= len(self.locks):
                raise HTTPException(status_code=404, detail="No sensor found")

            await self.locks[id].acquire()
            try:
                response: Response = await original_route_handler(request)
            except SensorException as e:
                raise HTTPException(status_code=505, detail=str(e))
            self.locks[id].release()
            return response

        return custom_route_handler


router = APIRouter(prefix="/api/sensor", tags=["sensor"])
router.route_class = BlockingRoute


@router.get("/names")
async def get_sensor_names():
    return sensor_names


@router.get("/name")
async def get_sensor_name(
    id: Annotated[int, Query(title="Sensor ID in serial port list")] = 0
):
    send(id, "I\n")
    msg = receive(id)
    [res, sensor_name, _] = msg.split(":")
    if res != "ID":
        raise SensorException(
            f"Sensor response header incorrect, expected ID, got {res}"
        )
    return {"sensor_name": sensor_name}


@router.post("/restart")
async def restart(id: Annotated[int, Query(title="Sensor ID in serial port list")] = 0):
    send(id, "X\n")
    # Wait until the sensor reboots
    await asyncio.sleep(1)
    msg = receive(id)
    if not msg.startswith("soft reset"):
        raise SensorException(f"Sensor soft restart failed, got {msg}")


@router.get("/single_measurement")
async def single_measurement(
    id: Annotated[int, Query(title="Sensor ID in serial port list")] = 0
):
    send(id, "M\n")
    msg = receive(id)

    return {"result": msg.rstrip()}


@router.get("/multiple_measurement")
async def multiple_measurement(
    id: Annotated[int, Query(title="Sensor ID in serial port list")] = 0,
    interval: Annotated[
        int, Query(title="Interval in seconds between measurements", min=3, max=300)
    ] = 5,
    count: Annotated[
        int, Query(title="Number of measurements to make", min=1, max=500)
    ] = 3,
):
    send(id, f"INT,{interval}\n")
    msg = receive(id)
    if not msg.rstrip().endswith(str(interval)):
        raise SensorException(
            f"Sensor multiple measurements interval setting incorrect, expected {interval}, got {msg.rstrip()}"
        )
    send(id, " \n")
    msg = receive(id)
    if not msg.startswith("Auto measure on"):
        send(id, " \n")
        raise SensorException(
            f"Sensor multiple measurement on did not succeed, expected Auto measure on, got {msg}"
        )

    def iter_measurement():
        for i in range(count):
            msg = receive(id)
            yield msg
            if i == count - 1:
                send(id, " \n")
                msg = receive(id)
                if not msg.startswith("Auto measure off"):
                    raise SensorException(
                        f"Sensor multiple measurement off did not succeed, expected Auto measure off, got {msg}"
                    )
                return
            time.sleep(interval)

    return StreamingResponse(iter_measurement(), media_type="text/event-stream")

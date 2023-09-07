import asyncio
import time
import logging
from typing import Annotated, Callable
from serial import Serial
from fastapi import (
    HTTPException,
    Path,
    Query,
    Body,
    APIRouter,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.routing import APIRoute
from ipaddress import IPv4Address
from .find_port import portname
from .lighting_exception import LightingException
from .lora_helper import __decode__, __encode__
from .data_models import (
    CCOUID,
    STAUID,
    ControlMode,
    Dimming,
    StartupControl,
    IpConfig,
    PowerMeter,
    STAStatus,
)


class BlockingRoute(APIRoute):
    """
    Make lighting router blocking because we are sharing one serial interface
    """

    def __init__(self, *args, **kargs):
        super().__init__(*args, **kargs)
        self.lock = asyncio.Lock()

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            await self.lock.acquire()
            response: Response = await original_route_handler(request)
            self.lock.release()
            return response

        return custom_route_handler


router = APIRouter(prefix="/api/lighting", tags=["lighting"])
router.route_class = BlockingRoute

logger = logging.getLogger(__name__)

# Initialize the serial port
logger.debug("Opening serial port " + portname + "...")
lora = Serial(port=portname, timeout=0.5)
# Flush the serial ports so any existing message in buffer is discarded
lora.flush()
logger.debug("Opening serial port " + portname + " successful")


def send(
    addr: str,
    cmd: str,
    data: str = "",
    data_raw: bytes = b"",
    wait_duration: float = 1.0,
) -> None:
    """Helper function for sending message through LoRa"""
    msg = __encode__(addr, cmd, data, data_raw)
    lora.write(msg)
    logger.debug("Wrote " + str(msg))
    # Wait for the command to apply
    time.sleep(wait_duration)


def receive() -> bytes:
    """Helper function for getting response through LoRa"""
    res = lora.read_until(b"\r\n")
    logger.debug("Read " + str(res))

    busy = [
        e.encode("ascii") for e in ["WHUSEING", "WHBUSY", "TOPOBUSY", "STAGROUPBUSY"]
    ]
    if any([res.startswith(b) for b in busy]):
        raise LightingException("Transmitter busy: " + res.decode("ascii"))

    if res == b"":
        logger.warning("LoRa readline() timeout")
        raise HTTPException(status_code=515, detail="LoRa response timeout")
    return res


def decode(res: bytes | bytearray, cmd: str):
    """Helper function for obtaining data from LoRa response"""
    try:
        (addr, data, _) = __decode__(res, cmd, False)
    except LightingException as e:
        logger.warning("Decode error " + str(e))
        raise HTTPException(status_code=515, detail="Decode error " + str(e))

    return addr, data


@router.get("/list_cco")
async def list_cco():
    """
    List the transmitter serial numbers connected in the LoRa network
    """
    cco_uid = "FFFFFFFF"
    cmd = "CCO_UID"

    send(cco_uid, cmd)
    res = receive()
    (addr, _) = decode(res, cmd)

    return {"address": addr}


@router.get("/{cco_uid}/control_mode")
async def get_control_mode(cco_uid: CCOUID):
    """
    Get the transmitter control mode, 1 means on and 0 means off.
    The sequence is in 0-10V, Button, Modbus/RTU, BACnet/IP and RS232 Debug.
    """
    cmd = "GETTYPE"

    send(cco_uid, cmd)
    res = receive()
    (_, data) = decode(res, cmd)

    analog = data[0] == "1"
    button = data[2] == "1"
    modbus = data[4] == "1"
    bacnet = data[6] == "1"
    debug = data[8] == "1"

    return ControlMode(
        analog=analog, button=button, modbus=modbus, bacnet=bacnet, debug=debug
    )


@router.put("/{cco_uid}/control_mode")
async def set_control_mode(
    cco_uid: CCOUID,
    control_mode: Annotated[
        ControlMode, Body(title="Transmitter external control mode")
    ],
):
    """
    Set the transmitter control mode, true means on and false means off
    The sequence is in 0-10V, Button, Modbus/RTU, BACnet/IP and RS232 Debug.
    """
    cmd = "SETTYPE"
    data_list = [" "] * 5
    data_list[0] = "1" if control_mode.analog else "0"
    # button mode is always enabled
    data_list[1] = "1"
    data_list[2] = "1" if control_mode.modbus else "0"
    data_list[3] = "1" if control_mode.bacnet else "0"
    data_list[4] = "1" if control_mode.debug else "0"
    data = " ".join(data_list)

    send(cco_uid, cmd, data)

    res = receive()

    (_, res_data) = decode(res, cmd)

    if data != res_data:
        logger.warning(
            f"Dimming mode response does not match, expected {data}, got {res_data}"
        )
        raise HTTPException(
            status_code=515, detail="Dimming mode response does not match"
        )


@router.post("/{cco_uid}/reset_control_mode")
async def reset_control_mode(cco_uid: CCOUID):
    """
    Reset the control modes and force the transmitter to use 0-10V dimming.
    The transmitter will follow digital command if receiving digital signals even after this command.
    """
    await set_control_mode(
        cco_uid,
        ControlMode(analog=True, button=True, modbus=True, bacnet=True, debug=True),
    )
    cmd = "RESET10V"
    send(cco_uid, cmd)


@router.put("/{cco_uid}/dim_single/{sta_uid}")
async def dim_single(
    cco_uid: CCOUID,
    sta_uid: STAUID,
    dimming: Annotated[Dimming, Body(title="Three channel dimming level")],
):
    """
    Enable and set the single fixture dimming level
    """
    cmd = "DIMALL3"
    # Special case: single dimming data is in raw hex format instead of ascii
    # Last byte of 0x04 means enabling single dimming
    data = (
        b"".join(
            [d.to_bytes(length=2, byteorder="big") for d in dimming.dimming_levels]
        )
        + bytes.fromhex(sta_uid)
        + int.to_bytes(0x04, length=1, byteorder="big")
    )
    # Special case: there is no ":" separator between cmd and data
    temp = __encode__(cco_uid, cmd).rstrip()
    lora.write(temp + data + b"\n")


@router.post("/{cco_uid}/disable_dim_single/{sta_uid}")
async def disable_dim_single(
    cco_uid: CCOUID,
    sta_uid: STAUID,
    dimming: Annotated[Dimming, Body(title="Three channel dimming level")] = Dimming(
        dimming_levels=[0, 0, 0]
    ),
):
    cmd = "DIMALL3"
    # Single dimming data is in raw hex format instead of ascii
    # Last byte of 0x05 means disabling single dimming
    data = (
        b"".join(
            [d.to_bytes(length=2, byteorder="big") for d in dimming.dimming_levels]
        )
        + bytes.fromhex(sta_uid)
        + int.to_bytes(0x05, length=1, byteorder="big")
    )
    # There is no ":" separator between cmd and data
    temp = __encode__(cco_uid, cmd).rstrip()
    lora.write(temp + data + b"\n")
    time.sleep(0.5)


@router.get("/{cco_uid}/dim_broadcast")
async def get_dim_broadcast(cco_uid: CCOUID):
    """
    Get the transmitter broadcast dimming level
    """
    cmd = "GETDIMS"
    send(cco_uid, cmd)
    res = receive()
    (_, data) = decode(res, cmd)

    dimming_levels = [int(d) for d in data.split()]
    return Dimming(dimming_levels=dimming_levels)


@router.put("/{cco_uid}/dim_broadcast")
async def dim_broadcast(
    cco_uid: CCOUID,
    dimming: Annotated[Dimming, Body(title="Three channel dimming level")],
):
    """
    Set the transmitter broadcast dimming level
    """
    cmd = "SETDIMS"
    data = " ".join([str(d) for d in dimming.dimming_levels]).encode(encoding="ascii")
    # Special case: separator between cmd and data is a single space " "
    temp = __encode__(cco_uid, cmd).rstrip()
    lora.write(temp + b"\x20" + data + b"\n")
    # Special case: no response from SETDIMS command

    time.sleep(0.5)


@router.get("/{cco_uid}/group/{sta_uid}")
async def get_sta_group(cco_uid: CCOUID, sta_uid: STAUID):
    """
    Get the group ID for a single adapter
    """
    cmd = "SET_DEVICE_GROUP"
    # Data field includes the STA serial number, any group id and "00" for group read command
    data = sta_uid + "0" * 12 + "00"
    send(cco_uid, cmd, data)

    # Group setting response takes two messages
    # The first response is just the STA uid
    res = receive()

    cmd = "GROUPS"
    (_, data) = decode(res, cmd)

    if data != sta_uid:
        logger.warning(
            f"STA UID response does not match, expected {data}, got {sta_uid}"
        )
        raise HTTPException(status_code=515, detail="STA UID response does not match")

    # The second response includes both the uid and the group
    res = receive()

    cmd = "SET_DEVICE_GROUP"
    (_, data) = decode(res, cmd)

    if not data.startswith(sta_uid):
        logger.warning(
            f"STA UID response does not match, expected {sta_uid}, got {data}"
        )
        raise HTTPException(status_code=515, detail="STA UID response does not match")

    group = int(data[len(sta_uid) :])
    return {"group_id": group}


@router.put("/{cco_uid}/group/{sta_uid}")
async def set_sta_group(
    cco_uid: CCOUID,
    sta_uid: STAUID,
    group: Annotated[int, Query(title="Group ID", min=1, max=8)],
):
    """
    Set the group ID for a single adapter
    """
    cmd = "SET_DEVICE_GROUP"
    data = sta_uid + str(group).zfill(12) + "01"
    send(cco_uid, cmd, data)

    # Group setting response takes two messages
    # The first response is just the STA uid
    res = receive()

    cmd = "GROUPS"
    (_, data) = decode(res, cmd)

    if data != sta_uid:
        logger.warning(
            f"STA UID response does not match, expected {data}, got {sta_uid}"
        )
        raise HTTPException(status_code=515, detail="STA UID response does not match")

    # The second response includes both the uid and the group
    res = receive()

    cmd = "SET_DEVICE_GROUP"
    (_, data) = decode(res, cmd)

    if not data.startswith(sta_uid):
        logger.warning(
            f"STA UID response does not match, expected {sta_uid}, got {data}"
        )
        raise HTTPException(status_code=515, detail="STA UID response does not match")

    group_res = int(data[len(sta_uid) :])

    if group != group_res:
        logger.warning(
            f"STA group ID does not match, expected {group}, got {group_res}"
        )
        raise HTTPException(status_code=515, detail="STA group ID does not match")


@router.get("/{cco_uid}/groups")
async def get_all_sta_groups(cco_uid: CCOUID):
    """
    Get the group ID for all STA
    """
    cmd = "GETSTAGROUPS"
    send(cco_uid, cmd)

    sta_uids = []
    group_ids = []
    # Continuously read until timeout, indicating end of response
    while (res := lora.readline()) != "":
        cmd = "GROUPS"
        try:
            (_, data) = decode(res, cmd)
        except HTTPException:
            # No STA groups
            return {"sta_uids": "", "group_ids": ""}

        sta_uids.append(data)

        # Continue check each group
        res = receive()

        cmd = "SET_DEVICE_GROUP"
        (_, data) = decode(res, cmd)

        if not data.startswith(sta_uids[-1]):
            logger.warning(
                f"STA UID response does not match, expected {sta_uids[-1]}, got {data}"
            )
            raise HTTPException(
                status_code=515, detail="STA UID response does not match"
            )

        group_id = int(data[len(sta_uids[-1]) :])
        group_ids.append(group_id)

    return {"sta_uids": sta_uids, "group_ids": group_ids}


@router.put("/{cco_uid}/dim_group")
async def dim_group(
    cco_uid: CCOUID,
    group: Annotated[int, Query(title="Group ID", min=1, max=8)],
    dimming: Annotated[Dimming, Body(title="Three channel dimming level")] = Dimming(
        dimming_levels=[50, 50, 0]
    ),
):
    """
    Enable group dimming for the given group ID
    """
    cmd = "DIMALL3"
    # Data format: 5x 2 bytes of dimming levels, 1x 2 bytes of group ID, 1 byte of disable cmd
    data = b"".join([d.to_bytes(2, byteorder="big") for d in dimming.dimming_levels])
    # Last 2x dimming levels are not used
    data += 0x00.to_bytes(4, byteorder="big")
    data += group.to_bytes(2, byteorder="big") + 0x06.to_bytes(1, byteorder="big")
    send(cco_uid, cmd, "", data)


@router.post("/{cco_uid}/disable_group_dimming")
async def disable_group_dimming(
    cco_uid: CCOUID, group: Annotated[int, Query(title="Group ID", min=1, max=8)]
):
    """
    Disable group dimming for the given group ID
    """
    cmd = "DIMALL3"
    # Data format: 5x 2 bytes of dimming levels, 1x 2 bytes of group ID, 1 byte of disable cmd
    data = (
        0x00.to_bytes(10, byteorder="big")
        + group.to_bytes(2, byteorder="big")
        + 0x07.to_bytes(1, byteorder="big")
    )
    send(cco_uid, cmd, "", data)


@router.post("/{cco_uid}/stop_network_waiting")
async def stop_network_waiting(
    cco_uid: CCOUID,
):
    """
    Stop waiting for the transmitter and adapter network reestablish
    """
    cmd = "WAITSTOP"
    send(cco_uid, cmd)


@router.get("/{cco_uid}/tx_power")
async def get_txpower(
    cco_uid: Annotated[
        str, Path(title="Transmitter serial number", min_length=8, max_length=8)
    ]
):
    """
    Obtain the transmitting power setting
    """
    cmd = "GetTxPower"
    send(cco_uid, cmd)

    res = receive()
    (_, data) = decode(res, cmd)

    txpower = int(data)
    return {"txpower": txpower}


@router.put("/{cco_uid}/tx_power")
async def set_txpower(
    cco_uid: CCOUID,
    txpower: Annotated[int, Query(title="Transmitting power", min=0, max=24)],
):
    """
    Set the transmitting power setting
    """
    cmd = "SetTxPower"
    data = str(txpower)
    send(cco_uid, cmd, data)

    res = receive()
    (_, rx_data) = decode(res, cmd)

    if rx_data != data:
        logger.warning(
            f"CCO TX power setting inconsistent, expected {data}, got {rx_data}"
        )
        raise HTTPException(status_code=515, detail="CCO TX power setting inconsistent")


@router.put("/{cco_uid}/access_time")
async def set_access_time(
    cco_uid: CCOUID,
    access_time: Annotated[
        int,
        Query(
            title="Time in minutes for searching adapters after transmitter power cycle",
            min=1,
            max=30,
        ),
    ],
):
    """
    Set the transmitter network discovery duration when autostarting
    """
    cmd = "SetAccessTime"
    data = str(access_time)
    send(cco_uid, cmd, data)

    res = receive()
    (_, rx_data) = decode(res, cmd)

    if rx_data != data:
        logger.warning(
            f"CCO access time setting inconsistent, expected {data}, got {rx_data}"
        )
        raise HTTPException(
            status_code=515, detail="CCO access time setting inconsistent"
        )


@router.get("/{cco_uid}/access_time")
async def get_access_time(cco_uid: CCOUID):
    """
    Get the transmitter network discovery duration when autostarting
    """
    cmd = "GetAccessTime"
    send(cco_uid, cmd)

    res = receive()
    (_, rx_data) = decode(res, cmd)
    access_time = int(rx_data)

    return {"access_time": access_time}


@router.put("/{cco_uid}/band")
async def set_frequency_band(
    cco_uid: CCOUID,
    band: Annotated[int, Query(title="Transmitter frequency band", min=0, max=3)],
):
    """
    Set the PLC communication frequency band
    """
    cmd = "SetCCOBand"
    data = str(band)
    send(cco_uid, cmd, data)

    res = receive()
    (_, rx_data) = decode(res, cmd)

    if rx_data != data:
        logger.warning(
            f"Transmitter band setting inconsistent, expected {data}, got {rx_data}"
        )
        raise HTTPException(
            status_code=515, detail="Transmitter band setting inconsistent"
        )


@router.get("/{cco_uid}/band")
async def get_frequency_band(cco_uid: CCOUID):
    """
    Get the PLC communication frequency band
    """
    cmd = "GetCCOBand"
    send(cco_uid, cmd)

    res = receive()
    (_, rx_data) = decode(res, cmd)
    band = int(rx_data)

    return {"band": band}


@router.put("/{cco_uid}/dim_channel")
async def set_dimming_channel(
    cco_uid: CCOUID,
    channel_count: Annotated[
        int, Query(title="Transmitter dimming channel count", min=1, max=2)
    ],
):
    """
    Set the transmitter number of dimming channels for 0-10V analog input
    When set to 1 channel, the minimum output is 15% and 1st and 2nd channel are set to follow
    When set to 2 channel, the minimum output is 0% and 1st and 2nd channel are set to independent
    """
    cmd = "SetCCOChannel"
    data = str(channel_count)
    send(cco_uid, cmd, data)

    res = receive()
    (_, rx_data) = decode(res, cmd)

    if rx_data != data:
        logger.warning(
            f"Transmitter dimming channel setting inconsistent, expected {data}, got {rx_data}"
        )
        raise HTTPException(
            status_code=515,
            detail="Transmitter dimming channel setting inconsistent",
        )


@router.get("/{cco_uid}/dim_channel")
async def get_dimming_channel(cco_uid: CCOUID):
    """
    Get the transmitter number of analog dimming channel
    """
    cmd = "GetCCOChannel"
    send(cco_uid, cmd)

    res = receive()
    (_, rx_data) = decode(res, cmd)
    channel_count = int(rx_data)

    return {"channel_count": channel_count}


@router.get("/{cco_uid}/startup_control")
async def get_startup_control(cco_uid: CCOUID):
    """
    Get the startup control setting for PLC adapters, ramp_up_duration is not used and meaningless
    """
    cmd = "GetStartContral"
    send(cco_uid, cmd)

    res = receive()
    (_, rx_data) = decode(res, cmd)

    rx_list = rx_data.split(" ")
    is_enabled = rx_list[0] == "1"
    default_dimming = int(rx_list[1])
    # _ = int(rx_list[2])

    startup_control = StartupControl(
        is_enabled=is_enabled,
        default_dimming=default_dimming,
    )

    return startup_control


@router.put("/{cco_uid}/startup_control")
async def set_startup_control(
    cco_uid: CCOUID,
    startup_control: Annotated[
        StartupControl, Body(title="Adapter startup control setting")
    ],
):
    """
    Set the startup control setting for PLC adapters
    """
    cmd = "SetStartContral"
    is_enabled = "1" if startup_control.is_enabled else "0"
    default_dimming = str(startup_control.default_dimming)
    # ramp_up_duration is meaningless but required
    ramp_up_duration = "10"
    data = " ".join([is_enabled, default_dimming, ramp_up_duration])
    send(cco_uid, cmd, data)

    res = receive()
    (_, rx_data) = decode(res, cmd)

    if rx_data != data:
        logger.warning(
            f"Startup control setting inconsistent, expected {data}, got {rx_data}"
        )
        raise HTTPException(
            status_code=515, detail="Startup control setting inconsistent"
        )


@router.put("/{cco_uid}/modbus_rtu_node_address")
async def set_modbus_rtu_node_address(
    cco_uid: CCOUID,
    address: Annotated[int, Query(title="Modbus RTU node address", min=1, max=255)],
):
    """
    Set the modbus node address
    """
    cmd = "SetModbusAddr"
    data = str(address)
    send(cco_uid, cmd, data)

    res = receive()
    (_, rx_data) = decode(res, cmd)

    if rx_data != data:
        logger.warning(
            f"Modbus node address setting inconsistent, expected {data}, got {rx_data}"
        )
        raise HTTPException(
            status_code=515, detail="Modbus node address setting inconsistent"
        )


@router.get("/{cco_uid}/modbus_rtu_node_address")
async def get_modbus_rtu_node_address(cco_uid: CCOUID):
    """
    Get the modbus node address setting
    """
    cmd = "GetModbusAddr"
    send(cco_uid, cmd)

    res = receive()
    (_, rx_data) = decode(res, cmd)
    address = int(rx_data)

    return {"address": address}


@router.put("/{cco_uid}/ip_address")
async def set_ip_address(
    cco_uid: CCOUID,
    ip_config: Annotated[IpConfig, Body(title="IP Address Setting")] = IpConfig(
        dynamic=False,
        address=IPv4Address("127.0.0.100"),
        netmask=IPv4Address("255.255.255.0"),
        gateway=IPv4Address("127.0.0.1"),
    ),
):
    """
    Set the transmitter ethernet IP address
    """
    cmd = "SetDeviceIP"
    if ip_config.dynamic:
        data = "0.0.0.0 0.0.0.0 0.0.0.0 0 0"
    else:
        # Format address netmask gateway separated by a single space, with a trailing 1 means static IP
        data = (
            " ".join(
                [
                    str(ip_config.address),
                    str(ip_config.netmask),
                    str(ip_config.gateway),
                ]
            )
            + " 1 "
        )

        # Checksum is the sum of numbers when using dot notation
        def ip_address_sum(addr: IPv4Address):
            return sum([int(a) for a in str(addr).split(".")])

        checksum = (
            ip_address_sum(ip_config.address)
            + ip_address_sum(ip_config.netmask)
            + ip_address_sum(ip_config.gateway)
            + 1
        )

        data += str(checksum)

    # IP address setting needs a bit longer
    send(cco_uid, cmd, data, b"", 4.0)

    if ip_config.dynamic:
        # Response message is in 1 line
        res = receive()
        (_, rx_data) = decode(res, cmd)

        if rx_data != "OK 0":
            logger.warning(f"Error setting dynamic IP address, response {rx_data}")
            raise HTTPException(
                status_code=515, detail="Error setting dynamic IP address"
            )

    else:
        # Response message is in 4 lines
        # First 3 lines are the static IP address, netmask and gateway
        _ = receive()
        _ = receive()
        _ = receive()
        res = receive()
        (_, rx_data) = decode(res, cmd)

        if rx_data != "OK 1":
            logger.warning(f"Error setting static IP address, response {rx_data}")
            raise HTTPException(
                status_code=515, detail="Error setting static IP address"
            )


@router.get("/{cco_uid}/ip_address")
async def get_ip_address(cco_uid: CCOUID):
    """
    Get the transmitter IP address setting
    """
    cmd = "GetDeviceIP"
    send(cco_uid, cmd)

    res = receive()
    (_, rx_data) = decode(res, cmd)
    fields = rx_data.split(" ")
    ip_addr = IpConfig(
        dynamic=False,
        address=IPv4Address(fields[0]),
        netmask=IPv4Address(fields[1]),
        gateway=IPv4Address(fields[2]),
    )

    return ip_addr


@router.delete("/{cco_uid}/whitelist")
async def clear_whitelist(cco_uid: CCOUID):
    """
    Remove the PLC communication STA whitelist
    """
    cmd = "WHCLR"
    send(cco_uid, cmd)

    # Response does not have data field
    _ = receive()
    # Transmitter PLC module will reload whitelist so wait here
    time.sleep(5)
    lora.flush()


@router.get("/{cco_uid}/whitelist")
async def get_whitelist(cco_uid: CCOUID):
    """
    Get the PLC communication STA whitelist
    """
    cmd = "WHSGET"
    send(cco_uid, cmd)

    sta_list = []

    while True:
        res = receive()
        cmd = "WHMULT"
        (_, data) = decode(res, cmd)
        fields = data.split()

        # 0th field is starting index
        index = int(fields[0])
        # 1st field is number of sta address in this message
        length = int(fields[1])
        # 2rd field is total number of sta address
        total = int(fields[2])

        # The rest are sta address
        sta_list.extend(fields[3:])

        if index + length == total:
            break

    return {"whitelist": sta_list}


@router.post("/{cco_uid}/whitelist")
async def set_whitelist(
    cco_uid: CCOUID,
    sta_list: Annotated[list[STAUID], Body(title="List of STA UIDs")],
):
    """
    Set the PLC communication STA whitelist
    """
    await clear_whitelist(cco_uid)
    cmd = "WHSTART"
    send(cco_uid, cmd)

    res = receive()
    decode(res, cmd)

    # Send 10 STA UIDs at a time
    index = 0
    stop_flag = False
    length = 10
    total = len(sta_list)

    while index + length <= total:
        fields = [str(index), str(length), str(total)]

        if index + 4 > total:
            stop_flag = True
            fields.extend(sta_list[index:])
        else:
            fields.extend(sta_list[index : index + 4])

        cmd = "WHMLIST"
        data = " ".join(fields)
        send(cco_uid, cmd, data)

        res = receive()
        (_, res_data) = decode(res, cmd)

        if data.startswith(res_data):
            index += 4
        else:
            cmd = "WHEND"
            send(cco_uid, cmd)
            logger.warning(f"Unknown failure when setting whitelist")
            raise HTTPException(
                status_code=515, detail="Unknown failure when setting whitelist"
            )

        if stop_flag:
            break

    cmd = "WHEND"
    send(cco_uid, cmd)


@router.post("/{cco_uid}/reboot")
async def reboot(cco_uid: CCOUID):
    """
    Force the transmitter to power cycle
    """
    cmd = "REBOOTCCO"
    send(cco_uid, cmd)
    # Wait until the transmitter reboots
    time.sleep(5)
    # Flush the serial buffer so the boot up message is discarded
    lora.flush()


@router.websocket("/{cco_uid}/network_discovery")
async def rebuild(cco_uid: CCOUID, websocket: WebSocket):
    """
    Force the transmitter to rebuild its whitelist
    """
    await websocket.accept()

    async def start():
        cmd = "REBOOTCCO"
        logger.debug("REBUILD rebooting")
        send(cco_uid, cmd)

        await asyncio.sleep(10)

        cmd = "SETTINGSTART"
        logger.debug("REBUILD start discovery")
        send(cco_uid, cmd)

    async def streaming():
        # There are two messages after the searching is complete
        # The last message is in the following shape
        # ##048BE6E1:PLCB_STA:4 4 559\r\r\n
        stop_cmd_sent = False
        stop_flag = False
        while not stop_flag:
            if res := lora.readlines():
                if stop_cmd_sent:
                    for s in res:
                        try:
                            (_, data, _) = __decode__(s, "PLCB_STA")
                            if data.count(" ") == 2:
                                stop_flag = True
                                break
                        except LightingException:
                            continue

                try:
                    await websocket.send_text(str(res))
                except WebSocketDisconnect:
                    if not stop_cmd_sent:
                        stop_cmd = "WHITESTOP"
                        send(cco_uid, stop_cmd)
                    return

            try:
                ws_res = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                if ws_res == "STOP":
                    stop_cmd = "WHITESTOP"
                    send(cco_uid, stop_cmd)
                    stop_cmd_sent = True
            except TimeoutError:
                pass
            except WebSocketDisconnect:
                if not stop_cmd_sent:
                    stop_cmd = "WHITESTOP"
                    send(cco_uid, stop_cmd)
                return

    async with asyncio.TaskGroup() as tg:
        _ = tg.create_task(start())
        _ = tg.create_task(streaming())


@router.get("/{cco_uid}/status/{sta_uid}")
async def get_status(
    cco_uid: CCOUID,
    sta_uid: Annotated[
        str, Path(title="Adapter serial number", min_length=12, max_length=12)
    ],
):
    """Retrieve the adapter status"""
    cmd = "STATUS"
    data = sta_uid
    send(cco_uid, cmd, data)
    res = receive()
    (_, res_data) = decode(res, cmd)

    if res_data != data:
        logger.warning("Inconsistent STA UID in get_status response")
        raise HTTPException(
            status_code=515, detail="Inconsistent STA UID in get_status response"
        )

    res = receive()
    (_, res_data) = decode(res, "STA")

    # Response data starts with STA UID
    if not res_data.startswith(sta_uid):
        logger.warning("Inconsistent STA UID in get_status response")
        raise HTTPException(status_code=515, detail="Inconsistent STA UID in response")
    # Then firmware version code of STA
    index = len(sta_uid)
    length = 6
    firmware_version = res_data[index : index + length]

    # Then the dimming levels
    index += length
    length = 6

    dimming_level_0 = int(bytes.fromhex(res_data[index + 1] + res_data[index]))
    dimming_level_1 = int(bytes.fromhex(res_data[index + 3] + res_data[index + 2]))
    dimming_level_2 = int(bytes.fromhex(res_data[index + 5] + res_data[index + 4]))

    dimming = Dimming(
        dimming_levels=[dimming_level_0, dimming_level_1, dimming_level_2]
    )

    # Then the dimming control mode:
    # 00 broadcast
    # 04 single dimming enable
    # 05 single dimming disable
    # 06 group dimming enable
    # 07 group dimming disable
    index += length
    length = 1

    if res_data[index] == "00":
        dimming_mode = "broadcast"
    elif res_data[index] == "04":
        dimming_mode = "single enable"
    elif res_data[index] == "05":
        dimming_mode = "single disable"
    elif res_data[index] == "06":
        dimming_mode = "group enable"
    elif res_data[index] == "07":
        dimming_mode = "group disable"
    else:
        dimming_mode = "unknown " + res_data[index]

    index += length

    # Power meter data starts with 0x55
    meter_header = int(res_data[index], base=16)
    index += 1

    # Current RMS value (quick)
    _ = int(res_data[index : index + 3], base=16)
    index += 3
    # Current RMS value
    current_rms = int(res_data[index : index + 3], base=16)
    # Reserved
    index += 3
    # Voltage RMS value
    voltage_rms = int(res_data[index : index + 3], base=16)
    index += 3
    # Reserved
    index += 3
    # Power
    power = int(res_data[index : index + 3], base=16)
    index += 3
    # Pulse count
    pulse_count = int(res_data[index : index + 3], base=16)
    index += 3
    # Reserved
    index += 3
    # Internal temperature measurement
    temp_internal = int(res_data[index : index + 3], base=16)
    index += 3
    # External temperature measurement
    temp_external = int(res_data, base=16)
    index += 3
    # Power meter checksum
    _ = res_data[index]
    index += 1
    # Total checksum
    total_checksum = int(res_data[index], base=16)

    power_meter = PowerMeter(
        header=meter_header,
        irms=current_rms,
        vrms=voltage_rms,
        power=power,
        pulse_count=pulse_count,
        temperature_int=temp_internal,
        temperature_ext=temp_external,
        checksum=total_checksum,
    )
    sta_status = STAStatus(
        serial_number=sta_uid,
        firmware_version=firmware_version,
        dimming=dimming,
        dimming_style=dimming_mode,
        power_meter=power_meter,
    )
    return sta_status

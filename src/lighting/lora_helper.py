from .lighting_exception import LightingException


def __decode__(msg: bytes, cmd: str, raw: bool = False):
    """
    Decode the serial command and return the data field

    Transmitter response message format

    ## + Transmitter address + : + Command + : + Data + CR + CR + LF

    1. Fixed 2 bytes ##
    2. Transmitter address (8 bytes long) such as 048BE6E1
    3. Transmitter address, command and data are joined together with :
    4. There is a carriage return at the end of data field

    If argument `raw` is True, the data field will be returned in bytes instead of string
    """
    msg = bytearray(msg)

    fields = msg.split(b":")

    if len(fields) != 3:
        raise LightingException(f"Expect 3 fields separated by :, got {len(fields)}")

    if not fields[0].startswith(b"##"):
        raise LightingException(
            f"Expect response to start with ##, got {fields[0].decode('ascii')}"
        )

    addr = fields[0][2:].decode("ascii")

    if fields[1] != cmd.encode("ascii"):
        raise LightingException(
            f"Incorrect command. Expect {cmd}, got {str(fields[1])}"
        )

    data_raw = fields[2].rstrip()
    data = "" if raw else data_raw.decode("ascii")

    return addr, data, data_raw


def __encode__(addr: str, cmd: str, data: str = "", data_raw: bytes = b"") -> bytes:
    """
    Encode the serial command to the transmitter command format

    @@ + Transmitter address + : + Command + : + Data(optional) + CR
    """
    msg = "@@" + addr + ":" + cmd
    if data == "" and data_raw == b"":
        return (msg + "\n").encode("ascii")
    elif data == "":
        return (msg + ":").encode("ascii") + data_raw + "\n".encode("ascii")
    elif data_raw == b"":
        return (msg + ":" + data + "\n").encode("ascii")
    else:
        return (msg + ":" + data).encode("ascii") + data_raw + "\n".encode("ascii")

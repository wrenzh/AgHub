from typing import Annotated
from fastapi import Path
from pydantic import BaseModel, Field, AfterValidator
from ipaddress import IPv4Address

CCOUID = Annotated[
    str, Path(title="Transmitter serial number", min_length=8, max_length=8)
]

STAUID = Annotated[
    str, Path(title="Adapter serial number", min_length=12, max_length=12)
]


class ControlMode(BaseModel):
    """Data model for transmitter external control modes"""

    analog: bool
    button: bool
    modbus: bool
    bacnet: bool
    debug: bool


def dimming_range(v: int) -> int:
    assert v >= 0 and v <= 1000
    return v


DimmingLevel = Annotated[int, AfterValidator(dimming_range)]


class Dimming(BaseModel):
    """
    Data model for 3 channel dimming signals
    Dimming values are x10, so 50% dimming is 500
    """

    dimming_levels: list[DimmingLevel] = Field(None, min_length=3, max_length=3)


class StartupControl(BaseModel):
    """
    Data model for startup control, ramp_up_duration feature is removed
    Note default_dimming is x1 instead of x10, so 50% default dimming is 50
    """

    is_enabled: bool
    default_dimming: int = Field(None, ge=0, le=100)


class IpConfig(BaseModel):
    """
    Data model for IP configuration, including whether DHCP or manual, and if manual
    IP address, netmask and gateway
    """

    dynamic: bool
    address: IPv4Address
    netmask: IPv4Address
    gateway: IPv4Address


class PowerMeter(BaseModel):
    """
    Data model for power meter
    """

    header: int = Field(None, ge=0x55, le=0x55)
    irms: int
    vrms: int
    power: int
    pulse_count: int
    temperature_int: int
    temperature_ext: int
    checksum: int


class STAStatus(BaseModel):
    """
    Data model for STA status report
    """

    serial_number: str = Field(min_length=12, max_length=12)
    firmware_version: str = Field(min_length=6, max_length=6)
    dimming: Dimming
    dimming_style: str
    power_meter: PowerMeter

from typing import Any, Literal

from pydantic import BaseModel, Field


CommandOp = Literal[
    "ping",
    "arm",
    "disarm",
    "motor_test",
    "servo_test",
    "stop_test",
    "params.read",
    "params.write",
    "camera.snapshot",
    "camera.params.read",
    "camera.params.write",
    "dock.status",
    "dock.command",
]


class CommandRequest(BaseModel):
    op: CommandOp
    params: dict[str, Any] = Field(default_factory=dict)


class ParamWriteRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class FlashRequest(BaseModel):
    artifact_id: str = Field(min_length=8, max_length=80)


class AircraftRegisterRequest(BaseModel):
    aircraft_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    name: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=80)
    static_ip: str = Field(default="")


class ClaimCreateRequest(BaseModel):
    aircraft_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    ttl_h: float | None = None


class ClaimRedeemRequest(BaseModel):
    code: str = Field(min_length=6, max_length=64)
    hw: dict[str, str] = Field(default_factory=dict)

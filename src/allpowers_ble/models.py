from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class AllpowersState:
    """State model for Allpowers devices."""

    ac_on: bool = False
    dc_on: bool = False
    close_ble: bool = False
    f60hz_on: bool = False
    beep_on: bool = False
    light_on: bool = False
    screen_on: bool = False
    voice_on: bool = False
    percent_remain: int = 0
    minutes_remain: int = 0
    watts_import: int = 0
    watts_export: int = 0
    ecoMode_on: bool = False
    chargingMode: int = 0
    acMode_on: bool = False
    carPortEn_on: bool = False
    ecoTime: int = 0
    chargeTime: int = 0

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from typing import Any, TypeVar

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakDBusError, BleakError
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
    retry_bluetooth_connection_error,
)

from .models import AllpowersState

CHARACTERISTIC_NOTIFY = "0000FFF1-0000-1000-8000-00805F9B34FB"
CHARACTERISTIC_WRITE = "0000FFF2-0000-1000-8000-00805F9B34FB"
BLEAK_BACKOFF_TIME = 0.25

__version__ = "0.0.0"


WrapFuncType = TypeVar("WrapFuncType", bound=Callable[..., Any])

RETRY_BACKOFF_EXCEPTIONS = (BleakDBusError,)

_LOGGER = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = sys.maxsize


class CharacteristicMissingError(Exception):
    """Raised when a characteristic is missing."""


class AllpowersBLE:
    """Allpowers BLE interface."""

    def __init__(
        self,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData | None = None,
    ) -> None:
#        _LOGGER.setLevel(logging.DEBUG)
        """Init the Allpowers BLE."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        self._state = AllpowersState()
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self.loop = asyncio.get_running_loop()
        self._callbacks: list[Callable[[AllpowersState], None]] = []
        self._disconnected_callbacks: list[Callable[[], None]] = []
        self._buf = b""

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Set the ble device."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data

    @property
    def address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def _address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Get the name of the device."""
        return self._ble_device.name or self._ble_device.address

    @property
    def rssi(self) -> int | None:
        """Get the rssi of the device."""
        if self._advertisement_data:
            return self._advertisement_data.rssi
        return None

    @property
    def state(self) -> AllpowersState:
        """Return the state."""
        return self._state

    @property
    def ac_on(self) -> bool:
        """Return the state of AC."""
        return self._state.ac_on

    @property
    def dc_on(self) -> bool:
        """Return the state of DC."""
        return self._state.dc_on

    @property
    def f60hz_on(self) -> bool:
        """Return the state of DC."""
        return self._state.f60hz_on

    @property
    def beep_on(self) -> bool:
        """Return the state of DC."""
        return self._state.beep_on

    @property
    def light_on(self) -> bool:
        """Return the state of Light."""
        return self._state.light_on

    @property
    def screen_on(self) -> bool:
        """Return the state of Light."""
        return self._state.screen_on

    @property
    def voice_on(self) -> bool:
        """Return the state of Light."""
        return self._state.voice_on

    @property
    def close_ble(self) -> bool:
        """Return the state of Light."""
        return self._state.close_ble

    @property
    def ecoMode_on(self) -> bool:
        """Return the state of Light."""
        return self._state.ecoMode_on

    @property
    def chargingMode(self) -> int:
        """Return the state of Light."""
        return self._state.chargingMode

    @property
    def acMode_on(self) -> bool:
        """Return the state of Light."""
        return self._state.acMode_on

    @property
    def carPortEn_on(self) -> bool:
        """Return the state of Light."""
        return self._state.carPortEn_on

    @property
    def ecoTime(self) -> int:
        """Return the state of Light."""
        return self._state.ecoTime

    @property
    def chargeTime(self) -> int:
        """Return the state of Light."""
        return self._state.chargeTime

    @property
    def percent_remain(self) -> int:
        """Return percentage battery remaining."""
        return self._state.percent_remain

    @property
    def minutes_remain(self) -> int:
        """Return minutes of battery remaining."""
        return self._state.minutes_remain

    @property
    def watts_import(self) -> int:
        """Return incoming power in watts."""
        return self._state.watts_import

    @property
    def watts_export(self) -> int:
        """Return outgoing power in watts."""
        return self._state.watts_export

    def crc(self,data):
        if len(data) < 2:
            raise ValueError("The array must have 2 or more elements")
    
        a = data[0]
        for s in range(1, len(data) - 1):
            a ^= data[s]
        return a

    async def _change_status_to_device(self) -> None:
        """Send the current state back to the device."""
        full = bytes.fromhex("a56500b10101000071")
        s = bytearray(9)
        for x in range(9):
            s[x] = full[x]

        s[7] = 0
        # When writing, the order of bits is not the same as when reading
        #Commented options do nothing on AllPowers R600 V2
        s[7] = s[7] ^ (1 << 0) if self.dc_on     else s[7] & ~(1 << 0)
        s[7] = s[7] ^ (1 << 1) if self.ac_on     else s[7] & ~(1 << 1)
        #s[7] = s[7] ^ (1 << 2) if self.close_ble else s[7] & ~(1 << 2)
        s[7] = s[7] ^ (1 << 3) if self.f60hz_on  else s[7] & ~(1 << 3)
        #s[7] = s[7] ^ (1 << 4) if self.beep_on   else s[7] & ~(1 << 4)
        s[7] = s[7] ^ (1 << 5) if self.light_on  else s[7] & ~(1 << 5)
        #s[7] = s[7] ^ (1 << 6) if self.screen_on else s[7] & ~(1 << 6) 
        #s[7] = s[7] ^ (1 << 7) if self.voice_on  else s[7] & ~(1 << 7)

        # Calculate checksum
        s[8] = 0
        s[8] = self.crc(s)
        
        if self._client is not None:
            await self._client.write_gatt_char(CHARACTERISTIC_WRITE, s)

    async def _change_status_to_device2(self) -> None:
        full = bytes.fromhex("a56500b1010202000171")
        s = bytearray(10)
        for x in range(10):
            s[x] = full[x]

        s[7] = 0
        #Commented options do nothing on AllPowers R600 V2
        s[7] = s[7] ^ (1 << 0) if self.ecoMode_on else s[7] & ~(1 << 0)
        s[7] = (s[7] & ~0b110) | (self.chargingMode << 1)
        #s[7] = s[7] ^ (1 << 3) if self.acMode_on else s[7] & ~(3 << 0)
        #s[7] = s[7] ^ (1 << 4) if self.carPortEn_on else s[7] & ~(4 << 0)
        s[8] = self.ecoTime
        
        # Calculate checksum
        s[9]=0
        s[9]=self.crc(s)

        if self._client is not None:
            await self._client.write_gatt_char(CHARACTERISTIC_WRITE, s)

    async def set_torch(self, enabled: bool) -> None:
        """Set the current value of the light."""
        self._state.light_on = enabled
        await self._change_status_to_device()

    async def set_ac(self, enabled: bool) -> None:
        """Set the current value of the AC."""
        self._state.ac_on = enabled
        await self._change_status_to_device()

    async def set_dc(self, enabled: bool) -> None:
        """Set the current value of the DC."""
        self._state.dc_on = enabled
        await self._change_status_to_device()

    async def set_f60hz(self, enabled: bool) -> None:
        """Set the current value of frequency."""
        self._state.f60hz_on = enabled
        await self._change_status_to_device()

    async def set_beep(self, enabled: bool) -> None:
        """Set the current value of beep."""
        self._state.beep_on = enabled
        await self._change_status_to_device()

    async def set_screen(self, enabled: bool) -> None:
        """Set the current value of the screen."""
        self._state.screen_on = enabled
        await self._change_status_to_device()

    async def set_voice(self, enabled: bool) -> None:
        """Set the current value of the voice."""
        self._state.voice_on = enabled
        await self._change_status_to_device()

    async def set_close_ble(self, enabled: bool) -> None:
        """Set the current value of the Bluetoth?"""
        self._state.close_ble = enabled
        await self._change_status_to_device()

    async def set_ecoMode_on(self, enabled: bool) -> None:
        """Set the current value of the Bluetoth?"""
        self._state.ecoMode_on = enabled
        await self._change_status_to_device2()

    async def set_ecoTime(self, newEcoTime: int) -> None:
        """Set the current value of ecoTime"""
        #Only certain values are allowed
        allowedValues=[1,2,4,6]
        res=self.verifyPositiveValueOnList(newEcoTime,allowedValues)
        if res!=-1:
            self._state.ecoTime = newEcoTime
            await self._change_status_to_device2()
        else:
            _LOGGER.error("Invalid ecoTime. It must be one of %s. Ignoring command",allowedValues)

    async def set_chargingMode(self, newChargingMode: int) -> None:
        """Set the current value of ecoTime"""
        #Only certain values are allowed
        allowedValues=[0,1,2]
        res=self.verifyPositiveValueOnList(newChargingMode,allowedValues)
        if res!=-1:
            self._state.chargingMode = newChargingMode
            await self._change_status_to_device2()
        else:
            _LOGGER.error("Invalid chargingMode. It must be one of %s. Ignoring command",allowedValues)

    async def set_acMode_on(self, enabled: bool) -> None:
        """Set the current value of the Bluetoth?"""
        self._state.acMode_on = enabled
        await self._change_status_to_device2()

    async def set_carPortEn_on(self, enabled: bool) -> None:
        """Set the current value of the Bluetoth?"""
        self._state.carPortEn_on = enabled
        await self._change_status_to_device2()

    def verifyPositiveValueOnList(self,value: int, allowedValues) -> int:
        res=-1
        for item in allowedValues:
            if item==value:
                res = value
        return res

    async def stop(self) -> None:
        """Stop the Allpowers BLE."""
        _LOGGER.debug("%s: Stop", self.name)
        await self._execute_disconnect()

    def _fire_callbacks(self) -> None:
        """Fire the callbacks."""
        for callback in self._callbacks:
            callback(self._state)

    def register_callback(
        self, callback: Callable[[AllpowersState], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""

        def unregister_callback() -> None:
            self._callbacks.remove(callback)

        self._callbacks.append(callback)
        return unregister_callback

    def _fire_disconnected_callbacks(self) -> None:
        """Fire the callbacks."""
        for callback in self._disconnected_callbacks:
            callback()

    def register_disconnected_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""

        def unregister_callback() -> None:
            self._disconnected_callbacks.remove(callback)

        self._disconnected_callbacks.append(callback)
        return unregister_callback

    async def initialise(self) -> None:
        """Initialize the device."""
        _LOGGER.debug("%s: Sending configuration commands", self.name)
        await self._ensure_connected()

        _LOGGER.debug("%s: Subscribe to notifications; RSSI: %s", self.name, self.rssi)
        if self._client is not None:
            await self._client.start_notify(
                CHARACTERISTIC_NOTIFY, self._notification_handler
            )

    async def _ensure_connected(self) -> None:
        """Ensure connection to device is established."""
        if self._connect_lock.locked():
            _LOGGER.debug(
                "%s: Connection already in progress, "
                + "waiting for it to complete; RSSI: %s",
                self.name,
                self.rssi,
            )
        if self._client and self._client.is_connected:
            return
        async with self._connect_lock:
            # Check again while holding the lock
            if self._client and self._client.is_connected:
                return
            _LOGGER.debug("%s: Connecting; RSSI: %s", self.name, self.rssi)
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.name,
                self._disconnected,
                use_services_cache=True,
                ble_device_callback=lambda: self._ble_device,
            )
            _LOGGER.debug("%s: Connected; RSSI: %s", self.name, self.rssi)

            self._client = client

    async def _reconnect(self) -> None:
        """Attempt a reconnect."""
        _LOGGER.debug("ensuring connection")
        try:
            await self._ensure_connected()
            _LOGGER.debug("ensured connection - initialising")
            await self.initialise()
        except BleakNotFoundError:
            _LOGGER.debug("failed to ensure connection - backing off")
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug("reconnecting again")
            self.reconnect_task = asyncio.create_task(self._reconnect())

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle notification responses."""
        _LOGGER.debug("%s: Notification received: %s", self.name, data.hex())
        
        if (len(data)==16):
            self._buf += data

            battery_percentage = data[8]
            dc_on     = data[7] >> 0 & 1 == 1
            ac_on     = data[7] >> 1 & 1 == 1
            f60hz_on  = data[7] >> 2 & 1 == 1
            beep_on   = data[7] >> 3 & 1 == 1
            torch_on  = data[7] >> 4 & 1 == 1
            screen_on = data[7] >> 5 & 1 == 1
            voice_on  = data[7] >> 6 & 1 == 1
            output_power = (256 * data[11]) + data[12]
            input_power = (256 * data[9]) + data[10]
            minutes_remaining = (256 * data[13]) + data[14]
            
            self._state = AllpowersState(
                ac_on=ac_on,
                dc_on=dc_on,
                f60hz_on=f60hz_on,
                beep_on=beep_on,
                light_on=torch_on,
                screen_on=screen_on,
                voice_on=voice_on,
                percent_remain=battery_percentage,
                minutes_remain=minutes_remaining,
                watts_export=output_power,
                watts_import=input_power,
                ecoMode_on=self.ecoMode_on,
                chargingMode=self.chargingMode,
                acMode_on=self.acMode_on,
                carPortEn_on=self.carPortEn_on,
                ecoTime=self.ecoTime,
                chargeTime=self.chargeTime
            )

        else:
            if (len(data)==14):
                self._buf += data
                ecoTime = data[8]
                ecoMode_on  = data[7] >> 0 & 1 == 1
                # chargingMode: 0->Mute; 1->Standard; 2->Fast
                chargingMode= (data[7] & 0b110) >> 1
                acMode_on   = data[7] >> 3 & 1 == 1
                carPortEn_on= data[7] >> 4 & 1 == 1
                chargeTime = (256 * data[9]) + data[10]

                self._state = AllpowersState(
                    ac_on=self.ac_on,
                    dc_on=self.dc_on,
                    f60hz_on=self.f60hz_on,
                    beep_on=self.beep_on,
                    light_on=self.light_on,
                    screen_on=self.screen_on,
                    voice_on=self.voice_on,
                    percent_remain=self.percent_remain,
                    minutes_remain=self.minutes_remain,
                    watts_export=self.watts_export,
                    watts_import=self.watts_import,
                    ecoMode_on=ecoMode_on,
                    chargingMode=chargingMode,
                    acMode_on=acMode_on,
                    carPortEn_on=carPortEn_on,
                    ecoTime=ecoTime,
                    chargeTime=chargeTime
                )

            else:
                _LOGGER.debug("%s: Invalid size Notification received: %s", self.name, len(data))
                
        if (len(data)==16 or len(data)==14):
            self._fire_callbacks()

            _LOGGER.debug(
                "%s: Notification received; RSSI: %s: %s %s",
                self.name,
                self.rssi,
                data.hex(),
                self._state,
            )


    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Disconnected callback."""
        self._fire_disconnected_callbacks()
        if self._expected_disconnect:
            _LOGGER.debug(
                "%s: Disconnected from device; RSSI: %s", self.name, self.rssi
            )
            return
        _LOGGER.warning(
            "%s: Device unexpectedly disconnected; RSSI: %s",
            self.name,
            self.rssi,
        )
        self.reconnect_task = asyncio.create_task(self._reconnect())

    def _disconnect(self) -> None:
        """Disconnect from device."""
        self.disconnect_task = asyncio.create_task(self._execute_timed_disconnect())

    async def _execute_timed_disconnect(self) -> None:
        """Execute timed disconnection."""
        _LOGGER.debug(
            "%s: Disconnecting",
            self.name,
        )
        await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            client = self._client
            self._expected_disconnect = True
            self._client = None
            if client and client.is_connected:
                await client.stop_notify(CHARACTERISTIC_NOTIFY)
                await client.disconnect()

    @retry_bluetooth_connection_error(DEFAULT_ATTEMPTS)
    async def _send_command_locked(self, commands: list[bytes]) -> None:
        """Send command to device and read response."""
        try:
            await self._execute_command_locked(commands)
        except BleakDBusError as ex:
            # Disconnect so we can reset state and try again
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug(
                "%s: RSSI: %s; Backing off %ss; Disconnecting due to error: %s",
                self.name,
                self.rssi,
                BLEAK_BACKOFF_TIME,
                ex,
            )
            await self._execute_disconnect()
            raise
        except BleakError as ex:
            # Disconnect so we can reset state and try again
            _LOGGER.debug(
                "%s: RSSI: %s; Disconnecting due to error: %s", self.name, self.rssi, ex
            )
            await self._execute_disconnect()
            raise

    async def _send_command(
        self, commands: list[bytes] | bytes, retry: int | None = None
    ) -> None:
        """Send command to device and read response."""
        await self._ensure_connected()
        if not isinstance(commands, list):
            commands = [commands]
        await self._send_command_while_connected(commands, retry)

    async def _send_command_while_connected(
        self, commands: list[bytes], retry: int | None = None
    ) -> None:
        """Send command to device and read response."""
        _LOGGER.debug(
            "%s: Sending commands %s",
            self.name,
            [command.hex() for command in commands],
        )
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation already in progress,"
                + "waiting for it to complete; RSSI: %s",
                self.name,
                self.rssi,
            )
        async with self._operation_lock:
            try:
                await self._send_command_locked(commands)
                return
            except BleakNotFoundError:
                _LOGGER.error(
                    "%s: device not found, no longer in range," + "or poor RSSI: %s",
                    self.name,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except CharacteristicMissingError as ex:
                _LOGGER.debug(
                    "%s: characteristic missing: %s; RSSI: %s",
                    self.name,
                    ex,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.debug("%s: communication failed", self.name, exc_info=True)
                raise

        raise RuntimeError("Unreachable")

    async def _execute_command_locked(self, commands: list[bytes]) -> None:
        """Execute command and read response."""
        if self._client is not None:
            for command in commands:
                await self._client.write_gatt_char(CHARACTERISTIC_WRITE, command, False)

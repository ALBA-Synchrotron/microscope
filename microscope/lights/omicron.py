#!/usr/bin/env python3

## Copyright (C) 2020 David Miguel Susano Pinto <carandraug@gmail.com>
## Copyright (C) 2020 Mick Phillips <mick.phillips@gmail.com>
##
## This file is part of Microscope.
##
## Microscope is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## Microscope is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Microscope.  If not, see <http://www.gnu.org/licenses/>.

import logging
from enum import Enum, auto
import serial
import time
import threading
from functools import partial
import microscope._utils
import microscope.abc
from microscope import TriggerType
from microscope import TriggerMode

_logger = logging.getLogger(__name__)


def bit_enabled(code: bytes, pos: int) -> bool:
    return (int(code, 16) & (0x01 << pos)) != 0


class Status:
    def __init__(self, bytes) -> None:
        self._bytes = bytes
        """
        This bit indicates whetherany preceded or pending error prevents the 
        devicefrom starting into normal operation. Only if this bit is unset 
        the laser/ledwill operate as expected. Please refer to the 
        “error handling” chapter for details.
        """
        self.error = bit_enabled(bytes, 0)

        """
        If the bit is set the laser/led is switched on and the working hours
        are counting. Note: there are some other dependencies that may prevent
        the laser/led from emitting light. Please refer to the “Best practices”
        chapter for details.
        """
        self.on = bit_enabled(bytes, 1)

        """
        This bit indicates if the device is actually preheating. This is a 
        temporary state. If the laser/led is already switched on, the 
        “Laser-ON”/“Led-ON” bit will be signaled beside the “preheating” bit 
        but the laser/led will not emit light during this situation. Immediately
        after the diode temperature has reached the valid range the laser/led 
        will start into operation.
        """
        self.preheating = bit_enabled(bytes, 2)

        """
        This bit is signalized if a situation occurred that needs special 
        attention.LedHUBcontroller:one or more channels are in interlock state.
        In this situation the functionality of the LedHUB is restricted, but it
        still may be operated with the remaining wavelengths. QuixX:The bit is
        set if the laser is in pulse mode and triggered by the external digital
        input, but the externally applied frequency is far from the given set
        point. (see QuixX manual for details).
        For all other devices this bit is reserved
        """
        self.attention_required = bit_enabled(bytes, 4)

        """
        This bit represents the state of the laser-enable/led-enable input pin 
        at the control-port.Note: if the laser-enable/led-enable input is not
        connected it will stay active and the bit is set.LedHUB: the bit is 
        set if “shutter” on the front panelis set to “open”
        """
        self.enabled_pin = bit_enabled(bytes, 6)

        """
        This bit represents the state of the key-switch input pin at the 
        control-port.
        """
        self.key_switch = bit_enabled(bytes, 7)

        """
        This bit relies to laserCDRH operation only. If the bit is set,a
        key-switch toggle is needed to release laser operation.
        """
        self.toggle_key = bit_enabled(bytes, 8)

        """
        If the bit is set,the laser/ledsystem is powered-up. This will happen
        automatically if the laser/led is in auto power-up mode (default state).
        """
        self.system_power = bit_enabled(bytes, 9)

        """
        This bit is set, if an external light sensor is connected to the device.
        (LedHUB controller only)
        """
        self.external_sensorconnectionected = bit_enabled(bytes, 13)

    def __repr__(self) -> str:
        return """{{
            "bytes": {},
            "error": {},
            "on": {},
            "preheating": {},
            "attention_required": {},
            "enabled_pin": {},
            "key_switch": {},
            "toggle_key": {},
            "system_power": {},
            "external_sensorconnectionected": {}
        }}""".format(
            self._bytes,
            self.error,
            self.on,
            self.preheating,
            self.attention_required,
            self.enabled_pin,
            self.key_switch,
            self.toggle_key,
            self.system_power,
            self.external_sensorconnectionected,
        )


class LatchedFailure:
    def __init__(self, bytes) -> None:
        """
        This bit indicates that the laser system is in internal error state
        (safety lockout). This bit is signalized in the “Get Actual Status”
        value (bit 0), too.
        """
        self.error_state = bit_enabled(bytes, 0)

        """
        This error bit may be signaled in two situations:
        - a laser is configured as CDHR compliant but no CDRH-kit is connected 
          to the laser.
        - a laser is not configured as CDHR compliant (OEM) but a CDRH-kit is 
          connected.
        """
        self.CDRH = bit_enabled(bytes, 4)

        """
        A controller<->headcommunication error occurred.With PhoxX lasers this
        mostly indicates that the laserhead is not connected correctly to the
        controller or the cable is defect.Otherwise this indicates serious 
        electronic problems.
        """
        self.internal_comunication_error = bit_enabled(bytes, 5)

        """
        An internal error occurred(the K1 relay did not operate).This indicates 
        serious electronic problems
        """
        self.k1_relay_error = bit_enabled(bytes, 6)

        """
        (Possible with PhoxX lasers only) some diodes of PhoxX lasers need a
        specially signed “high power controller”.If you own high and low power 
        PhoxX lasers and do mix up the controllers this bit will indicate that
        the actual connected low power controller is not suitable to drive the 
        connected high power laser head.
        """
        self.high_power = bit_enabled(bytes, 7)

        """
        an under voltage or overvoltage occurred.(is still pending ifbit is set 
        in “Get Failure Byte” command)
        """
        self.under_over_voltage = bit_enabled(bytes, 8)

        """
        The external interlock loop was open.(it is still open if this bit is
        set in “Get Failure Byte” command)Note:if the “Auto Reset” function is 
        active this will also be signalized in the “Latched Failure”as long the
        interlock loop is still open, since the device will automatically reset
        itself after the interlock is closed again.
        """
        self.external_interlock = bit_enabled(bytes, 9)

        """
        The diode currentexceeded the maximum allowed value.
        """
        self.diode_current = bit_enabled(bytes, 10)

        """
        The ambient temperaturein the laser head exceededthe valid temperature 
        range. (still exceeds if bit is set in “Get Failure Byte” command)
        """
        self.ambient_temp = bit_enabled(bytes, 11)

        """
        The diode temperatureexceededthe valid temperature range.(still exceeds 
        if bit is set in “Get Failure Byte” command)
        """
        self.diode_temp = bit_enabled(bytes, 12)

        """
        The test error was triggered.This test error can be triggered by 
        sending “?TIS” (test interlock state).
        """
        self.test_error = bit_enabled(bytes, 13)

        """
        An internal error occurred. This indicates serious electronic problems.
        """
        self.internal_error = bit_enabled(bytes, 14)

        """
        The diode power exceeded the maximum allowed value.
        """
        self.diode_power = bit_enabled(bytes, 15)

    def __repr__(self) -> str:
        return """{{
            "error_state": {},
            "CDRH_error": {},
            "internal_communication_error": {},
            "k1_relay_error": {},
            "high_power": {},
            "under_over_voltage": {},
            "external_interlock": {},
            "diode_current": {},
            "ambient_temp": {},
            "diode_temp": {},
            "test_error": {},
            "internal_error": {},
            "diode_power": {}
        }}""".format(
            self.error_state,
            self.CDRH,
            self.internal_comunication_error,
            self.k1_relay_error,
            self.high_power,
            self.under_over_voltage,
            self.external_interlock,
            self.diode_current,
            self.ambient_temp,
            self.diode_temp,
            self.test_error,
            self.internal_error,
            self.diode_power,
        )


class OperationMode:
    def __init__(self, bytes) -> None:
        self.internal_clock_generator = bit_enabled(bytes, 2)
        self.bias_level_release = bit_enabled(bytes, 3)
        self.operating_level_release = bit_enabled(bytes, 4)
        self.digital_input_release = bit_enabled(bytes, 5)
        self.analog_input_release = bit_enabled(bytes, 7)

        self.APC_mode = bit_enabled(bytes, 8)
        self.digital_input_impedance = bit_enabled(bytes, 11)
        self.analog_input_impedance = bit_enabled(bytes, 12)
        self.usb_adhoc_mode = bit_enabled(bytes, 13)
        self.auto_startup = bit_enabled(bytes, 14)
        self.auto_powerup = bit_enabled(bytes, 15)

    def __repr__(self) -> str:
        return """{{ "hex": "{}", "bin": "{}", 
            "internal_clock_generator": {},
            "bias_level_release": {},
            "operating_level_release": {},
            "digital_input_release": {},
            "analog_input_release": {},
            "APC_mode": {},
            "digital_input_impedance": {},
            "analog_input_impedance": {},
            "usb_adhoc_mode": {},
            "auto_startup": {},
            "auto_powerup": {}
        }}""".format(
            hex(int(self)),
            bin(int(self)),
            self.internal_clock_generator,
            self.bias_level_release,
            self.operating_level_release,
            self.digital_input_release,
            self.analog_input_release,
            self.APC_mode,
            self.digital_input_impedance,
            self.analog_input_impedance,
            self.usb_adhoc_mode,
            self.auto_startup,
            self.auto_powerup,
        )

    def __int__(self) -> int:
        return (
            self.internal_clock_generator << 2
            | self.bias_level_release << 3
            | self.operating_level_release << 4
            | self.digital_input_release << 5
            | self.analog_input_release << 7
            | self.APC_mode << 8
            | self.digital_input_impedance << 10
            | self.analog_input_impedance << 12
            | self.usb_adhoc_mode << 13
            | self.auto_startup << 14
            | self.auto_powerup << 15
        )

    def __bytes__(self) -> bytes:
        return hex(self.__int__())[2:].encode("Latin1")


class CalibrationResult(Enum):
    SUCCESS = 0
    MAX_POWER_UNREACHABLE = 1
    KEY_SWITCH_OFF = 2
    LASER_ENABLE_INPUT_LOW = 3
    INTERLOCK_OCURRED = 4
    DIODE_TEMP_ERROR = 5
    CONTROLLER_HEAD_COMM_ERROR = 6
    BIAS_OUT_OF_RANGE = 7
    NO_BIAS_POINT = 8
    LESS_THAN_PREV_95 = 9
    LASER_SWITCHED_OFF = 10
    NO_CALIBRATION_SENSOR = 11
    NO_LIGHT_DETECTED = 12
    OVER_POWER_OCURRED = 13
    UNKNOWN_ERROR = 14

class LaserMode(Enum):
    APC = auto()
    ACC = auto()

class OmicronLaser(
    microscope.abc.SerialDeviceMixin,
    microscope.abc.LightSource,
):
    """Omicron lasers.
    """

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _ask(self, question: bytes) -> str:
        raw = self._query(question + b"|")
        return raw[:-1].decode("Latin1")[4:].split("|")

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _ask_bytes(self, question: bytes) -> bytes:
        raw = self._query(question + b"|")
        return raw[4:-1]

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _set(self, what: bytes, value: bytes) -> str:
        return self._ask(what + value)

    @microscope.abc.SerialDeviceMixin.lock_comms
    def reset(self) -> bool:
        response = self._query(b"RsC")
        recv = response == b"!RsC\r"
        logging.info("Reset command received. Laser reponse: {}".format(recv))

        if recv:
            response = self.connection.read_until(b"\r")
            while response != b"\x00$RsC>\r":
                response += self.connection.read_until(b"\r")
                logging.info("Reset in course, Laser response: {}".format(response))
            return True

        return False

    def _process_adhoc(self, raw):
        # CMD: GWH, MDP, MTD, MTA, MTB, GAS, GFB, GLP, GPP,
        #      TPP, GOM, COM, GPF, LPF, GDC, RsC, CLD,
        decoded = raw[:-1].decode("Latin1")
        command = decoded[:4]
        content = decoded[4:].split("|")
        if command == "$GAS":
            self.status = Status(content[0])
        elif command == "$GOM":
            self.operation_mode = OperationMode(content[0])
        elif command == "$TPP":
            self.temporal_power = float(content[0])
        elif command == "$MDP":
            self._laser_power = float(content[0])
        else:
            _logger.debug("AdHoc meesage: %s" % raw)

    def get_maximum_power(self):
        return float(self._ask(b"GMP")[0])

    def __init__(self, com=None, baud=500000, timeout=0.01, **kwargs):
        super().__init__(**kwargs)
        self.connection = serial.serial_for_url("COM4", baudrate=baud)
        self.connection.read_all()
        self._do_disable()

        firmware = self._ask(b"GFw")
        self.model_code = firmware[0]
        self.device_id = firmware[1]
        self.firmware_version = firmware[2]

        self.serial_number = self._ask(b"GSN")[0]

        self.specs = self._ask(b"GSI")
        self.wavelength = float(self.specs[0])
        self._max_power_mw = self.get_maximum_power()
        self.exposure = 100 # ms
        self.mode = LaserMode.APC
        self._trigger_type = TriggerType.SOFTWARE
        self.standby = True

        _logger.info(
            f"Omicron Laser Model: {self.model_code}, Id: {self.device_id}, Firmware: {self.firmware_version}."
        )
        _logger.info(f"\tSerial Number: {self.serial_number}")
        _logger.info(f"\tWavelength: {self.wavelength}")
        _logger.info(f"\tPower: {self._max_power_mw}")

        self.add_setting(
            "Standby",
            "bool",
            lambda: self.standby,
            self.set_standby,
            None,
        )

        self.add_setting(
            "Trigger",
            "enum",
            lambda: self._trigger_type,
            partial(self.set_trigger, tmode=TriggerMode.ONCE),
            TriggerType,
        )

        self.initialize()

    def _query(self, q: bytes) -> bytes:
        self.connection.write(b"?" + q + b"\r")
        raw = self._read()
        if raw.decode()[1:4] == q.decode()[:3]:
            # _logger.debug("%s: %s" % (q, raw))
            pass
        else:
            _logger.warning("Unexpected answer to %s: %s" % (q, raw))
            raw = self._query(q)
        return raw

    def _read(self) -> bytes:
        """
        This method reads the connection and manage ad_hoc messages
        """
        raw = self.connection.read_until(b"\r")
        if raw.decode()[0] == "$":  # Ad-hoc message
            self._process_adhoc(raw)
            raw = self._read()
        return raw

    def get_status(self) -> Status:
        code = self._ask_bytes(b"GAS")
        self.status = Status(code)
        return self.status

    def get_latched_failure(self) -> LatchedFailure:
        code = self._ask_bytes(b"GLF")
        self.latched_failure = LatchedFailure(code)
        return self.latched_failure

    def get_operation_mode(self) -> OperationMode:
        code = self._ask_bytes(b"GOM")
        self.operation_mode = OperationMode(code)
        return self.operation_mode

    def set_operation_mode(self) -> bool:
        mode = hex(int(self.operation_mode))[2:]
        response = self._set(b"SOM", mode.encode("Latin1"))
        return response == ">"

    def power_on(self) -> bool:
        response = self._ask(b"POn")[0] == ">"
        _logger.info(f"Power on: {response}")
        return response

    def power_off(self) -> bool:
        response = self._ask(b"POf")[0] == ">"
        _logger.info(f"Power off: {response}")
        return response

    def laser_off(self) -> bool:
        response = self._ask(b"LOf")[0] == ">"
        _logger.warning(f"Laser off: {response}")
        return response

    def laser_on(self) -> bool:
        response = self._ask(b"LOn")[0] == ">"
        _logger.warning(f"Laser on: {response}")
        return response

    def measure_diode_power(self) -> float:
        response = self._ask(b"MDP")[0]
        self._laser_power = float(response)
        return self._laser_power

    def get_level_power(self):
        response = self._ask(b"GLP")[0]
        return int(response, 16)

    def set_level_power(self, power: float) -> bool:
        if power > 1.0:
            _logger.error("Set power level must be [0-1] but is %5.2f" % power)
        level = int(power * 0xFFF)
        response = self._set(b"SLP", hex(level)[2:].encode("Latin1"))
        return response == ">"

    def _do_shutdown(self) -> None:
        # Disable laser.
        _logger.info("Shuting down...")
        self.laser_off()
        self.connection.flushInput()

    def set_standby(self, enabled: bool) -> None:
        self.standby = enabled
        self._update_mode()

    def set_mode(self, mode: LaserMode) -> None:
        self.mode = mode
        self._update_mode()

    def set_trigger(self, ttype: TriggerType, tmode: TriggerMode) -> None:
        if tmode is not TriggerMode.ONCE:
            raise microscope.UnsupportedFeatureError(
                "the only trigger mode supported is 'once'"
            )

        self._trigger_type = ttype
        self._update_mode()

    def _update_mode(self) -> None:
        self.get_operation_mode()
        if self._trigger_type == TriggerType.SOFTWARE:
            self.operation_mode.APC_mode = True
            self.operation_mode.bias_level_release = not self.standby
            self.operation_mode.operating_level_release = not self.standby
        else:
            self.operation_mode.bias_level_release = True
            self.operation_mode.operating_level_release = True
            if self.standby:
                self.operation_mode.APC_mode = False
            else:
                # APC mode enables the light when external trigger is connected
                self.operation_mode.APC_mode = True
        self.set_operation_mode()

    #  Initialization to do when cockpit connects.

    def initialize(self):
        _logger.info("Initializing...")
        self.connection.flushInput()
        self.get_operation_mode()
        self.operation_mode.auto_powerup = True
        self.operation_mode.auto_startup = False
        self.operation_mode.usb_adhoc_mode = True
        self.operation_mode.APC_mode = True
        self.operation_mode.analog_input_release = False
        self.operation_mode.bias_level_release = False
        self.operation_mode.operating_level_release = False
        self.set_operation_mode()
        self.measure_diode_power()

    def _do_set_power(self, power: float) -> None:
        self._set_point = power
        _logger.info(f"Setting laser power to {power*100}%")
        if self.is_on:
            self.set_level_power(power)

    def _do_get_power(self) -> float:
        return self.measure_diode_power() / self._max_power_mw

    # Turn the laser ON. Return True if we succeeded, False otherwise.
    def _do_enable(self):
        self.is_on = self.laser_on()
        _logger.warning(f"Laser on: {self.is_on}")
        if not self.is_on:
            # Something went wrong.
            _logger.error("Failed to turn on. Current status:\r\n")
            _logger.error(self.get_status())
            _logger.error(self.get_latched_failure())
            return False
        return True

    # Turn the laser OFF.
    def _do_disable(self):
        self.is_on = not self.laser_off()
        _logger.warning(f"Laser off: {not self.is_on}")

    # Return True if the laser is currently able to produce light.
    def get_is_on(self):
        _logger.info(f"is on: {self.is_on}")
        return self.is_on

    @property
    def trigger_mode(self) -> TriggerMode:
        return TriggerMode.ONCE

    @property
    def trigger_type(self) -> TriggerType:
        return self._trigger_type

    def _do_trigger(self) -> None:
        """Actual trigger of the device.
        """
        if self._trigger_type == TriggerType.SOFTWARE:
            self.set_standby(False)
            timer = threading.Timer(self.exposure / 1000.,
                                    self.set_standby,
                                    (True,))
            timer.start()

    def set_exposure_time(self, value):
        """Set exposure time."""
        self.exposure = value

    def get_exposure_time(self):
        """Get exposure time."""
        return self.exposure

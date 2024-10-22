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

import serial
import numpy as np

import microscope._utils
import microscope.abc
from functools import partial
from microscope import TriggerMode, TriggerType
from microscope.win32 import MicroscopeWindowsService
from microscope.abc import SerialDeviceMixin
import threading


_logger = logging.getLogger(__name__)


class CoboltLaser(
    microscope._utils.OnlyTriggersBulbOnSoftwareMixin,
    microscope.abc.SerialDeviceMixin,
    microscope.abc.LightSource,
):
    """Cobolt lasers.

    The cobolt lasers are diode pumped lasers and only supports
    `TriggerMode.SOFTWARE` (this is probably not completely true, some
    cobolt lasers are probably not diode pumped and those should be
    able to support other trigger modes, but we only got access to the
    04 series).

    """

    def __init__(self, com=None, baud=115200, timeout=0.01, **kwargs):
        super().__init__(**kwargs)
        self.connection = serial.Serial(
            port=com,
            baudrate=baud,
            timeout=timeout,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
        )

        self.exposure = 100  # ms

        # Start a logger.
        response = self.send(b"sn?")
        _logger.info("Cobolt laser serial number: [%s]", response.decode())
        # We need to ensure that autostart is disabled so that we can switch emission
        # on/off remotely.
        response = self.send(b"@cobas 0")
        _logger.info("Response to @cobas 0 [%s]", response.decode())

        self._max_power_mw = 120  # mW. We get this value from the manual
        self.initialize()

    def send(self, command):
        """Send command and retrieve response."""
        success = False
        while not success:
            self._write(command)
            response = self._readline()
            # Catch zero-length responses to queries and retry.
            if not command.endswith(b"?"):
                success = True
            elif len(response) > 0:
                success = True
        return response

    @microscope.abc.SerialDeviceMixin.lock_comms
    def clearFault(self):
        self.send(b"cf")
        return self.get_status()

    @microscope.abc.SerialDeviceMixin.lock_comms
    def get_status(self):
        result = []
        for cmd, stat in [
            (b"l?", "Emission on?"),
            (b"p?", "Target power:"),
            (b"pa?", "Measured power:"),
            (b"f?", "Fault?"),
            (b"hrs?", "Head operating hours:"),
        ]:
            response = self.send(cmd)
            result.append(stat + " " + response.decode())
        return result

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _do_shutdown(self) -> None:
        # Disable laser.
        self.disable()
        self.send(b"@cob0")
        self.connection.flushInput()

    #  Initialization to do when cockpit connects.
    @microscope.abc.SerialDeviceMixin.lock_comms
    def initialize(self):
        self.connection.flushInput()
        # We don't want 'direct control' mode.
        self.send(b"@cobasdr 0")
        # Force laser into autostart mode.
        self.send(b"@cob1")

    # Turn the laser ON. Return True if we succeeded, False otherwise.
    @microscope.abc.SerialDeviceMixin.lock_comms
    def _do_enable(self):
        _logger.info("Turning laser ON.")
        # Turn on emission.
        response = self.send(b"l1")
        _logger.info("l1: [%s]", response.decode())

        if not self.get_is_on():
            # Something went wrong.
            _logger.error("Failed to turn on. Current status:\r\n")
            _logger.error(self.get_status())
            return False
        return True

    # Turn the laser OFF.
    @microscope.abc.SerialDeviceMixin.lock_comms
    def disable(self):
        _logger.info("Turning laser OFF.")
        return self.send(b"l0").decode()

    # Return True if the laser is currently able to produce light.
    @microscope.abc.SerialDeviceMixin.lock_comms
    def get_is_on(self):
        response = self.send(b"l?")
        return response == b"1"

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _get_power_mw(self) -> float:
        if not self.get_is_on():
            return 0.0
        success = False
        # Sometimes the controller returns b'1' rather than the power.
        while not success:
            response = self.send(b"pa?")
            if response != b"1":
                success = True
        return 1000 * float(response)

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _set_power_mw(self, mW: float) -> None:
        # There is no minimum power in cobolt lasers.  Any
        # non-negative number is accepted.
        W_str = "%.4f" % (mW / 1000.0)
        _logger.info("Setting laser power to %s W.", W_str)
        return self.send(b"@cobasp " + W_str.encode())

    def _do_set_power(self, power: float) -> None:
        self._set_power_mw(power * self._max_power_mw)

    def _do_get_power(self) -> float:
        return self._get_power_mw() / self._max_power_mw

    def set_exposure_time(self, value):
        self.exposure = value

    def get_exposure_time(self):
        return self.exposure


class CoboltLaser06DPL(CoboltLaser):
    """Specific Cobolt Laser 06-DPL561nm.

    This class implements specific commands that are only present in the
    Cobolt Laser 06-DPL561nm.

    """

    def __init__(self, com=None, baud=115200, timeout=0.01, **kwargs):
        super().__init__(com, baud, timeout, **kwargs)

        # Hardode parameters of the model to transform power to Amperes
        # They can be found in CSBL-5904
        self.a = 7.65034104e02
        self.b = 1.07097444e-02
        self.c = 1.96326100e00
        self.d = 2.71321903e03

        self._trigger_type = TriggerType.SOFTWARE
        self.standby = True
        # Initialize with power = 0
        self.theoretical_power = 0

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

        # Set modulation_low and modulation_high
        # to theoretical power = 0
        self._set_modulation_low_I(650)
        # self._change_modulation_low_mW(self.theoretical_power)
        self._set_power_mw(self.theoretical_power)
        self.initialize()

    def initialize(self):
        self.connection.flushInput()
        self.send(b"@cobasdr 1")  # to activate direct control

    # Model to transform power (mW) to Ampere (A)
    def _mW2A(self, x):
        return self.a * np.tan(self.b * x + self.c) + self.d

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _set_modulation_high_I(self, mA: float) -> None:
        mA_str = "%.4f" % mA
        _logger.info("Setting laser modulation high current to %s mA.", mA_str)
        return self.send(b"smc " + mA_str.encode())

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _get_modulation_high_I(self) -> float:
        if not self.get_is_on():
            return 0.0
        success = False
        # Sometimes the controller returns b'1' rather than the power.
        while not success:
            response = self.send(b"gmc?")
            if response != b"1":
                success = True
        return float(response)

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _set_modulation_low_I(self, mA: float) -> None:
        mA_str = "%.4f" % mA
        _logger.info("Setting laser modulation low current to %s mA.", mA_str)
        return self.send(b"slth " + mA_str.encode())

    @microscope.abc.SerialDeviceMixin.lock_comms
    def _get_modulation_low_I(self) -> float:
        if not self.get_is_on():
            return 0.0
        response = self.send(b"glth?")
        return float(response)

    def _change_modulation_low_mW(self, mW: float) -> None:
        equivalent_mA = self._mW2A(mW)
        self._set_modulation_low_I(equivalent_mA)

    # Overwrite existing method
    @microscope.abc.SerialDeviceMixin.lock_comms
    def _get_power_mw(self) -> float:
        if not self.get_is_on():
            return 0.0

        return self.theoretical_power

    # Overwrite existing method
    @microscope.abc.SerialDeviceMixin.lock_comms
    def _set_power_mw(self, mW: float) -> None:
        # There is no minimum power in cobolt lasers.  Any
        # non-negative number is accepted.
        equivalent_mA = self._mW2A(mW)
        mA_str = "%.4f" % equivalent_mA
        mW_str = "%.4f" % mW

        _logger.info("Equivalent current for %s mW: %s mA", mW_str, mA_str)

        answer = self._set_modulation_high_I(equivalent_mA)

        self.theoretical_power = mW

        return answer
        # return self.exposure

    def set_standby(self, enabled: bool):
        self.standby = enabled
        if enabled:
            self._set_power_mw(0.12)
        else:
            self._set_power_mw(100)

    @property
    def trigger_mode(self):
        return TriggerMode.ONCE

    @property
    def trigger_type(self):
        return self._trigger_type

    def _do_trigger(self):
        if self._trigger_type == TriggerType.SOFTWARE:
            self.set_standby(False)
            timer = threading.Timer(self.exposure / 1000.,
                                    self.set_standby,
                                    (True,))
            timer.start()

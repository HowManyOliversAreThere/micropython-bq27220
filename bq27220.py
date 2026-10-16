"""
MicroPython driver for the Texas Instruments BQ27220 single-cell Li-ion
fuel gauge (CEDV algorithm, I2C).

I2C address (7-bit): 0x55 (fixed). Up to 400 kHz.

This covers the gauge's standard commands - state of charge, voltage,
current, capacity, time-to-empty/full, temperature, status flags - and
reading the device number. It doesn't change the gauge's data memory
(design capacity, CEDV profile and so on). The gauge ships with generic
defaults, so for accurate state-of-charge figures configure it for your
cell with TI's tools (or the CONFIG UPDATE procedure in the TRM).

Quick start
-----------
    from machine import I2C, Pin
    import bq27220

    i2c = I2C(0, sda=Pin(19), scl=Pin(20), freq=400_000)
    gauge = bq27220.BQ27220(i2c)
    print(gauge.state_of_charge(), "%", gauge.voltage(), "mV", gauge.current(), "mA")
    print(gauge.charging, gauge.time_to_empty())
"""

import time
from micropython import const

ADDR = 0x55
DEVICE_NUMBER = 0x0220

# Standard commands (each a little-endian 16-bit word).
_CMD_CONTROL = const(0x00)
_CMD_TEMPERATURE = const(0x06)  # 0.1 K
_CMD_VOLTAGE = const(0x08)  # mV
_CMD_BATTERY_STATUS = const(0x0A)
_CMD_CURRENT = const(0x0C)  # mA, signed
_CMD_REMAINING_CAPACITY = const(0x10)  # mAh
_CMD_FULL_CHARGE_CAPACITY = const(0x12)  # mAh
_CMD_AVERAGE_CURRENT = const(0x14)  # mA, signed
_CMD_TIME_TO_EMPTY = const(0x16)  # minutes, 65535 = not discharging
_CMD_TIME_TO_FULL = const(0x18)  # minutes, 65535 = not charging
_CMD_AVERAGE_POWER = const(0x24)  # mW, signed
_CMD_INTERNAL_TEMPERATURE = const(0x28)  # 0.1 K
_CMD_CYCLE_COUNT = const(0x2A)
_CMD_STATE_OF_CHARGE = const(0x2C)  # %
_CMD_STATE_OF_HEALTH = const(0x2E)  # %
_CMD_OPERATION_STATUS = const(0x3A)
_CMD_DESIGN_CAPACITY = const(0x3C)  # mAh
_CMD_MAC = const(0x3E)  # ManufacturerAccessControl

_SUB_DEVICE_NUMBER = const(0x0001)
_SUB_FW_VERSION = const(0x0002)

# BatteryStatus() bits.
STATUS_DSG = 0x0001  # discharging (clear while charging or relaxed)
STATUS_SYSDWN = 0x0002  # system should shut down
STATUS_TDA = 0x0004  # terminate discharge alarm
STATUS_BATTPRES = 0x0008  # battery present
STATUS_AUTH_GD = 0x0010
STATUS_OCVGD = 0x0020
STATUS_TCA = 0x0040  # terminate charge alarm
STATUS_CHGINH = 0x0100  # charge inhibited (temperature)
STATUS_FC = 0x0200  # fully charged
STATUS_OTD = 0x0400  # over-temperature, discharging
STATUS_OTC = 0x0800  # over-temperature, charging
STATUS_SLEEP = 0x1000  # gauge is in SLEEP mode
STATUS_OCVFAIL = 0x2000
STATUS_OCVCOMP = 0x4000
STATUS_FD = 0x8000  # fully discharged


class BQ27220:
    def __init__(self, i2c, address=ADDR, check_id=True):
        """
        i2c      : machine.I2C (or SoftI2C) instance
        check_id : read DEVICE_NUMBER and raise OSError unless it's 0x0220
        """
        self._i2c = i2c
        self._addr = address
        self._buf = bytearray(2)
        if check_id:
            number = self.device_number()
            if number != DEVICE_NUMBER:
                raise OSError("BQ27220 not found (device number 0x%04X)" % number)

    # ---- Low-level I2C ----------------------------------------------------
    def _word(self, cmd):
        self._i2c.readfrom_mem_into(self._addr, cmd, self._buf)
        return self._buf[0] | (self._buf[1] << 8)

    def _signed(self, cmd):
        v = self._word(cmd)
        return v - 0x10000 if v & 0x8000 else v

    def _subcommand(self, sub, length=2):
        """Run a Control() subcommand and return its data from
        MACData(): the subcommand echo is checked and stripped."""
        self._i2c.writeto_mem(self._addr, _CMD_CONTROL, bytes((sub & 0xFF, sub >> 8)))
        time.sleep_ms(2)
        data = self._i2c.readfrom_mem(self._addr, _CMD_MAC, 2 + length)
        if data[0] | (data[1] << 8) != sub:
            raise OSError("BQ27220 subcommand 0x%04X not acknowledged" % sub)
        return data[2:]

    # ---- Identity ---------------------------------------------------------
    def device_number(self):
        d = self._subcommand(_SUB_DEVICE_NUMBER)
        return d[0] | (d[1] << 8)

    def firmware_version(self):
        """Raw FW_VERSION bytes, as described in the TRM."""
        return bytes(self._subcommand(_SUB_FW_VERSION, 8))

    # ---- Battery state ----------------------------------------------------
    def state_of_charge(self):
        """Relative state of charge, 0-100 %."""
        return self._word(_CMD_STATE_OF_CHARGE)

    def state_of_health(self):
        return self._word(_CMD_STATE_OF_HEALTH)

    def voltage(self):
        """Cell voltage, mV."""
        return self._word(_CMD_VOLTAGE)

    def current(self):
        """Instantaneous current, mA: positive while charging,
        negative while discharging. Updated once a second."""
        return self._signed(_CMD_CURRENT)

    def average_current(self):
        return self._signed(_CMD_AVERAGE_CURRENT)

    def average_power(self):
        """mW, signed like current()."""
        return self._signed(_CMD_AVERAGE_POWER)

    def remaining_capacity(self):
        """mAh."""
        return self._word(_CMD_REMAINING_CAPACITY)

    def full_charge_capacity(self):
        """mAh."""
        return self._word(_CMD_FULL_CHARGE_CAPACITY)

    def design_capacity(self):
        """mAh, from the gauge's configuration."""
        return self._word(_CMD_DESIGN_CAPACITY)

    def time_to_empty(self):
        """Minutes, or None when not discharging."""
        v = self._word(_CMD_TIME_TO_EMPTY)
        return None if v == 0xFFFF else v

    def time_to_full(self):
        """Minutes, or None when not charging."""
        v = self._word(_CMD_TIME_TO_FULL)
        return None if v == 0xFFFF else v

    def cycle_count(self):
        return self._word(_CMD_CYCLE_COUNT)

    def temperature(self):
        """Battery temperature (from the thermistor, or the gauge's
        internal sensor if it's configured that way), degrees C."""
        return self._word(_CMD_TEMPERATURE) / 10 - 273.15

    def internal_temperature(self):
        """The gauge's own die temperature, degrees C."""
        return self._word(_CMD_INTERNAL_TEMPERATURE) / 10 - 273.15

    # ---- Status -----------------------------------------------------------
    def status(self):
        """Raw BatteryStatus() word - see the STATUS_* bits."""
        return self._word(_CMD_BATTERY_STATUS)

    def operation_status(self):
        return self._word(_CMD_OPERATION_STATUS)

    @property
    def charging(self):
        """True while current flows into the cell."""
        return self.current() > 0

    @property
    def battery_present(self):
        return bool(self.status() & STATUS_BATTPRES)

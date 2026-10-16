"""
MicroPython driver for the Texas Instruments BQ27220 single-cell Li-ion
fuel gauge (CEDV algorithm, I2C).

I2C address (7-bit): 0x55 (fixed). Up to 400 kHz.

This covers the gauge's standard commands - state of charge, voltage,
current, capacity, time-to-empty/full, temperature, status flags - and
reading the device number - and writing the gauge's data memory, such
as the cell's capacity (set_capacity()). The data memory is RAM: it goes
back to the factory defaults (3000 mAh) whenever the gauge loses power,
so set it on every start-up - set_capacity() only writes when it differs.
The rest of the CEDV profile stays at TI's generic values; for the best
accuracy, characterise your cell with TI's tools.

Quick start
-----------
    from machine import I2C, Pin
    import bq27220

    i2c = I2C(0, sda=Pin(19), scl=Pin(20), freq=400_000)
    gauge = bq27220.BQ27220(i2c)
    print(gauge.state_of_charge(), "%", gauge.voltage(), "mV", gauge.current(), "mA")
    print(gauge.charging, gauge.time_to_empty())
    gauge.set_capacity(1000)  # a 1000 mAh cell
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
_CMD_MAC_DATA = const(0x40)
_CMD_MAC_DATA_SUM = const(0x60)  # then MACDataLen() at 0x61

_SUB_DEVICE_NUMBER = const(0x0001)
_SUB_FW_VERSION = const(0x0002)
_SUB_SEALED = const(0x0030)
_SUB_ENTER_CFG_UPDATE = const(0x0090)
_SUB_EXIT_CFG_UPDATE_REINIT = const(0x0091)
UNSEAL_KEYS = (0x0414, 0x3672)  # TI's defaults

# OperationStatus() bits.
OP_SEC_SHIFT = const(1)  # 2 bits: SEC_*
OP_INITCOMP = const(0x0020)
OP_CFGUPDATE = const(0x0400)
SEC_FULL = const(1)
SEC_UNSEALED = const(2)
SEC_SEALED = const(3)

# Data memory addresses (values are big-endian).
DM_FULL_CHARGE_CAPACITY = const(0x929D)  # mAh, CEDV profile 1
DM_DESIGN_CAPACITY = const(0x929F)  # mAh, CEDV profile 1

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

    # ---- Configuration (data memory) ----------------------------------------
    def _control(self, sub):
        self._i2c.writeto_mem(self._addr, _CMD_CONTROL, bytes((sub & 0xFF, sub >> 8)))

    def security(self):
        """SEC_SEALED, SEC_UNSEALED or SEC_FULL."""
        return (self.operation_status() >> OP_SEC_SHIFT) & 3

    def unseal(self, keys=UNSEAL_KEYS):
        """Allow data memory access. Raises OSError if the keys don't work."""
        if self.security() != SEC_SEALED:
            return
        for key in keys:
            self._control(key)
            time.sleep_ms(5)
        if self.security() == SEC_SEALED:
            raise OSError("BQ27220 didn't unseal")

    def seal(self):
        self._control(_SUB_SEALED)
        time.sleep_ms(5)

    def _wait_status(self, bit, set_, timeout_ms):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while bool(self.operation_status() & bit) != set_:
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise OSError("BQ27220 timed out waiting for status 0x%04X" % bit)
            time.sleep_ms(10)

    def read_data_memory(self, address, size):
        """`size` bytes of data memory at `address` (needs unsealing)."""
        self._i2c.writeto_mem(self._addr, _CMD_MAC, bytes((address & 0xFF, address >> 8)))
        time.sleep_ms(2)
        return self._i2c.readfrom_mem(self._addr, _CMD_MAC_DATA, size)

    def write_data_memory(self, values):
        """Write data memory in one CONFIG UPDATE: `values` is a list of
        (address, bytes) - multi-byte values big-endian. Unseals first if
        needed (and seals again after). Takes about 2 seconds."""
        was_sealed = self.security() == SEC_SEALED
        self.unseal()
        self._i2c.writeto_mem(self._addr, _CMD_MAC, bytes((_SUB_ENTER_CFG_UPDATE & 0xFF, _SUB_ENTER_CFG_UPDATE >> 8)))
        self._wait_status(OP_CFGUPDATE, True, 2000)
        try:
            for address, data in values:
                block = bytes((address & 0xFF, address >> 8)) + bytes(data)
                self._i2c.writeto_mem(self._addr, _CMD_MAC, block)
                time.sleep_ms(1)
                checksum = 0xFF - (sum(block) & 0xFF)
                # Checksum, then the length of address + data + checksum + length.
                self._i2c.writeto_mem(self._addr, _CMD_MAC_DATA_SUM, bytes((checksum, len(block) + 2)))
                time.sleep_ms(10)
        finally:
            self._control(_SUB_EXIT_CFG_UPDATE_REINIT)
            time.sleep_ms(1000)
            self._wait_status(OP_CFGUPDATE, False, 3000)
            if was_sealed:
                self.seal()

    def set_capacity(self, mah):
        """Configure the gauge for a `mah` cell: design capacity, and the
        starting full-charge capacity (the gauge learns the real one over
        charge cycles). Does nothing if it's already set. Returns True if
        it wrote."""
        if self.design_capacity() == mah:
            return False
        v = bytes((mah >> 8, mah & 0xFF))
        self.write_data_memory([(DM_DESIGN_CAPACITY, v), (DM_FULL_CHARGE_CAPACITY, v)])
        if self.design_capacity() != mah:
            raise OSError("BQ27220 capacity didn't take (reads %d mAh)" % self.design_capacity())
        return True

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

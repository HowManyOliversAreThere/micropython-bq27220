"""Print a battery report every few seconds."""

import time
from machine import I2C, Pin

import bq27220

i2c = I2C(0, sda=Pin(19), scl=Pin(20), freq=400_000)
gauge = bq27220.BQ27220(i2c)
print("Design capacity: {} mAh".format(gauge.design_capacity()))

while True:
    current = gauge.current()
    state = "charging" if current > 0 else ("discharging" if current < 0 else "idle")
    print("{}%  {} mV  {} mA ({})  {:.1f} °C  status 0x{:04X}".format(
        gauge.state_of_charge(), gauge.voltage(), current, state,
        gauge.temperature(), gauge.status()))
    time.sleep(5)

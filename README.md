# BQ27220 fuel gauge

A MicroPython driver for the Texas Instruments **BQ27220** single-cell
Li-ion fuel gauge: state of charge, voltage, current, capacity, time to
empty/full, temperature and status flags, over I2C.

Copy `bq27220.py` onto your board, or install it with `mip`:

```python
import mip
mip.install("github:HowManyOliversAreThere/micropython-bq27220")
```

## Usage

```python
from machine import I2C, Pin
import bq27220

i2c = I2C(0, sda=Pin(19), scl=Pin(20), freq=400_000)
gauge = bq27220.BQ27220(i2c)

print(gauge.state_of_charge(), "%")
print(gauge.voltage(), "mV", gauge.current(), "mA")   # current > 0 while charging
print(gauge.time_to_empty(), "min to empty")           # None unless discharging
if gauge.status() & bq27220.STATUS_SYSDWN:
    print("battery critically low")
```

## Notes

- The address is fixed at 0x55. By default the constructor reads the
  device number and raises `OSError` unless it's 0x0220.
- The gauge's figures are only as good as its configuration (design
  capacity, CEDV profile, termination voltage...). This driver doesn't
  write data memory. Configure the gauge for your cell with TI's tools, or
  follow the CONFIG UPDATE procedure in the BQ27220 technical reference
  manual (SLUUBD4).
- Current, voltage and so on update about once a second.

## License

MIT - see [LICENSE](LICENSE).

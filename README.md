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

gauge.set_capacity(1000)  # configure for a 1000 mAh cell (only writes if it differs)
```

## Notes

- The address is fixed at 0x55. By default the constructor reads the
  device number and raises `OSError` unless it's 0x0220.
- The gauge's figures are only as good as its configuration. The data
  memory is RAM: it's back to TI's defaults (a 3000 mAh cell) whenever
  the gauge loses power, so call `set_capacity()` at every start-up. It
  sets the design capacity and the starting full-charge capacity, which
  the gauge then learns over charge cycles. Writing takes about 2 s.
- `write_data_memory()` and `read_data_memory()` reach any other
  setting, by address (values big-endian). They unseal with TI's default
  keys, and seal again after if it was sealed. The rest of the CEDV
  profile stays generic; for the best accuracy, characterise your cell
  with TI's tools. The addresses and update sequence follow
  [Flipper Zero's driver](https://github.com/flipperdevices/flipperzero-firmware/tree/dev/lib/drivers),
  since the technical reference manual (SLUUBD4) gets the procedure
  wrong.
- Current, voltage and so on update about once a second.

## License

MIT - see [LICENSE](LICENSE).

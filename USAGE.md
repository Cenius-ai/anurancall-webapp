# Using AnuranCall

Every example below is a real command. `python3 anurancall.py <command> --help`
documents each one, and `--data PATH` points any command at a different store.

## 1. Count a bout by tapping

```bash
python3 anurancall.py count --species 'Spring Peeper' --site 'Mud Pond' --temp 16.0 \
  --notes 'chorus from the north bank, water still'
```

Press <kbd>Enter</kbd> once per call. Each press prints the running call number
and the seconds since the previous call:

```
count  Spring Peeper at Mud Pond
  press enter once per call, then q and enter to finish

call 1
call 2  +0.51 s
call 3  +0.61 s
```

Timestamps come from `time.monotonic`, so an NTP correction mid-bout cannot
skew the rate. Type `q` and <kbd>Enter</kbd> (or press <kbd>Ctrl-D</kbd>) to
finish. The closing summary reports calls, duration, mean and median interval,
the min/max spread and the drip rate, then saves the session and prints its id.
A bout with fewer than two calls is reported as insufficient and is **not**
saved.

## 2. Paste intervals from a field sheet

```bash
python3 anurancall.py count --species 'Wood Frog' --site 'Beaver Marsh' --temp 12.0 \
  --notes 'two males clucking' --intervals '2.0, 2.4, 1.8' --date 2024-04-28
```

Commas or spaces both work. Every value must be a positive number — the error
names the offending token (`error: interval 'zero' is not a number ...`). The
method line reads `intervals` so pasted and tapped runs stay distinguishable, and
`--date` lets you backdate a run you transcribed days later.

## 3. Read the log

```bash
python3 anurancall.py sessions
python3 anurancall.py sessions --species 'Spring Peeper' --from 2024-05-01 --to 2024-05-31
python3 anurancall.py show 2024-05-12-3f9a
```

```
sessions  2 of 3 matched
  filters: species "Spring Peeper"

date        id               species        site       temp  calls/min
----------  ---------------  -------------  -------  ------  ---------
2024-05-12  2024-05-12-3f9a  Spring Peeper  Mud Pond  17.5 C       58.5
2024-05-02  2024-05-02-b71c  Spring Peeper  Mud Pond       —       61.3
```

`show` prints every stored field plus the whole interval series.

## 4. Species-by-date phenology

```bash
python3 anurancall.py phenology
python3 anurancall.py phenology --site 'Mud Pond' --from 2024-05-01 --to 2024-05-31
```

```
phenology
  filters: none (all sites, all dates)
  2 species across 3 dates

species        2024-05-01  2024-05-03  2024-05-12  total
-------------  ----------  ----------  ----------  -----
Spring Peeper           1           1           2      4
Wood Frog               1           0           0      1
```

The filter line always states the active window and site. With more dates than
fit in 100 columns the grid keeps the newest ones and says so, pointing at
`--from`/`--to`.

## 5. Pond site summary

```bash
python3 anurancall.py sites
```

```
sites  2

site          sessions    temp  latest      newest note
------------  --------  ------  ----------  ---------------------------------
Beaver Marsh         1  12.0 C  2024-04-28  two males clucking
Mud Pond             3  17.5 C  2024-05-12  full chorus along the north shore
```

A pond with no recorded temperature shows an em-dash, never `0` and never a
blank cell. Long notes wrap under their own column instead of being truncated.

## 6. Correct or remove a session

```bash
python3 anurancall.py edit 2024-05-12-3f9a --temp 19.0
python3 anurancall.py edit 2024-05-12-3f9a --notes 'chorus had stopped by 21:30'
python3 anurancall.py delete 2024-05-12-3f9a          # asks y/N, echoes the id
python3 anurancall.py delete 2024-05-12-3f9a --yes    # in scripts
```

`edit` validates the temperature range and the temperature/pond pairing exactly
like `count` does, and the phenology and site views pick the change up
immediately. Answering `n` (or piping no answer) leaves the file untouched.

## 7. Pipe it somewhere

```bash
python3 anurancall.py sessions --json | jq '.sessions[0].drip_rate_per_min'
python3 anurancall.py phenology --json | jq '.row_totals'
python3 anurancall.py sites --json
python3 anurancall.py count --species 'Bullfrog' --site 'Quarry Pool' --intervals '3.1, 2.9' --json
```

`--json` prints exactly one JSON object on stdout with no headers and no colour;
errors still go to stderr with a non-zero exit status, so a pipeline fails loudly.

## Paths

`--data PATH` (or `$ANURANCALL_DATA`) points every command at a different store.
The value is resolved once: `.` and symlinks are collapsed, and a `..` segment or
a directory is refused — `error: data path '../etc/x.json' contains a '..'
segment`. The atomic save writes only a sibling `<store>.tmp` inside the store's
own directory, and refuses a temp path that escapes it or is a symlink, so a
crafted path can never redirect a write outside the file you named.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success (including "nothing matched", which is not an error). |
| `1` | A rejected input or an unreadable store — one plain line on stderr. |
| `2` | Usage error from `argparse` (unknown command, missing `--species`). |
| `130` | Interrupted with <kbd>Ctrl-C</kbd>. |

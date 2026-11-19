# Install AnuranCall

AnuranCall is one Python file with **zero third-party runtime dependencies**. The
steps below are short because there is nothing to build, migrate or serve.

## Prerequisites

1. **Python 3.9 or newer** on `PATH` (`python3 -V` must print 3.9+). Nothing
   else is required — no compiler, no database, no system libraries.
2. A POSIX shell (`bash`/`sh`) for the two helper scripts.
3. Tested on Linux and macOS. Windows users can run the tool itself; the shell
   scripts need Git Bash or WSL.

`install.sh` never uses `sudo`, never installs system packages and never starts
anything in the foreground.

## 1. Install project dependencies

```bash
bash install.sh
```

The package manager for this project is **pip**, driven by the committed
`requirements.txt` (there is no lockfile to choose from, and no second
package manager). AnuranCall itself imports only the standard library; the only
declared dependency is the dev test runner `pytest`, listed so
`python3 -m pytest tests` works on a clean machine. The suite is also written
with stdlib `unittest` and runs with no packages installed at all.

The script is idempotent — run it as often as you like. It finishes by importing
the module, running `--help`, collecting the test suite, and then **exits**.

## 2. Create your survey store (automatic)

There is no separate seed or migration step: the JSON store is created at
`~/.anurancall/sessions.json` on the first saved session. To keep several ponds
or field seasons apart, point it elsewhere at any time:

```bash
export ANURANCALL_DATA=/path/to/pond-2024.json      # same as --data PATH
```

`--data PATH` on the command line wins over `$ANURANCALL_DATA`; with neither set
the default above is used.

## 3. Run it

```bash
python3 anurancall.py --help
python3 anurancall.py count --species 'Spring Peeper' --site 'Mud Pond'
python3 anurancall.py sessions
python3 anurancall.py phenology
python3 anurancall.py sites
```

There is no server, no port and no background process: every command runs to
completion and exits with `0` on success and non-zero on failure.

## 4. Try the guided demo

```bash
bash demo.sh
```

Non-interactive, writes to `demo/sessions.json` (safe to re-run, safe to delete).

## 5. Run the tests

```bash
python3 -m unittest discover tests     # stdlib only
python3 -m pytest tests                # optional, uses the declared dev dependency
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `error: --temp needs --site` | A temperature belongs to a named pond; add `--site 'Mud Pond'`. |
| `error: ... is not valid JSON` | The store was hand-edited badly. `mv <file> <file>.bak` and start a fresh file. |
| `error: no session with id '...'` | Run `python3 anurancall.py sessions` to list the real ids. |
| `error: data path ... contains a '..' segment` | `--data` must name the store file directly; `..` is refused and a directory is rejected. |
| Colour codes in a pipe | They are already disabled for non-TTY output; force it with `--no-color` or `NO_COLOR=1`. |

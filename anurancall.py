#!/usr/bin/env python3
"""AnuranCall — frog call drip-rate counter for pond surveys.

Single file, standard library only, no network. Everything is stored in one
hand-editable JSON document (~/.anurancall/sessions.json by default; override
with --data PATH or $ANURANCALL_DATA).

Commands
    count       record a session by tapping enter or pasting an interval list
    sessions    list saved sessions (newest first) with filters
    show        print every field of one session
    edit        correct the site, temperature or note of a session
    delete      remove a session after a y/N confirmation
    phenology   species-by-date table of session counts
    sites       per-pond summary: surveys, latest temperature, newest note
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, NamedTuple, Sequence

VERSION = "1.0.0"
SCHEMA_VERSION = 1
TEMP_MIN_C = -5.0
TEMP_MAX_C = 45.0
MAX_WIDTH = 100
EM_DASH = "\u2014"
DATA_ENV_VAR = "ANURANCALL_DATA"

# Committed design direction (calm-precise, terminal dark, mono, one accent).
# These are the tokens every surface of the tool draws from: the accent carries
# the primary status only (never the only signal), the neutral ramp stays
# unemphasised, and nothing here depends on a remote asset or font.
THEME: dict[str, str] = {
    "accent": "#5478c1",        # oklch(0.58 0.12 263) — primary status only
    "foreground": "#e6e8ef",    # body text on the terminal-dark surface
    "muted": "#9aa3b5",         # labels, secondary values
    "surface": "#12141a",       # the terminal's own dark ground
    "ansi_accent": "\x1b[38;2;84;120;193m",
    "ansi_reset": "\x1b[0m",
}


class AnuranError(Exception):
    """A user-facing failure: printed as one plain line, never a traceback."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class Style:
    """Exactly one accent, and it never carries meaning on its own."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def _wrap(self, text: str, code: str) -> str:
        if not self.enabled or not text:
            return text
        return f"{code}{text}{THEME['ansi_reset']}"

    def accent(self, text: str) -> str:
        return self._wrap(text, THEME["ansi_accent"])


STYLE = Style(False)


def install_style(style: Style) -> None:
    global STYLE
    STYLE = style


def color_enabled(no_color_flag: bool) -> bool:
    if no_color_flag or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def emit(text: str = "") -> None:
    print(text, flush=True)


def warn(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def prog_name() -> str:
    raw = sys.argv[0] if sys.argv else ""
    name = Path(raw).name if raw else ""
    return name or "anurancall.py"


def display_path(path: Path) -> str:
    """Short, copy-pasteable form of a store path for human output."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except (ValueError, OSError):
        return str(path)


def command_hint(parts: Sequence[str]) -> str:
    prog = prog_name()
    prefix = f"python3 {prog}" if prog.endswith(".py") else prog
    return " ".join([prefix, *parts])


# Single source for the "record your first count" hints, so every empty state
# teaches the same command and none of them exceed the 100-column budget.
HINT_COUNT = [
    "count",
    "--species",
    "'Spring Peeper'",
    "--site",
    "'Mud Pond'",
    "--intervals",
    "'1.0, 1.2'",
]
HINT_COUNT_WITH_TEMP = [
    "count",
    "--species",
    "'Spring Peeper'",
    "--site",
    "'Mud Pond'",
    "--temp",
    "17.5",
]


# --------------------------------------------------------------------------- #
# text rendering
# --------------------------------------------------------------------------- #

def ellipsize(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "\u2026"
    return text[: width - 1] + "\u2026"


def wrap_text(text: str, width: int) -> list[str]:
    """Wrap on word boundaries; unbreakable tokens are hard-split."""
    words = text.split()
    if not words:
        return []
    width = max(width, 8)
    lines: list[str] = []
    current = ""
    for word in words:
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _pad(value: str, width: int, align: str) -> str:
    value = ellipsize(value, width)
    return value.rjust(width) if align == "right" else value.ljust(width)


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    aligns: Sequence[str] | None = None,
    caps: Sequence[int] | None = None,
    wrap_last: int | None = None,
) -> list[str]:
    """Fixed-width table; the last column wraps onto continuation lines when asked."""
    columns = len(headers)
    aligns = list(aligns) if aligns else ["left"] * columns
    caps = list(caps) if caps else [MAX_WIDTH] * columns
    if wrap_last is not None:
        caps = [*caps[:-1], wrap_last]

    def cell_lines(index: int, value: str) -> list[str]:
        if wrap_last is not None and index == columns - 1:
            return wrap_text(value, wrap_last) or [EM_DASH]
        return [ellipsize(value, caps[index])]

    rendered = [
        [
            cell_lines(index, value)
            for index, value in enumerate(
                (list(str(cell) for cell in row[:columns]) + [EM_DASH] * columns)[:columns]
            )
        ]
        for row in rows
    ]
    widths = []
    for index, header in enumerate(headers):
        widest = len(ellipsize(header, caps[index]))
        for row_cells in rendered:
            for line in row_cells[index]:
                widest = max(widest, len(line))
        widths.append(min(widest, caps[index]))

    lines = ["  ".join(_pad(headers[i], widths[i], aligns[i]) for i in range(columns)).rstrip()]
    lines.append("  ".join("-" * width for width in widths))
    for row_cells in rendered:
        lines.append(
            "  ".join(_pad(cell[0], widths[i], aligns[i]) for i, cell in enumerate(row_cells)).rstrip()
        )
        for extra in range(1, max((len(cell) for cell in row_cells), default=1)):
            filler = [
                _pad(cell[extra], widths[i], aligns[i]) if extra < len(cell) else " " * widths[i]
                for i, cell in enumerate(row_cells)
            ]
            lines.append("  ".join(filler).rstrip())
    return lines


def fmt_temp(value: float | None) -> str:
    return EM_DASH if value is None else f"{float(value):.1f} C"


def fmt_rate(value: float) -> str:
    return f"{float(value):.1f}"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


# --------------------------------------------------------------------------- #
# intervals, statistics, validation
# --------------------------------------------------------------------------- #

class RateStats(NamedTuple):
    """One bout's rate statistics."""

    call_count: int
    duration_s: float
    mean_interval_s: float
    median_interval_s: float
    min_interval_s: float
    max_interval_s: float
    drip_rate_per_min: float


def parse_intervals(raw: str) -> list[float]:
    """Parse comma- or space-separated inter-call intervals in seconds."""
    tokens = raw.replace(",", " ").split()
    if not tokens:
        raise AnuranError(
            "no intervals given — pass seconds like --intervals '1.0, 1.2, 0.8'"
        )
    values: list[float] = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError as exc:
            raise AnuranError(
                f"interval {token!r} is not a number — give seconds, like --intervals '1.0, 1.2, 0.8'"
            ) from exc
        if not math.isfinite(value) or value <= 0:
            raise AnuranError(
                f"interval {token!r} must be a positive number of seconds"
            )
        values.append(value)
    if len(values) < 2:
        raise AnuranError(
            "a rate needs at least two calls — give at least two intervals, like --intervals '1.0, 1.2'"
        )
    return values


def compute_stats(intervals: Sequence[float], call_count: int) -> RateStats:
    if not intervals:
        raise AnuranError("no intervals to summarise")
    # statistics is imported here, not at module scope, to keep cold start fast
    import statistics

    mean = statistics.fmean(intervals)
    if mean <= 0:
        raise AnuranError("intervals must be positive to compute a drip rate")
    return RateStats(
        call_count=call_count,
        duration_s=sum(intervals),
        mean_interval_s=mean,
        median_interval_s=statistics.median(intervals),
        min_interval_s=min(intervals),
        max_interval_s=max(intervals),
        drip_rate_per_min=60.0 / mean,
    )


def require_species(raw: str | None) -> str:
    species = (raw or "").strip()
    if not species:
        raise AnuranError(
            "--species is required — name what you were hearing, e.g. --species 'Spring Peeper'"
        )
    if len(species) > 60:
        raise AnuranError("--species is too long — keep it under 60 characters")
    return species


def clean_text(raw: str | None, *, limit: int = 2000) -> str | None:
    if raw is None:
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    if len(text) > limit:
        raise AnuranError(
            f"that text is {len(text)} characters long — trim it below {limit}"
        )
    return text


def parse_temperature(raw: str | None) -> float | None:
    if raw is None:
        return None
    token = raw.strip()
    if token == "" or token.lower() in {"none", "null", "-"}:
        return None
    try:
        value = float(token)
    except ValueError as exc:
        raise AnuranError(
            f"--temp {raw!r} is not a number of degrees Celsius"
        ) from exc
    if not math.isfinite(value):
        raise AnuranError(f"--temp {raw!r} is not a real temperature")
    if value < TEMP_MIN_C or value > TEMP_MAX_C:
        raise AnuranError(
            f"--temp {value:g} is out of range — temperature must be between "
            f"{TEMP_MIN_C:g} and {TEMP_MAX_C:g} C"
        )
    return round(value, 1)


def require_site_for_temperature(site: str | None, temperature_c: float | None) -> None:
    if temperature_c is not None and not site:
        raise AnuranError("--temp needs --site — a temperature belongs to a named pond")


def parse_iso_date(raw: str, flag: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise AnuranError(f"{flag} {raw!r} is not a date — use YYYY-MM-DD") from exc


# --------------------------------------------------------------------------- #
# tap counter
# --------------------------------------------------------------------------- #

class TapBout(NamedTuple):
    """A finished tap bout: how many taps landed and the gaps between them."""

    call_count: int
    intervals: list[float]


def collect_taps(
    read_line: Callable[[], str | None],
    clock: Callable[[], float],
    on_tap: Callable[[int, float | None], None],
) -> TapBout:
    """Timestamp one tap per entered line. `read_line` returns None at end-of-input."""
    previous: float | None = None
    calls = 0
    intervals: list[float] = []
    while True:
        raw = read_line()
        if raw is None:
            break
        if raw.strip().lower() in {"q", "quit", "done", "exit"}:
            break
        now = clock()
        calls += 1
        interval = None if previous is None else now - previous
        if interval is not None:
            intervals.append(interval)
        previous = now
        on_tap(calls, interval)
    return TapBout(call_count=calls, intervals=intervals)


def stdin_reader() -> str | None:
    line = sys.stdin.readline()
    if line == "":
        return None
    return line.rstrip("\n")


def read_confirmation(prompt: str) -> str | None:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.readline()
    if line == "" or not sys.stdout.isatty():
        # keep the recorded/piped transcript reading on its own line
        sys.stdout.write("\n")
        sys.stdout.flush()
    if line == "":
        return None
    return line


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #

def default_data_path() -> Path:
    return Path.home() / ".anurancall" / "sessions.json"


def resolve_data_path(cli_value: str | None) -> Path:
    """Normalise --data / $ANURANCALL_DATA into one absolute file path.

    The value is user-chosen, so it is settled exactly once: '..' segments are
    refused outright, '.' and symlinks are collapsed by resolve(), and a
    directory is rejected. Every later read and write uses this resolved path,
    so a store can only ever live at the file it names.
    """
    raw = (cli_value or "").strip() or os.environ.get(DATA_ENV_VAR, "").strip()
    candidate = Path(raw).expanduser() if raw else default_data_path()
    if ".." in candidate.parts:
        raise AnuranError(
            f"data path {str(candidate)!r} contains a '..' segment — give the store "
            f"file path directly"
        )
    resolved = candidate.resolve()
    if resolved.is_dir():
        raise AnuranError(f"data path {resolved} is a directory — give the JSON file path")
    return resolved


class Store:
    """The single JSON document: one schema_version plus the session list."""

    def __init__(self, path: Path, sessions: list[dict[str, Any]] | None = None) -> None:
        self.path = path
        self.sessions: list[dict[str, Any]] = list(sessions) if sessions else []

    @classmethod
    def load(cls, path: Path) -> "Store":
        path = Path(path).expanduser().resolve()
        if not path.exists():
            return cls(path, [])
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AnuranError(f"cannot read {path}: {exc.strerror or exc}") from exc
        if not raw.strip():
            return cls(path, [])
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AnuranError(
                f"{path} is not valid JSON (line {exc.lineno}: {exc.msg}) — move it "
                f"aside, e.g. mv {path} {path}.bak, then run the command again"
            ) from exc
        if not isinstance(document, dict):
            raise AnuranError(
                f"{path} does not look like an AnuranCall store (expected a JSON "
                f"object) — move it aside, e.g. mv {path} {path}.bak"
            )
        sessions = document.get("sessions")
        if not isinstance(sessions, list):
            raise AnuranError(
                f"{path} has no 'sessions' list — move it aside, e.g. mv {path} {path}.bak"
            )
        version = document.get("schema_version", SCHEMA_VERSION)
        if not isinstance(version, int) or version > SCHEMA_VERSION:
            raise AnuranError(
                f"{path} was written by a newer AnuranCall (schema_version "
                f"{version!r}) — upgrade AnuranCall to read it"
            )
        for entry in sessions:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                raise AnuranError(
                    f"{path} has a session entry without an id — fix it by hand or "
                    f"move the file aside, e.g. mv {path} {path}.bak"
                )
        return cls(path, sessions)

    def _temp_path(self) -> Path:
        """The sibling temp file, confined to the store's own directory.

        The store path arrives from a CLI flag or an environment variable, so
        the write target is resolved against the fixed base directory that
        holds the store and refused if it does not sit directly inside it.
        """
        base = self.path.parent.resolve()
        if self.path.resolve().parent != base:
            raise AnuranError(
                f"refusing to write {self.path} — it is not directly inside {base}"
            )
        sibling = base / f"{self.path.name}.tmp"
        if sibling.is_symlink():
            raise AnuranError(f"refusing to write {sibling} — the temp path is a symlink")
        candidate = sibling.resolve()
        if candidate.parent != base or candidate.name != sibling.name:
            raise AnuranError(
                f"refusing to write {candidate} — it escapes the data directory {base}"
            )
        return candidate

    def save(self) -> None:
        document = {"schema_version": SCHEMA_VERSION, "sessions": self.sessions}
        payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        tmp_path = self._temp_path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except OSError as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError as cleanup_error:
                    warn(f"warning: could not remove {tmp_path}: {cleanup_error}")
            raise AnuranError(
                f"cannot write {self.path}: {exc.strerror or exc}"
            ) from exc

    def add(self, session: dict[str, Any]) -> None:
        self.sessions.append(session)

    def find(self, session_id: str) -> dict[str, Any]:
        for session in self.sessions:
            if session.get("id") == session_id:
                return session
        raise AnuranError(
            f"no session with id {session_id!r} — run 'sessions' to list the ids you have"
        )

    def remove(self, session_id: str) -> dict[str, Any]:
        session = self.find(session_id)
        self.sessions.remove(session)
        return session


def new_session_id(when: date, taken: set[str]) -> str:
    # imported here so a plain read command never pays for it
    import uuid

    for _ in range(50):
        candidate = f"{when.isoformat()}-{uuid.uuid4().hex[:4]}"
        if candidate not in taken:
            return candidate
    raise AnuranError("could not allocate a session id — run the command again")


def build_session(
    *,
    session_id: str,
    species: str,
    site: str | None,
    temperature_c: float | None,
    notes: str | None,
    method: str,
    intervals: Sequence[float],
    call_count: int,
    started_at: datetime,
    stats: RateStats,
) -> dict[str, Any]:
    return {
        "id": session_id,
        "species": species,
        "site": site,
        "date": started_at.date().isoformat(),
        "started_at": started_at.isoformat(timespec="seconds"),
        "method": method,
        "intervals_s": [round(value, 4) for value in intervals],
        "call_count": call_count,
        "duration_s": round(stats.duration_s, 3),
        "mean_interval_s": round(stats.mean_interval_s, 4),
        "median_interval_s": round(stats.median_interval_s, 4),
        "min_interval_s": round(stats.min_interval_s, 3),
        "max_interval_s": round(stats.max_interval_s, 3),
        "drip_rate_per_min": round(stats.drip_rate_per_min, 2),
        "temperature_c": temperature_c,
        "notes": notes,
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
    }


# --------------------------------------------------------------------------- #
# filters and derived views
# --------------------------------------------------------------------------- #

def filter_sessions(
    sessions: Sequence[dict[str, Any]],
    *,
    species: str | None = None,
    site: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    needle_species = (species or "").strip().lower()
    needle_site = (site or "").strip().lower()
    start = date_from.isoformat() if date_from else None
    end = date_to.isoformat() if date_to else None
    matched: list[dict[str, Any]] = []
    for session in sessions:
        if needle_species and needle_species not in str(session.get("species", "")).lower():
            continue
        if needle_site and needle_site not in str(session.get("site") or "").lower():
            continue
        day = str(session.get("date") or "")
        if start and day < start:
            continue
        if end and day > end:
            continue
        matched.append(session)
    return matched


def sort_sessions(sessions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        sessions,
        key=lambda session: (
            str(session.get("started_at") or ""),
            str(session.get("id") or ""),
        ),
        reverse=True,
    )


def describe_filters(
    *,
    species: str | None = None,
    site: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> str:
    parts: list[str] = []
    if species:
        parts.append(f'species "{species}"')
    if site:
        parts.append(f'site "{site}"')
    if date_from:
        parts.append(f"from {date_from.isoformat()}")
    if date_to:
        parts.append(f"to {date_to.isoformat()}")
    return ", ".join(parts)


def build_phenology(sessions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    dates = sorted({str(s["date"]) for s in sessions if s.get("date")})
    species = sorted(
        {str(s["species"]) for s in sessions if s.get("species")}, key=str.lower
    )
    cells: dict[str, dict[str, int]] = {
        name: {day: 0 for day in dates} for name in species
    }
    totals = {name: 0 for name in species}
    for session in sessions:
        name = str(session.get("species") or "")
        day = str(session.get("date") or "")
        if name in cells and day in cells[name]:
            cells[name][day] += 1
            totals[name] += 1
    rows = [
        {"species": name, "counts": cells[name], "total": totals[name]}
        for name in species
    ]
    return {
        "dates": dates,
        "species": species,
        "cells": cells,
        "row_totals": totals,
        "rows": rows,
    }


def build_sites(sessions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        site = session.get("site")
        if not site:
            continue
        groups.setdefault(str(site), []).append(session)
    rows: list[dict[str, Any]] = []
    for name in sorted(groups, key=str.lower):
        group = sort_sessions(groups[name])
        temperature = next(
            (s.get("temperature_c") for s in group if s.get("temperature_c") is not None),
            None,
        )
        note = next((s.get("notes") for s in group if s.get("notes")), None)
        rows.append(
            {
                "name": name,
                "session_count": len(group),
                "last_temperature_c": temperature,
                "last_session_date": group[0].get("date"),
                "latest_note": note,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# session rendering
# --------------------------------------------------------------------------- #

def field_entries(session: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """(label, value, is_primary) rows shared by the count summary and the detail view."""
    temperature = session.get("temperature_c")
    rate = float(session.get("drip_rate_per_min") or 0.0)
    return [
        ("species", str(session.get("species") or EM_DASH), False),
        ("site", str(session.get("site") or EM_DASH), False),
        ("date", str(session.get("date") or EM_DASH), False),
        ("started", str(session.get("started_at") or EM_DASH), False),
        ("method", str(session.get("method") or EM_DASH), False),
        ("temperature", fmt_temp(temperature), False),
        ("calls", str(int(session.get("call_count") or 0)), False),
        ("duration", f"{float(session.get('duration_s') or 0.0):.2f} s", False),
        (
            "mean interval",
            f"{float(session.get('mean_interval_s') or 0.0):.2f} s",
            False,
        ),
        (
            "median interval",
            f"{float(session.get('median_interval_s') or 0.0):.2f} s",
            False,
        ),
        (
            "min / max",
            f"{float(session.get('min_interval_s') or 0.0):.2f} / "
            f"{float(session.get('max_interval_s') or 0.0):.2f} s",
            False,
        ),
        ("drip rate", f"{rate:.1f} calls/min", True),
        ("notes", str(session.get("notes") or EM_DASH), False),
    ]


def render_field_block(session: dict[str, Any], heading: str) -> list[str]:
    entries = field_entries(session)
    label_width = max(len(label) for label, _, _ in entries)
    value_width = MAX_WIDTH - 4 - label_width
    lines = [heading]
    for label, value, primary in entries:
        wrapped = wrap_text(value, value_width) or [EM_DASH]
        head = STYLE.accent(wrapped[0]) if primary else wrapped[0]
        lines.append(f"  {label.ljust(label_width)}  {head}")
        for extra in wrapped[1:]:
            lines.append(f"  {' ' * label_width}  {extra}")
    return lines


def render_interval_series(intervals: Sequence[float], per_line: int = 8) -> list[str]:
    if not intervals:
        return ["  (no intervals recorded)"]
    lines = []
    for start in range(0, len(intervals), per_line):
        chunk = intervals[start : start + per_line]
        lines.append("  " + "  ".join(f"{float(value):6.2f}" for value in chunk))
    return lines


def table_width(lines: Sequence[str]) -> int:
    return max((len(line) for line in lines), default=0)


def render_phenology_table(view: dict[str, Any]) -> tuple[list[str], str | None]:
    """Render the grid so it never exceeds MAX_WIDTH; returns (lines, note)."""
    dates: list[str] = view["dates"]
    names: list[str] = view["species"]
    cells: dict[str, dict[str, int]] = view["cells"]

    def build(window: Sequence[str], labels: Sequence[str]) -> list[str]:
        totals = {name: sum(cells[name][day] for day in window) for name in names}
        headers = ["species", *labels, "total"]
        rows = [
            [name, *(str(cells[name][day]) for day in window), str(totals[name])]
            for name in names
        ]
        return render_table(
            headers,
            rows,
            aligns=["left", *(["right"] * len(window)), "right"],
            caps=[26, *([10] * len(window)), 7],
        )

    short_labels = [day[5:] for day in dates]
    for window, labels, note in (
        (dates, list(dates), None),
        (dates, short_labels, "dates shown as MM-DD"),
    ):
        lines = build(window, labels)
        if table_width(lines) <= MAX_WIDTH:
            return lines, note

    # More dates than fit at 100 columns: keep the newest ones and say so.
    room = MAX_WIDTH - (26 + 7 + 2 * 2)
    visible = max(2, room // (len("MM-DD") + 2))
    window = dates[-visible:]
    lines = build(window, [day[5:] for day in window])
    note = (
        f"dates shown as MM-DD; showing the newest {len(window)} of {len(dates)} "
        f"dates — narrow the window with --from/--to"
    )
    return lines, note


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_count(args: argparse.Namespace, path: Path) -> int:
    species = require_species(args.species)
    site = clean_text(args.site, limit=80)
    notes = clean_text(args.notes)
    temperature = parse_temperature(args.temp)
    require_site_for_temperature(site, temperature)

    today = date.today()
    survey_date = parse_iso_date(args.date, "--date") if args.date else today
    if survey_date > today:
        raise AnuranError(
            f"--date {survey_date.isoformat()} is in the future — record today or an "
            f"earlier survey date"
        )
    started_at = datetime.combine(
        survey_date, datetime.now().replace(microsecond=0).time()
    )

    place = f" at {site}" if site else ""
    if args.intervals is not None:
        method = "intervals"
        intervals = parse_intervals(args.intervals)
        call_count = len(intervals)
        if not args.json:
            emit(f"count  {species}{place}")
            emit(f"  pasted {call_count} intervals (method: intervals)")
            emit()
    else:
        method = "tap"
        if not args.json:
            emit(f"count  {species}{place}")
            emit("  press enter once per call, then q and enter to finish")
            emit()

        def on_tap(calls: int, interval: float | None) -> None:
            if args.json:
                return
            if interval is None:
                emit(f"call {calls}")
            else:
                emit(f"call {calls}  +{interval:.2f} s")

        bout = collect_taps(stdin_reader, time.monotonic, on_tap)
        intervals = bout.intervals
        call_count = bout.call_count
        if not args.json:
            emit()
        if call_count < 2:
            raise AnuranError(
                "a rate needs at least two calls — nothing was saved; run count again "
                "and press enter for each call"
            )

    stats = compute_stats(intervals, call_count)
    store = Store.load(path)
    session_id = new_session_id(survey_date, {str(s.get("id")) for s in store.sessions})
    session = build_session(
        session_id=session_id,
        species=species,
        site=site,
        temperature_c=temperature,
        notes=notes,
        method=method,
        intervals=intervals,
        call_count=call_count,
        started_at=started_at,
        stats=stats,
    )
    store.add(session)
    store.save()

    if args.json:
        emit(json.dumps(session, ensure_ascii=False, indent=2))
        return 0

    for line in render_field_block(session, heading="count summary"):
        emit(line)
    emit()
    emit(f"saved session {STYLE.accent(session['id'])}")
    emit(f"  file  {display_path(path)}")
    return 0


def cmd_sessions(args: argparse.Namespace, path: Path) -> int:
    store = Store.load(path)
    date_from = parse_iso_date(args.date_from, "--from") if args.date_from else None
    date_to = parse_iso_date(args.date_to, "--to") if args.date_to else None
    if date_from and date_to and date_from > date_to:
        raise AnuranError(f"--from {date_from.isoformat()} is after --to {date_to.isoformat()}")
    matched = sort_sessions(
        filter_sessions(
            store.sessions,
            species=args.species,
            site=args.site,
            date_from=date_from,
            date_to=date_to,
        )
    )

    if args.json:
        payload = {
            "data_file": str(path),
            "schema_version": SCHEMA_VERSION,
            "total_sessions": len(store.sessions),
            "count": len(matched),
            "filters": {
                "species": args.species,
                "site": args.site,
                "from": date_from.isoformat() if date_from else None,
                "to": date_to.isoformat() if date_to else None,
            },
            "sessions": matched,
        }
        emit(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not matched:
        emit("sessions")
        if store.sessions:
            emit(f"  no sessions match those filters ({describe_filters(species=args.species, site=args.site, date_from=date_from, date_to=date_to)})")
            emit(
                f"  {plural(len(store.sessions), 'session')} in the store — run "
                f"'sessions' with no filters to see them"
            )
        else:
            emit("  no sessions yet — the log fills in as you record counts")
            emit("  record your first count with:")
            emit(
                "    "
                + command_hint(HINT_COUNT)
            )
            emit("  (leave --intervals off to tap enter once per call)")
        return 0

    header = f"sessions  {len(matched)} of {len(store.sessions)} matched"
    emit(header)
    filters = describe_filters(
        species=args.species, site=args.site, date_from=date_from, date_to=date_to
    )
    if filters:
        emit(f"  filters: {filters}")
    emit()
    rows = [
        [
            str(session.get("date") or EM_DASH),
            str(session.get("id") or EM_DASH),
            str(session.get("species") or EM_DASH),
            str(session.get("site") or EM_DASH),
            fmt_temp(session.get("temperature_c")),
            fmt_rate(session.get("drip_rate_per_min") or 0.0),
        ]
        for session in matched
    ]
    lines = render_table(
        ["date", "id", "species", "site", "temp", "calls/min"],
        rows,
        aligns=["left", "left", "left", "left", "right", "right"],
        caps=[10, 17, 22, 20, 8, 9],
    )
    for line in lines:
        emit(line)
    return 0


def cmd_show(args: argparse.Namespace, path: Path) -> int:
    store = Store.load(path)
    session = store.find(args.id)
    if args.json:
        emit(json.dumps(session, ensure_ascii=False, indent=2))
        return 0
    for line in render_field_block(session, heading=f"session {session.get('id')}"):
        emit(line)
    emit()
    emit("  intervals (s)")
    for line in render_interval_series(session.get("intervals_s") or []):
        emit(line)
    emit()
    emit(f"  file  {display_path(path)}")
    return 0


def cmd_phenology(args: argparse.Namespace, path: Path) -> int:
    store = Store.load(path)
    date_from = parse_iso_date(args.date_from, "--from") if args.date_from else None
    date_to = parse_iso_date(args.date_to, "--to") if args.date_to else None
    if date_from and date_to and date_from > date_to:
        raise AnuranError(f"--from {date_from.isoformat()} is after --to {date_to.isoformat()}")
    matched = filter_sessions(
        store.sessions, site=args.site, date_from=date_from, date_to=date_to
    )
    view = build_phenology(matched)

    if args.json:
        payload = {
            "data_file": str(path),
            "filters": {
                "site": args.site,
                "from": date_from.isoformat() if date_from else None,
                "to": date_to.isoformat() if date_to else None,
            },
            "dates": view["dates"],
            "species": view["species"],
            "cells": view["cells"],
            "row_totals": view["row_totals"],
            "rows": view["rows"],
        }
        emit(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not matched:
        emit("phenology")
        filters = describe_filters(
            site=args.site, date_from=date_from, date_to=date_to
        )
        if store.sessions:
            emit(f"  no sessions in that window ({filters})")
        else:
            emit("  no sessions recorded yet — the table fills in as you count")
        emit()
        emit("  record a count with:")
        emit(
            "    "
            + command_hint(HINT_COUNT)
        )
        return 0

    emit("phenology")
    filters = describe_filters(site=args.site, date_from=date_from, date_to=date_to)
    emit(f"  filters: {filters}" if filters else "  filters: none (all sites, all dates)")
    emit(
        f"  {plural(len(view['species']), 'species', 'species')} across "
        f"{plural(len(view['dates']), 'date')}"
    )
    emit()
    lines, note = render_phenology_table(view)
    for line in lines:
        emit(line)
    if note:
        emit()
        emit(f"  {note}")
    return 0


def cmd_sites(args: argparse.Namespace, path: Path) -> int:
    store = Store.load(path)
    rows = build_sites(store.sessions)
    if args.json:
        emit(
            json.dumps(
                {"data_file": str(path), "sites": rows},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not rows:
        emit("sites")
        emit("  no sites yet — a site appears once a session records one")
        emit("  record a count at a pond with:")
        emit(
            "    "
            + command_hint(HINT_COUNT_WITH_TEMP)
        )
        return 0

    emit(f"sites  {len(rows)}")
    emit()
    note_width = MAX_WIDTH - (22 + 8 + 10 + 10 + 3 * 2) - 2
    table_rows = [
        [
            row["name"],
            str(row["session_count"]),
            fmt_temp(row["last_temperature_c"]),
            str(row["last_session_date"] or EM_DASH),
            str(row["latest_note"] or EM_DASH),
        ]
        for row in rows
    ]
    lines = render_table(
        ["site", "sessions", "temp", "latest", "newest note"],
        table_rows,
        aligns=["left", "right", "right", "left", "left"],
        caps=[22, 8, 10, 10, note_width],
        wrap_last=note_width,
    )
    for line in lines:
        emit(line)
    return 0


def cmd_edit(args: argparse.Namespace, path: Path) -> int:
    store = Store.load(path)
    session = store.find(args.id)
    site = session.get("site")
    temperature = session.get("temperature_c")
    notes = session.get("notes")
    changed = False
    if args.site is not None:
        site = clean_text(args.site, limit=80)
        changed = True
    if args.temp is not None:
        temperature = parse_temperature(args.temp)
        changed = True
    if args.notes is not None:
        notes = clean_text(args.notes)
        changed = True
    if not changed:
        raise AnuranError("nothing to change — pass --site, --temp or --notes")
    require_site_for_temperature(site, temperature)
    session["site"] = site
    session["temperature_c"] = temperature
    session["notes"] = notes
    session["updated_at"] = datetime.now().replace(microsecond=0).isoformat()
    store.save()
    emit(f"updated session {session['id']}")
    for line in render_field_block(session, heading="session"):
        emit(line)
    return 0


def cmd_delete(args: argparse.Namespace, path: Path) -> int:
    store = Store.load(path)
    session = store.find(args.id)
    if not args.yes:
        answer = read_confirmation(
            f"delete session {args.id} ({session.get('species')}, {session.get('date')})? [y/N] "
        )
        if answer is None or answer.strip().lower() not in {"y", "yes"}:
            emit("not deleted — session kept")
            return 0
    store.remove(args.id)
    store.save()
    emit(f"deleted {args.id}")
    return 0


# --------------------------------------------------------------------------- #
# argparse wiring
# --------------------------------------------------------------------------- #

EXAMPLES = """\
examples
  count --species 'Spring Peeper' --site 'Mud Pond' --intervals '1.0, 1.2, 0.8'
  count --species 'Wood Frog' --site 'Beaver Marsh' --temp 12.0 --notes 'two males'
  sessions --species 'Spring Peeper' --from 2024-05-01 --to 2024-05-31
  show 2024-05-12-3f9a
  phenology --site 'Mud Pond'
  sites --json
"""


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=f"session store (default: ${DATA_ENV_VAR} or {default_data_path()})",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="disable ANSI colour (NO_COLOR is honoured too)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog_name(),
        description=(
            "AnuranCall — turn frog call taps or pasted intervals into a drip rate, "
            "log the pond conditions, and read the survey back."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"anurancall {VERSION}")
    add_common_flags(parser)
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    count = subparsers.add_parser(
        "count",
        help="record a call session (tap enter per call, or paste intervals)",
        description=(
            "Record a call session. Without --intervals the tool reads one tap per "
            "entered line until you type q (or close stdin)."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_flags(count)
    count.add_argument("--species", required=True, help="species heard, e.g. 'Spring Peeper'")
    count.add_argument("--site", help="pond name, e.g. 'Mud Pond'")
    count.add_argument("--temp", metavar="C", help=f"temperature in C ({TEMP_MIN_C:g} to {TEMP_MAX_C:g})")
    count.add_argument("--notes", help="free-text note stored with the session")
    count.add_argument(
        "--intervals",
        help="pasted inter-call intervals in seconds, e.g. '1.0, 1.2, 0.8' (skips tapping)",
    )
    count.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="survey date when transcribing a field sheet (default: today)",
    )
    count.add_argument("--json", action="store_true", help="print the saved session as one JSON object")
    count.set_defaults(func=cmd_count)

    sessions = subparsers.add_parser(
        "sessions", help="list saved sessions, newest first"
    )
    add_common_flags(sessions)
    sessions.add_argument("--species", help="only sessions whose species contains this text")
    sessions.add_argument("--site", help="only sessions whose site contains this text")
    sessions.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="earliest survey date")
    sessions.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="latest survey date")
    sessions.add_argument("--json", action="store_true", help="print one JSON object")
    sessions.set_defaults(func=cmd_sessions)

    show = subparsers.add_parser("show", help="print every field of one session")
    add_common_flags(show)
    show.add_argument("id", help="session id, e.g. 2024-05-12-3f9a")
    show.add_argument("--json", action="store_true", help="print one JSON object")
    show.set_defaults(func=cmd_show)

    edit = subparsers.add_parser("edit", help="correct the site, temperature or note of a session")
    add_common_flags(edit)
    edit.add_argument("id", help="session id to correct")
    edit.add_argument("--site", help="replacement pond name (pass '' to clear)")
    edit.add_argument("--temp", metavar="C", help="replacement temperature (pass none to clear)")
    edit.add_argument("--notes", help="replacement note (pass '' to clear)")
    edit.set_defaults(func=cmd_edit)

    delete = subparsers.add_parser("delete", help="remove a session after a y/N confirmation")
    add_common_flags(delete)
    delete.add_argument("id", help="session id to remove")
    delete.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    delete.set_defaults(func=cmd_delete)

    phenology = subparsers.add_parser(
        "phenology", help="species-by-date table of session counts"
    )
    add_common_flags(phenology)
    phenology.add_argument("--site", help="only sessions at this pond")
    phenology.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="earliest survey date")
    phenology.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="latest survey date")
    phenology.add_argument("--json", action="store_true", help="print one JSON object")
    phenology.set_defaults(func=cmd_phenology)

    sites = subparsers.add_parser("sites", help="per-pond summary of surveys and conditions")
    add_common_flags(sites)
    sites.add_argument("--json", action="store_true", help="print one JSON object")
    sites.set_defaults(func=cmd_sites)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not raw_args:
        parser.print_help()
        return 0
    args = parser.parse_args(raw_args)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    install_style(Style(color_enabled(bool(getattr(args, "no_color", False)))))
    try:
        path = resolve_data_path(getattr(args, "data", None))
        return int(args.func(args, path))
    except AnuranError as exc:
        warn(f"{prog_name()}: error: {exc.message}")
        return exc.exit_code
    except KeyboardInterrupt:
        warn(f"{prog_name()}: aborted")
        return 130
    except BrokenPipeError:
        # `... | head` closed the pipe; exit quietly without a shutdown traceback.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError as exc:
            warn(f"warning: could not detach stdout after a closed pipe: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())

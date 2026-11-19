"""Tests for AnuranCall: interval parsing, drip-rate maths, the JSON store,
the tap counter, phenology grouping, the site summary and the CLI contract.

Run with either:
    python3 -m unittest discover tests
    python3 -m pytest tests
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import anurancall  # noqa: E402

SCRIPT = ROOT / "anurancall.py"


def run_cli(*args: str, data: Path | None = None, stdin: str = "", env: dict | None = None):
    command = [sys.executable, str(SCRIPT)]
    if data is not None:
        command += ["--data", str(data)]
    command += list(args)
    child_env = dict(os.environ)
    child_env.pop("ANURANCALL_DATA", None)
    if env:
        child_env.update(env)
    return subprocess.run(
        command, input=stdin, capture_output=True, text=True, timeout=120, env=child_env
    )


def sample_session(
    session_id: str,
    *,
    species: str = "Spring Peeper",
    site: str | None = "Mud Pond",
    day: str = "2024-05-12",
    started_at: str = "2024-05-12T20:14:03",
    temperature: float | None = 17.5,
    notes: str | None = "full chorus along the north shore",
    rate: float = 58.5,
) -> dict:
    return {
        "id": session_id,
        "species": species,
        "site": site,
        "date": day,
        "started_at": started_at,
        "method": "intervals",
        "intervals_s": [1.0, 1.2, 0.8, 1.1],
        "call_count": 4,
        "duration_s": 4.1,
        "mean_interval_s": 1.025,
        "median_interval_s": 1.05,
        "min_interval_s": 0.8,
        "max_interval_s": 1.2,
        "drip_rate_per_min": rate,
        "temperature_c": temperature,
        "notes": notes,
        "created_at": "2024-05-12T20:15:00",
    }


def write_store(path: Path, sessions: list[dict]) -> Path:
    path.write_text(
        json.dumps({"schema_version": 1, "sessions": sessions}, indent=2),
        encoding="utf-8",
    )
    return path


class IntervalParsingTests(unittest.TestCase):
    def test_comma_separated(self):
        self.assertEqual(anurancall.parse_intervals("1.0, 1.2, 0.8"), [1.0, 1.2, 0.8])

    def test_space_separated(self):
        self.assertEqual(anurancall.parse_intervals("1.0 1.2 0.8"), [1.0, 1.2, 0.8])

    def test_mixed_separators_and_padding(self):
        self.assertEqual(
            anurancall.parse_intervals("  1.0 ,1.2   0.8 , "), [1.0, 1.2, 0.8]
        )

    def test_word_named_in_error(self):
        with self.assertRaises(anurancall.AnuranError) as raised:
            anurancall.parse_intervals("1.0, zero, 0.8")
        self.assertIn("'zero'", raised.exception.message)
        self.assertIn("not a number", raised.exception.message)

    def test_zero_rejected(self):
        with self.assertRaises(anurancall.AnuranError) as raised:
            anurancall.parse_intervals("1.0, 0, 0.8")
        self.assertIn("'0'", raised.exception.message)
        self.assertIn("positive", raised.exception.message)

    def test_negative_rejected(self):
        with self.assertRaises(anurancall.AnuranError):
            anurancall.parse_intervals("1.0, -0.4")

    def test_single_value_is_not_a_rate(self):
        with self.assertRaises(anurancall.AnuranError) as raised:
            anurancall.parse_intervals("1.0")
        self.assertIn("at least two calls", raised.exception.message)

    def test_empty_rejected(self):
        with self.assertRaises(anurancall.AnuranError):
            anurancall.parse_intervals("   ")


class RateMathTests(unittest.TestCase):
    def test_mean_median_and_drip_rate(self):
        stats = anurancall.compute_stats([1.0, 1.2, 0.8, 1.1], call_count=4)
        self.assertAlmostEqual(stats.mean_interval_s, 1.025)
        self.assertAlmostEqual(stats.median_interval_s, 1.05)
        self.assertAlmostEqual(stats.duration_s, 4.1)
        self.assertAlmostEqual(stats.min_interval_s, 0.8)
        self.assertAlmostEqual(stats.max_interval_s, 1.2)
        self.assertAlmostEqual(stats.drip_rate_per_min, 60 / 1.025, places=6)
        self.assertAlmostEqual(stats.drip_rate_per_min, 58.54, places=2)

    def test_half_second_intervals_are_120_per_minute(self):
        stats = anurancall.compute_stats([0.5, 0.5], call_count=3)
        self.assertAlmostEqual(stats.drip_rate_per_min, 120.0)

    def test_no_intervals_is_an_error(self):
        with self.assertRaises(anurancall.AnuranError):
            anurancall.compute_stats([], call_count=1)


class TapCounterTests(unittest.TestCase):
    def test_three_taps_two_intervals(self):
        clock = iter([10.0, 10.5, 11.0]).__next__
        lines = iter(["\n", "\n", "\n", "q\n"]).__next__
        seen: list[tuple[int, float | None]] = []
        bout = anurancall.collect_taps(lines, clock, lambda n, gap: seen.append((n, gap)))
        self.assertEqual(bout.call_count, 3)
        self.assertEqual(len(bout.intervals), 2)
        self.assertAlmostEqual(bout.intervals[0], 0.5)
        self.assertAlmostEqual(bout.intervals[1], 0.5)
        self.assertEqual(seen[0], (1, None))
        self.assertAlmostEqual(seen[1][1], 0.5)

    def test_end_of_input_finishes_bout(self):
        clock = iter([1.0, 1.25]).__next__
        readings = iter(["\n", "\n", None]).__next__
        bout = anurancall.collect_taps(readings, clock, lambda n, gap: None)
        self.assertEqual(bout.call_count, 2)
        self.assertAlmostEqual(bout.intervals[0], 0.25)

    def test_quit_words_finish_bout(self):
        clock = iter([1.0, 1.5]).__next__
        readings = iter(["\n", "\n", "Q\n", "\n", "\n"]).__next__
        bout = anurancall.collect_taps(readings, clock, lambda n, gap: None)
        self.assertEqual(bout.call_count, 2)

    def test_nothing_tapped(self):
        bout = anurancall.collect_taps(iter([None]).__next__, iter([1.0]).__next__, lambda n, g: None)
        self.assertEqual(bout.call_count, 0)
        self.assertEqual(bout.intervals, [])


class TapCommandTests(unittest.TestCase):
    """The tap path end-to-end in-process, with a fake clock and fake stdin."""

    def _count(self, tmpdir: Path, ticks: list[float], lines: list[str], *, as_json: bool):
        data = tmpdir / "sessions.json"
        args = argparse.Namespace(
            species="Spring Peeper",
            site="Mud Pond",
            temp="17.5",
            notes="full chorus",
            intervals=None,
            date=None,
            json=as_json,
        )
        tick_iter = iter(ticks)
        line_iter = iter(lines)

        def fake_readline() -> str:
            try:
                return next(line_iter)
            except StopIteration:
                return ""

        buffer = io.StringIO()
        with mock.patch.object(anurancall.time, "monotonic", lambda: next(tick_iter)), mock.patch.object(
            anurancall.sys, "stdin", SimpleNamespace(readline=fake_readline)
        ), mock.patch.object(anurancall.sys, "stdout", buffer):
            try:
                rc = anurancall.cmd_count(args, data)
            except anurancall.AnuranError as exc:
                # main() turns this into one plain stderr line plus a non-zero exit
                return exc.exit_code, exc.message, data
        return rc, buffer.getvalue(), data

    def test_tap_run_saves_a_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, output, data = self._count(
                Path(tmp), [20.0, 20.5, 21.0], ["\n", "\n", "\n", "q\n"], as_json=True
            )
            self.assertEqual(rc, 0)
            session = json.loads(output)
            self.assertEqual(session["method"], "tap")
            self.assertEqual(session["call_count"], 3)
            self.assertAlmostEqual(session["drip_rate_per_min"], 120.0, places=1)
            self.assertEqual(session["temperature_c"], 17.5)
            stored = json.loads(data.read_text(encoding="utf-8"))
            self.assertEqual(stored["sessions"][0]["id"], session["id"])

    def test_single_tap_is_refused_and_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, output, data = self._count(Path(tmp), [30.0], ["\n", "q\n"], as_json=False)
            self.assertEqual(rc, 1)
            self.assertIn("at least two calls", output)
            self.assertFalse(data.exists())


class StoreTests(unittest.TestCase):
    def test_missing_file_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = anurancall.Store.load(Path(tmp) / "sessions.json")
            self.assertEqual(store.sessions, [])

    def test_round_trip_is_atomic_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "sessions.json"
            store = anurancall.Store.load(path)
            store.add(sample_session("2024-05-12-abcd"))
            store.save()

            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(len(document["sessions"]), 1)
            self.assertEqual(path.read_text(encoding="utf-8")[-1], "\n")
            leftovers = sorted(p.name for p in path.parent.iterdir())
            self.assertEqual(leftovers, ["sessions.json"])

            reloaded = anurancall.Store.load(path)
            self.assertEqual(reloaded.sessions[0]["species"], "Spring Peeper")

    def test_corrupt_file_names_the_path_and_suggests_a_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            path.write_text("not json at all\n", encoding="utf-8")
            with self.assertRaises(anurancall.AnuranError) as raised:
                anurancall.Store.load(path)
            message = raised.exception.message
            self.assertIn(str(path), message)
            self.assertIn("mv", message)
            self.assertIn("not valid JSON", message)

    def test_unknown_id_raises_plain_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = anurancall.Store.load(Path(tmp) / "sessions.json")
            with self.assertRaises(anurancall.AnuranError) as raised:
                store.find("nope")
            self.assertIn("no session with id", raised.exception.message)

    def test_remove_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_store(Path(tmp) / "sessions.json", [sample_session("2024-05-12-abcd")])
            store = anurancall.Store.load(path)
            store.remove("2024-05-12-abcd")
            store.save()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["sessions"], [])

    def test_cli_data_flag_beats_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "from-env.json"
            cli_path = Path(tmp) / "from-flag.json"
            with mock.patch.dict(os.environ, {"ANURANCALL_DATA": str(env_path)}):
                self.assertEqual(anurancall.resolve_data_path(None), env_path)
                self.assertEqual(anurancall.resolve_data_path(str(cli_path)), cli_path)

    def test_parent_segment_in_the_data_path_is_refused(self):
        with self.assertRaises(anurancall.AnuranError) as raised:
            anurancall.resolve_data_path("../somewhere/sessions.json")
        self.assertIn("'..'", raised.exception.message)

    def test_data_path_pointing_at_a_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(anurancall.AnuranError) as raised:
                anurancall.resolve_data_path(tmp)
            self.assertIn("is a directory", raised.exception.message)

    def test_data_path_is_resolved_to_one_absolute_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = anurancall.resolve_data_path(f"{tmp}/./pond-2024.json")
            self.assertTrue(resolved.is_absolute())
            self.assertEqual(str(resolved), str((Path(tmp) / "pond-2024.json").resolve()))

    def test_save_refuses_a_symlinked_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "elsewhere.json"
            target.write_text("keep me\n", encoding="utf-8")
            store = anurancall.Store.load(Path(tmp) / "sessions.json")
            store.add(sample_session("2024-05-12-abcd"))
            (Path(tmp) / "sessions.json.tmp").symlink_to(target)
            with self.assertRaises(anurancall.AnuranError) as raised:
                store.save()
            self.assertIn("refusing to write", raised.exception.message)
            self.assertIn("symlink", raised.exception.message)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep me\n")
            self.assertFalse((Path(tmp) / "sessions.json").exists())

    def test_empty_environment_variable_falls_back_to_home_default(self):
        with mock.patch.dict(os.environ, {"ANURANCALL_DATA": "  "}):
            self.assertEqual(anurancall.resolve_data_path(None), anurancall.default_data_path())


class PhenologyTests(unittest.TestCase):
    def setUp(self):
        self.sessions = [
            sample_session("2024-05-01-aaaa", species="Spring Peeper", day="2024-05-01", started_at="2024-05-01T20:00:00"),
            sample_session("2024-05-03-bbbb", species="Spring Peeper", day="2024-05-03", started_at="2024-05-03T20:00:00"),
            sample_session("2024-05-03-cccc", species="Spring Peeper", day="2024-05-03", started_at="2024-05-03T21:00:00"),
            sample_session("2024-05-01-dddd", species="Wood Frog", site="Beaver Marsh", day="2024-05-01", started_at="2024-05-01T21:00:00"),
        ]

    def test_cells_and_totals(self):
        view = anurancall.build_phenology(self.sessions)
        self.assertEqual(view["dates"], ["2024-05-01", "2024-05-03"])
        self.assertEqual(view["species"], ["Spring Peeper", "Wood Frog"])
        self.assertEqual(view["cells"]["Spring Peeper"]["2024-05-03"], 2)
        self.assertEqual(view["cells"]["Wood Frog"]["2024-05-03"], 0)
        self.assertEqual(view["row_totals"]["Spring Peeper"], 3)
        self.assertEqual(view["row_totals"]["Wood Frog"], 1)

    def test_site_filter_narrows_the_grid(self):
        filtered = anurancall.filter_sessions(self.sessions, site="Beaver Marsh")
        view = anurancall.build_phenology(filtered)
        self.assertEqual(view["species"], ["Wood Frog"])
        self.assertEqual(view["row_totals"]["Wood Frog"], 1)

    def test_date_window(self):
        filtered = anurancall.filter_sessions(
            self.sessions,
            date_from=anurancall.date(2024, 5, 3),
            date_to=anurancall.date(2024, 5, 3),
        )
        self.assertEqual(len(filtered), 2)


class SiteSummaryTests(unittest.TestCase):
    def test_latest_temperature_and_newest_note(self):
        sessions = [
            sample_session("2024-05-01-aaaa", day="2024-05-01", started_at="2024-05-01T20:00:00", temperature=15.0, notes="old note"),
            sample_session("2024-05-12-bbbb", day="2024-05-12", started_at="2024-05-12T20:00:00", temperature=17.5, notes="newest note"),
        ]
        rows = anurancall.build_sites(sessions)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Mud Pond")
        self.assertEqual(rows[0]["session_count"], 2)
        self.assertEqual(rows[0]["last_temperature_c"], 17.5)
        self.assertEqual(rows[0]["last_session_date"], "2024-05-12")
        self.assertEqual(rows[0]["latest_note"], "newest note")

    def test_site_without_temperature_keeps_a_dash(self):
        sessions = [sample_session("2024-05-01-aaaa", temperature=None, notes=None)]
        rows = anurancall.build_sites(sessions)
        self.assertIsNone(rows[0]["last_temperature_c"])
        lines = anurancall.render_table(
            ["site", "sessions", "temp", "latest", "newest note"],
            [[rows[0]["name"], "1", anurancall.fmt_temp(rows[0]["last_temperature_c"]), "2024-05-12", str(rows[0]["latest_note"] or anurancall.EM_DASH)]],
            aligns=["left", "right", "right", "left", "left"],
            caps=[22, 8, 10, 10, 40],
            wrap_last=40,
        )
        self.assertIn(anurancall.EM_DASH, "\n".join(lines))

    def test_sessions_without_a_site_are_skipped(self):
        sessions = [sample_session("2024-05-01-aaaa", site=None, temperature=None)]
        self.assertEqual(anurancall.build_sites(sessions), [])


class WidthBudgetTests(unittest.TestCase):
    """Every human view has to fit 100 columns, notes wrapped rather than cut."""

    def _wide_sessions(self) -> list[dict]:
        import datetime

        species = ["Spring Peeper", "Wood Frog", "American Bullfrog", "Gray Treefrog"]
        start = datetime.date(2024, 4, 1)
        sessions = []
        for week in range(12):
            for rank, name in enumerate(species):
                if (week + rank) % 3:
                    continue
                day = (start + datetime.timedelta(days=7 * week)).isoformat()
                sessions.append(
                    sample_session(
                        f"{day}-{week}{rank}aa",
                        species=name,
                        site="Mud Pond" if rank % 2 else "Beaver Marsh",
                        day=day,
                        started_at=f"{day}T20:00:00",
                        notes="shoreline survey with a long note " * 6,
                    )
                )
        return sessions

    def test_wide_phenology_is_trimmed_and_says_so(self):
        view = anurancall.build_phenology(self._wide_sessions())
        lines, note = anurancall.render_phenology_table(view)
        self.assertLessEqual(anurancall.table_width(lines), 100)
        self.assertIsNotNone(note)
        self.assertIn("newest", note)
        self.assertIn("--from", note)

    def test_narrow_phenology_uses_iso_dates_without_a_note(self):
        sessions = [
            sample_session("2024-05-01-aaaa", day="2024-05-01", started_at="2024-05-01T20:00:00"),
            sample_session("2024-05-03-bbbb", day="2024-05-03", started_at="2024-05-03T20:00:00"),
        ]
        lines, note = anurancall.render_phenology_table(anurancall.build_phenology(sessions))
        self.assertIsNone(note)
        self.assertIn("2024-05-01", "\n".join(lines))

    def test_every_cli_view_stays_within_100_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = self._wide_sessions()
            data = write_store(Path(tmp) / "sessions.json", sessions)
            for args in (
                ("sessions",),
                ("phenology",),
                ("sites",),
                ("sessions", "--species", "Gray Treefrog"),
                ("show", sessions[0]["id"]),
            ):
                result = run_cli(*args, data=data)
                self.assertEqual(result.returncode, 0, result.stderr)
                widest = max(len(line) for line in result.stdout.splitlines())
                self.assertLessEqual(widest, 100, f"{args} printed a {widest}-column line")


class CliTests(unittest.TestCase):
    def test_help_lists_every_subcommand(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        for command in ("count", "sessions", "show", "edit", "delete", "phenology", "sites"):
            self.assertIn(command, result.stdout)

    def test_bare_invocation_prints_usage_and_exits_zero(self):
        result = run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)

    def test_unknown_subcommand_exits_two_without_traceback(self):
        result = run_cli("frobnicate")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_interval_count_then_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "sessions.json"
            result = run_cli(
                "count",
                "--species", "Spring Peeper",
                "--site", "Mud Pond",
                "--temp", "17.5",
                "--notes", "full chorus along the north shore",
                "--intervals", "1.0, 1.2, 0.8, 1.1",
                data=data,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("58.5", result.stdout)
            self.assertIn("intervals", result.stdout)
            self.assertIn("saved session", result.stdout)
            self.assertIn("17.5 C", result.stdout)
            self.assertIn("full chorus along the north shore", result.stdout)
            self.assertNotIn("\x1b[", result.stdout)

            session_id = next(
                token for token in result.stdout.split() if token.count("-") == 3
            )
            shown = run_cli("show", session_id, data=data)
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertIn("17.5 C", shown.stdout)
            self.assertIn("58.5 calls/min", shown.stdout)

            listing = run_cli("sessions", data=data)
            self.assertEqual(listing.returncode, 0)
            self.assertIn("1 of 1 matched", listing.stdout)
            self.assertIn(session_id, listing.stdout)

    def test_sessions_json_is_one_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(
                Path(tmp) / "sessions.json",
                [sample_session("2024-05-12-abcd")],
            )
            result = run_cli("sessions", "--json", data=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["sessions"][0]["id"], "2024-05-12-abcd")
            self.assertNotIn("\x1b[", result.stdout)

    def test_phenology_json_on_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "sessions.json"
            result = run_cli("phenology", "--json", data=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["dates"], [])
            self.assertEqual(payload["rows"], [])
            self.assertEqual(payload["species"], [])

    def test_count_json_echoes_the_saved_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "sessions.json"
            result = run_cli(
                "count",
                "--species", "Wood Frog",
                "--site", "Beaver Marsh",
                "--intervals", "2.0, 2.4, 1.8",
                "--json",
                data=data,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["species"], "Wood Frog")
            self.assertEqual(payload["method"], "intervals")
            self.assertNotIn("count summary", result.stdout)

    def test_out_of_range_temperature_is_refused_and_nothing_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "sessions.json"
            result = run_cli(
                "count", "--species", "Spring Peeper", "--site", "Mud Pond",
                "--temp", "99", "--intervals", "1.0, 1.2", data=data,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("between -5 and 45 C", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(data.exists())

    def test_temperature_without_site_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "sessions.json"
            result = run_cli(
                "count", "--species", "Spring Peeper", "--temp", "17.5",
                "--intervals", "1.0, 1.2", data=data,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("needs --site", result.stderr)
            self.assertFalse(data.exists())

    def test_bad_interval_names_the_token_and_saves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "sessions.json"
            result = run_cli(
                "count", "--species", "Spring Peeper", "--intervals", "1.0, zero",
                data=data,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("'zero'", result.stderr)
            self.assertFalse(data.exists())

    def test_missing_species_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("count", "--intervals", "1.0, 1.2", data=Path(tmp) / "s.json")
            self.assertEqual(result.returncode, 2)
            self.assertIn("--species", result.stderr)

    def test_unknown_id_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(Path(tmp) / "sessions.json", [])
            result = run_cli("show", "nonexistent-id", data=data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no session with id 'nonexistent-id'", result.stderr)

    def test_corrupt_store_is_one_line_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "sessions.json"
            data.write_text("<<< not json >>>\n", encoding="utf-8")
            result = run_cli("sessions", data=data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(data), result.stderr)
            self.assertIn("mv", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_sessions_filters_narrow_the_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(
                Path(tmp) / "sessions.json",
                [
                    sample_session("2024-05-12-aaaa", species="Spring Peeper", site="Mud Pond"),
                    sample_session("2024-05-12-bbbb", species="Wood Frog", site="Beaver Marsh"),
                    sample_session("2024-05-02-cccc", species="Spring Peeper", site="Mud Pond", day="2024-05-02", started_at="2024-05-02T21:00:00"),
                ],
            )
            result = run_cli("sessions", "--species", "Spring Peeper", data=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("2 of 3 matched", result.stdout)
            self.assertNotIn("Wood Frog", result.stdout)

            windowed = run_cli("sessions", "--from", "2024-05-10", "--to", "2024-05-31", data=data)
            self.assertIn("2 of 3 matched", windowed.stdout)

    def test_empty_store_invites_a_first_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "sessions.json"
            result = run_cli("sessions", data=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no sessions yet", result.stdout)
            self.assertIn("count --species", result.stdout)
            sites = run_cli("sites", data=data)
            self.assertEqual(sites.returncode, 0)
            self.assertIn("--site", sites.stdout)

    def test_no_match_is_distinct_from_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(Path(tmp) / "sessions.json", [sample_session("2024-05-12-abcd")])
            result = run_cli("sessions", "--species", "Bullfrog", data=data)
            self.assertEqual(result.returncode, 0)
            self.assertIn("no sessions match those filters", result.stdout)
            self.assertIn("1 session in the store", result.stdout)

    def test_phenology_table_has_totals_and_aligned_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(
                Path(tmp) / "sessions.json",
                [
                    sample_session("2024-05-01-aaaa", species="Spring Peeper", day="2024-05-01", started_at="2024-05-01T20:00:00"),
                    sample_session("2024-05-03-bbbb", species="Spring Peeper", day="2024-05-03", started_at="2024-05-03T20:00:00"),
                    sample_session("2024-05-01-cccc", species="Wood Frog", site="Beaver Marsh", day="2024-05-01", started_at="2024-05-01T21:00:00"),
                ],
            )
            result = run_cli("phenology", data=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("2024-05-01", result.stdout)
            self.assertIn("2024-05-03", result.stdout)
            self.assertIn("total", result.stdout)
            self.assertNotIn("\x1b[", result.stdout)
            for line in result.stdout.splitlines():
                self.assertLessEqual(len(line), 100)

    def test_phenology_site_filter_names_the_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(
                Path(tmp) / "sessions.json",
                [
                    sample_session("2024-05-01-aaaa", species="Spring Peeper", site="Mud Pond"),
                    sample_session("2024-05-01-bbbb", species="Wood Frog", site="Beaver Marsh"),
                ],
            )
            result = run_cli("phenology", "--site", "Mud Pond", data=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('site "Mud Pond"', result.stdout)
            self.assertNotIn("Wood Frog", result.stdout)

    def test_sites_view_shows_counts_and_dash_for_missing_temperature(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(
                Path(tmp) / "sessions.json",
                [
                    sample_session("2024-05-12-aaaa", site="Mud Pond", temperature=17.5),
                    sample_session("2024-05-10-bbbb", site="Quarry Pool", temperature=None, notes=None),
                ],
            )
            result = run_cli("sites", data=data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Mud Pond", result.stdout)
            self.assertIn("Quarry Pool", result.stdout)
            self.assertIn("17.5 C", result.stdout)
            self.assertIn(anurancall.EM_DASH, result.stdout)

    def test_edit_corrects_a_temperature(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(Path(tmp) / "sessions.json", [sample_session("2024-05-12-abcd", temperature=14.0)])
            edited = run_cli("edit", "2024-05-12-abcd", "--temp", "19.0", data=data)
            self.assertEqual(edited.returncode, 0, edited.stderr)
            shown = run_cli("show", "2024-05-12-abcd", data=data)
            self.assertIn("19.0 C", shown.stdout)
            sites = run_cli("sites", data=data)
            self.assertIn("19.0 C", sites.stdout)

    def test_edit_needs_a_change_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(Path(tmp) / "sessions.json", [sample_session("2024-05-12-abcd")])
            result = run_cli("edit", "2024-05-12-abcd", data=data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("nothing to change", result.stderr)

    def test_delete_declined_keeps_the_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(Path(tmp) / "sessions.json", [sample_session("2024-05-12-abcd")])
            result = run_cli("delete", "2024-05-12-abcd", data=data, stdin="n\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not deleted", result.stdout)
            self.assertEqual(len(json.loads(data.read_text(encoding="utf-8"))["sessions"]), 1)

    def test_delete_confirmed_removes_and_echoes_the_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(
                Path(tmp) / "sessions.json",
                [sample_session("2024-05-12-abcd"), sample_session("2024-05-11-efgh", day="2024-05-11", started_at="2024-05-11T20:00:00")],
            )
            result = run_cli("delete", "2024-05-12-abcd", data=data, stdin="y\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("deleted 2024-05-12-abcd", result.stdout)
            remaining = json.loads(data.read_text(encoding="utf-8"))["sessions"]
            self.assertEqual([s["id"] for s in remaining], ["2024-05-11-efgh"])
            listing = run_cli("sessions", data=data)
            self.assertNotIn("2024-05-12-abcd", listing.stdout)

    def test_delete_without_input_declines(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_store(Path(tmp) / "sessions.json", [sample_session("2024-05-12-abcd")])
            result = run_cli("delete", "2024-05-12-abcd", data=data, stdin="")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(data.read_text(encoding="utf-8"))["sessions"]), 1)

    def test_cli_refuses_a_parent_segment_data_path(self):
        result = run_cli("sessions", data=None, env={"ANURANCALL_DATA": "../escape.json"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'..'", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_resolves_a_relative_data_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--data",
                    "store.json",
                    "count",
                    "--species",
                    "Spring Peeper",
                    "--site",
                    "Mud Pond",
                    "--intervals",
                    "1.0, 1.2",
                ],
                cwd=tmp,
                input="",
                capture_output=True,
                text=True,
                timeout=120,
                env={k: v for k, v in os.environ.items() if k != "ANURANCALL_DATA"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            written = Path(tmp) / "store.json"
            self.assertTrue(written.exists())
            # human output shortens the path against the working directory ...
            self.assertIn("file  store.json", result.stdout)
            # ... while --json always carries the resolved absolute path
            payload = run_cli("sessions", "--json", data=written)
            self.assertEqual(
                json.loads(payload.stdout)["data_file"], str(written.resolve())
            )

    def test_data_flag_keeps_the_home_directory_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            data = Path(tmp) / "store" / "sessions.json"
            result = run_cli(
                "count", "--species", "Spring Peeper", "--site", "Mud Pond",
                "--intervals", "1.0, 1.2", data=data, env={"HOME": str(home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(data.exists())
            self.assertFalse((home / ".anurancall").exists())

    def test_environment_variable_selects_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "env-store.json"
            result = run_cli(
                "count", "--species", "Spring Peeper", "--site", "Mud Pond",
                "--intervals", "1.0, 1.2", env={"ANURANCALL_DATA": str(data)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(data.exists())


if __name__ == "__main__":
    unittest.main()

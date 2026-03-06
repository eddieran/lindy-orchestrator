"""Tests for PlanProgress live display UX."""

import time

from io import StringIO

from rich.console import Console

from lindy_orchestrator.reporter import PlanProgress


def _make_progress(interactive: bool = True) -> tuple[PlanProgress, Console, StringIO]:
    """Create a PlanProgress with a captured console."""
    buf = StringIO()
    con = Console(file=buf, force_terminal=interactive, no_color=True)
    pp = PlanProgress(console=con)
    return pp, con, buf


class TestSpinnerRenders:
    def test_build_display_contains_phase(self):
        pp, _con, _buf = _make_progress(interactive=True)
        pp._phase = "Calling LLM..."
        display = pp._build_display()
        assert "Calling LLM..." in display.plain

    def test_build_display_contains_event_count(self):
        pp, _con, _buf = _make_progress(interactive=True)
        for _ in range(5):
            pp.tick_event()
        display = pp._build_display()
        assert "events: 5" in display.plain

    def test_build_display_contains_timer(self):
        pp, _con, _buf = _make_progress(interactive=True)
        # Manually set start to make elapsed predictable
        pp._start = time.monotonic() - 65  # 1m05s
        display = pp._build_display()
        assert "1:05" in display.plain


class TestTimerTicks:
    def test_elapsed_increases(self):
        pp, _con, _buf = _make_progress()
        pp._start = time.monotonic() - 10
        assert pp.elapsed >= 10

    def test_timer_format_in_display(self):
        pp, _con, _buf = _make_progress()
        pp._start = time.monotonic() - 125  # 2m05s
        display = pp._build_display()
        assert "2:05" in display.plain


class TestPhaseTransitions:
    def test_set_phase_updates_state(self):
        pp, _con, _buf = _make_progress()
        pp.set_phase("Reading statuses...")
        assert pp.phase == "Reading statuses..."

    def test_set_phase_reflected_in_display(self):
        pp, _con, _buf = _make_progress()
        pp.set_phase("Parsing plan...")
        display = pp._build_display()
        assert "Parsing plan..." in display.plain

    def test_multiple_phase_transitions(self):
        pp, _con, _buf = _make_progress()
        phases = ["Reading statuses...", "Calling LLM...", "Parsing plan..."]
        for phase in phases:
            pp.set_phase(phase)
            assert pp.phase == phase
            assert phase in pp._build_display().plain


class TestNonTTYFallback:
    def test_non_tty_prints_phase_on_set(self):
        pp, _con, buf = _make_progress(interactive=False)
        pp.set_phase("Reading statuses...")
        output = buf.getvalue()
        assert "Reading statuses..." in output

    def test_non_tty_start_prints(self):
        pp, _con, buf = _make_progress(interactive=False)
        pp.start()
        output = buf.getvalue()
        assert "Initializing..." in output

    def test_non_tty_no_live_created(self):
        pp, _con, _buf = _make_progress(interactive=False)
        pp.start()
        assert pp._live is None

    def test_non_tty_stop_prints_summary(self):
        pp, _con, buf = _make_progress(interactive=False)
        pp.start()
        pp.tick_event()
        pp.tick_event()
        pp.stop()
        output = buf.getvalue()
        assert "2 events" in output

    def test_non_tty_heartbeat_on_update(self):
        pp, _con, buf = _make_progress(interactive=False)
        pp.start()
        # Simulate passage of 31 seconds
        pp._last_print_time = time.monotonic() - 31
        pp._start = time.monotonic() - 31
        pp._event_count = 42
        pp.update()
        output = buf.getvalue()
        assert "42 events" in output


class TestRichProtocol:
    def test_rich_returns_display(self):
        pp, _con, _buf = _make_progress(interactive=True)
        pp.set_phase("Calling LLM...")
        result = pp.__rich__()
        assert "Calling LLM..." in result.plain

    def test_live_renderable_is_self(self):
        """Live should use PlanProgress as renderable so __rich__() is called each refresh."""
        pp, _con, _buf = _make_progress(interactive=True)
        pp.start()
        assert pp._live is not None
        assert pp._live.renderable is pp
        pp.stop()


class TestInteractiveModeLifecycle:
    def test_start_creates_live(self):
        pp, _con, _buf = _make_progress(interactive=True)
        pp.start()
        assert pp._live is not None
        pp.stop()

    def test_stop_clears_live(self):
        pp, _con, _buf = _make_progress(interactive=True)
        pp.start()
        pp.stop()
        assert pp._live is None

    def test_stop_with_custom_message(self):
        pp, _con, buf = _make_progress(interactive=True)
        pp.start()
        pp.stop("Done in 5s!")
        output = buf.getvalue()
        assert "Done in 5s!" in output

    def test_tick_event_increments(self):
        pp, _con, _buf = _make_progress()
        assert pp.event_count == 0
        pp.tick_event()
        pp.tick_event()
        pp.tick_event()
        assert pp.event_count == 3

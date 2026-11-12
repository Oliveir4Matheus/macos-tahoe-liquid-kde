"""Boot with an incorrect dual-boot RTC, without touching the real desktop."""

import datetime
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import theme_switch


@pytest.fixture
def boot(monkeypatch):
    state = SimpleNamespace(elapsed=0.0, hour=15, queries=0, applied=[],
                            systemd=True, available=True, replies=[],
                            plasma_ready=True, ready_at=0.0)
    def sleep(seconds):
        state.elapsed += seconds
        if state.elapsed >= state.ready_at:
            state.plasma_ready = True
    def query(command, **kwargs):
        assert command == ["timedatectl", "show", "--property=NTP",
                           "--property=NTPSynchronized"]
        assert kwargs["capture"] and 0 < kwargs["timeout"] <= 2
        state.queries += 1
        reply = state.replies.pop(0) if state.replies else "NTP=yes\nNTPSynchronized=no\n"
        if isinstance(reply, Exception):
            raise reply
        if reply == "synchronized":
            state.hour = 21
            reply = "NTP=yes\nNTPSynchronized=yes\n"
        return subprocess.CompletedProcess(command, 0, stdout=reply)
    class Clock:
        @staticmethod
        def now():
            return datetime.datetime(2026, 10, 31, state.hour, 18)
    monkeypatch.setattr(theme_switch, "time", SimpleNamespace(
        monotonic=lambda: state.elapsed, sleep=sleep))
    monkeypatch.setattr(theme_switch, "_dt", SimpleNamespace(datetime=Clock))
    monkeypatch.setattr(theme_switch, "Path", lambda _: SimpleNamespace(
        is_dir=lambda: state.systemd))
    monkeypatch.setattr(theme_switch, "_have", lambda _: state.available)
    monkeypatch.setattr(theme_switch, "_run_user", query)
    monkeypatch.setattr(theme_switch, "_sync_session_env", lambda: None)
    monkeypatch.setattr(theme_switch, "plasmashell_ready",
                        lambda: state.plasma_ready)
    monkeypatch.setattr(theme_switch, "apply", lambda mode, **_: (
        state.applied.append(mode) or True))
    return state


def test_dual_boot_waits_for_corrected_time_before_selecting_theme(boot):
    boot.replies = ["NTP=yes\nNTPSynchronized=no\n"] * 2 + ["synchronized"]
    assert theme_switch.main(["auto"]) == 0
    assert boot.elapsed == 2
    assert boot.applied == ["dark"]


def test_offline_boot_leaves_theme_untouched_then_retry_uses_synced_time(boot):
    assert theme_switch.main(["auto"]) == 75
    assert boot.elapsed == 30
    assert boot.applied == []
    boot.replies = ["synchronized"]
    assert theme_switch.main(["auto"]) == 0
    assert boot.applied == ["dark"]


@pytest.mark.parametrize("failure", [OSError("system bus unavailable"),
    subprocess.TimeoutExpired("timedatectl", 2), "", "NTP=yes\n"])
def test_unavailable_sync_status_never_uses_unverified_clock(boot, failure):
    boot.replies = [failure] * 40
    assert theme_switch.main(["auto"]) == 75
    assert boot.elapsed == 30
    assert boot.applied == []


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_explicit_appearance_does_not_wait_for_ntp(boot, mode):
    assert theme_switch.main([mode]) == 0
    assert boot.queries == 0
    assert boot.applied == [mode]


@pytest.mark.parametrize("case", ["non-systemd", "no-timedatectl", "ntp-disabled"])
def test_systems_without_enabled_ntp_keep_wall_clock_behavior(boot, case):
    boot.systemd = case != "non-systemd"
    boot.available = case != "no-timedatectl"
    boot.replies = ["NTP=no\nNTPSynchronized=no\n"]
    assert theme_switch.main(["auto"]) == 0
    assert boot.elapsed == 0
    assert boot.applied == ["light"]


def test_already_synchronized_boot_applies_without_delay(boot):
    boot.replies = ["synchronized"]
    assert theme_switch.main(["auto"]) == 0
    assert boot.elapsed == 0
    assert boot.applied == ["dark"]


def test_user_service_retries_only_clock_sync_deferral():
    unit = (Path(__file__).resolve().parents[1] /
            "src/offline/mac-tahoe-liquid-kde-theme.service").read_text()
    assert "RestartForceExitStatus=75" in unit
    assert "RestartSec=15s" in unit


def test_auto_waits_until_plasmashell_can_render(boot):
    """The timer's Persistent catch-up fires at boot in parallel with session
    startup. Applying then would repaint nothing and leave the previous
    session's mode on screen, so ``auto`` must wait for plasmashell."""
    boot.replies = ["synchronized"]
    boot.plasma_ready = False
    boot.ready_at = 4.0
    assert theme_switch.main(["auto"]) == 0
    assert boot.elapsed == 4
    assert boot.applied == ["dark"]


def test_auto_applies_config_after_bounded_wait_when_no_desktop_appears(boot):
    """A headless/lingering session must not hang: after the bounded wait the
    configuration is still applied for the next login."""
    boot.replies = ["synchronized"]
    boot.plasma_ready = False
    boot.ready_at = float("inf")
    assert theme_switch.main(["auto"]) == 0
    assert boot.elapsed == 90
    assert boot.applied == ["dark"]

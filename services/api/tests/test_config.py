"""Settings: the .env that setup creates from .env.example starts an offline run, an empty value means "not set",
and the service's clock writes the time zone it is given."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from afterledger.adapters.local.stores import FixedClock, SystemClock, make_clock
from afterledger.config import REPO_ROOT, Settings, resolve_paths


@pytest.fixture(autouse=True)
def no_settings_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the file under test speaks (tools/dev.py, for one, sets AL_DEMO_BUNDLES for the processes it runs)."""
    for name in [k for k in os.environ if k.startswith("AL_")]:
        monkeypatch.delenv(name)


def test_the_example_env_starts_an_offline_run() -> None:
    s = Settings(_env_file=str(REPO_ROOT / ".env.example"))
    assert (s.mode, s.llm, s.store) == ("offline", "scripted", "local")
    assert s.story_day is None and s.demo_bundles is None and s.timezone is None and s.llm_record_dir is None
    assert s.live is False and s.live_voice is False


def test_an_empty_value_keeps_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("AL_STORY_DAY=\nAL_MAX_LLM_CALLS=\nAL_MODE=live\n", encoding="utf-8")
    s = Settings(_env_file=str(env))
    assert s.story_day is None and s.max_llm_calls == 400 and s.mode == "live"


def test_the_clock_writes_the_time_zone_it_is_given() -> None:
    now = SystemClock("Asia/Kolkata").now()
    assert now.utcoffset() is not None and now.utcoffset().total_seconds() == 5.5 * 3600
    assert SystemClock().now().tzinfo is not None                     # this computer's own zone, never naive
    fixed = make_clock("fixed:2026-10-14", "Asia/Tokyo")
    assert isinstance(fixed, FixedClock) and fixed.today().isoformat() == "2026-10-14"
    assert fixed.now().date().isoformat() == "2026-10-14" and fixed.now().utcoffset().total_seconds() == 9 * 3600
    assert isinstance(make_clock("system"), SystemClock)


def test_folders_in_env_are_relative_to_the_repository(tmp_path: Path) -> None:
    """tools/dev.py starts the API in services/api, but ./.data/cassettes/laptop in .env means the repository's own
    .data, where the guide says the recordings are."""
    env = tmp_path / ".env"
    env.write_text("AL_LLM_RECORD_DIR=./.data/cassettes/laptop\nAL_STATE_DIR=.data\n"
                   f"AL_LLM_REPLAY_DIR={tmp_path / 'replay'}\n", encoding="utf-8")
    s = resolve_paths(Settings(_env_file=str(env)))
    assert s.llm_record_dir == (REPO_ROOT / ".data" / "cassettes" / "laptop").resolve()
    assert s.state_dir == (REPO_ROOT / ".data").resolve() and s.llm_replay_dir == tmp_path / "replay"


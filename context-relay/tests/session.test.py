"""Warning decisions using numeric fixtures; never run a live hook or model."""

import datetime
import json
import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from fm_context_relay import admission, config, session  # noqa: E402

NOW = datetime.datetime(2026, 10, 30, 20, 6, 26, tzinfo=datetime.timezone.utc)


class WarningDecisionTests(unittest.TestCase):
    def record(self, tokens=200121):
        record = session.new_record("codex", "fixture-incident", NOW)
        record.update({
            "model": "gpt-6.1-sol",
            "session_started_at": "2026-10-30T19:22:24Z",
            "resident": {"status": "ok", "tokens": tokens},
            "window_tokens_observed": 475000,
            "compactions": {"count": 0, "counting": "unverified", "signal": "codex codex-tui"},
        })
        record["scan"]["caught_up"] = True
        return record

    def evaluate(self, record, new_compactions=0, values=None):
        # load() is intentionally avoided: no real home or state is accessed.
        loaded = SimpleNamespace(settings=config.Settings(values or {}, "file"),
                                 using="file", status="ok", sha=None)
        session.evaluate(record, loaded, "codex", NOW, "fixture-cli", new_compactions)
        return session.take_pending(record, "PostToolUse", NOW) or ""

    def test_observed_tui_crossing_is_advisory_and_deduplicated(self):
        record = self.record()
        text = self.evaluate(record)
        self.assertEqual(record["request"]["level"], 1)
        self.assertEqual(record["effective"]["effective_trigger"], "tokens")
        self.assertIn("is unverified", text)
        self.assertIn("Continue useful work", text)
        self.assertIn("native compaction", text)
        self.assertIn("42.1%", text)
        self.assertIn("475.0k", text)
        self.assertNotIn("Relay requested", text)
        self.assertNotIn("Finish the operation", text)
        self.assertNotIn("do not resume", text)
        self.assertEqual(self.evaluate(record), "")
        self.assertEqual(sorted(record["delivered"]), ["fallback:gpt-6.1-sol", "level:1"])

    def test_precaution_keeps_current_pressure_guard_and_session(self):
        record = self.record(310123)
        text = self.evaluate(record)
        self.assertEqual(record["request"]["level"], 2)
        self.assertIn("Do not admit new substantive work while", text)
        self.assertIn("310.0k", text)
        self.assertIn("valid resident measurement", text)
        self.assertIn("native compaction", text)
        self.assertNotIn("ask the operator for relay", text)
        self.assertIn("does not authorize", text)

    def test_compaction_keeps_history_without_requiring_replacement(self):
        record = self.record(310123)
        self.evaluate(record)
        record["compactions"]["count"] = 1
        record["resident"] = {"status": "no_data", "reason": "after_compaction", "tokens": None}
        text = self.evaluate(record, new_compactions=1)
        self.assertEqual(record["request"]["level"], 2)
        self.assertIn("historical", text)
        self.assertIn("next valid resident measurement", text)
        self.assertNotIn("relay request remains open", text)
        record["resident"] = {"status": "ok", "tokens": 42000}
        self.assertEqual(self.evaluate(record), "")

    def test_observed_capacity_wins_over_configuration_for_display(self):
        record = self.record()
        record["window_tokens_observed"] = 400000
        text = self.evaluate(record)
        self.assertIn("50.0%", text)
        self.assertIn("400.0k (observed", text)

    def test_configured_capacity_is_labelled_without_claiming_observation(self):
        record = self.record()
        record.pop("window_tokens_observed")
        text = self.evaluate(record)
        self.assertIn("475.0k (configured", text)
        self.assertNotIn("42.1%", text)

    def test_missing_resident_is_not_pressure(self):
        record = self.record()
        record["resident"] = {"status": "no_data", "reason": "after_compaction", "tokens": None}
        self.evaluate(record)
        self.assertEqual(record["request"]["level"], 0)

    def test_upgrade_rewrites_only_undelivered_legacy_instructions(self):
        record = self.record()
        self.evaluate(record)
        record["delivered"].remove("level:1")
        record["pending"] = [{"key": "level:1", "text": "Relay requested. Finish the operation; do not resume."}]
        text = self.evaluate(record)
        self.assertIn("Continue useful work", text)
        self.assertNotIn("Relay requested", text)
        self.assertEqual(record["delivered"].count("level:1"), 1)
        self.assertEqual(self.evaluate(record), "")

    def test_verified_compaction_and_age_notices_are_advisory(self):
        record = self.record(42000)
        record["compactions"].update(count=2, counting="verified")
        text = self.evaluate(record)
        self.assertIn("Continue useful work", text)
        self.assertEqual(record["effective"]["effective_trigger"], "compactions")
        aged = self.record(42000)
        aged["session_started_at"] = "2026-10-29T19:22:24Z"
        self.assertIn("Continue useful work", self.evaluate(aged))


    def test_codex_observed_admission_tracks_rollout_owner_and_latest_window(self):
        with tempfile.TemporaryDirectory(prefix='relay-window-') as temporary:
            path = Path(temporary)/'rollout.jsonl'
            rows = [json.loads(line) for line in (Path(__file__).parent/'fixtures/codex-tui-paginated.jsonl').read_text().splitlines()]
            rows[1]['payload']["model"] = 'gpt-unlisted'
            path.write_text("".join(json.dumps(row)+'\n' for row in rows))
            record = session.new_record("codex", 'fixture-session', NOW)
            record.update(owner_pid='123', transcript_path=str(path))
            session.refresh_measurement(record, "codex", 5, now=NOW)
            policy = dict(harness="codex", compactions=4)
            self.assertTrue(admission.evidence(record, policy)['admitted'])
            # A new token event without window evidence invalidates the old
            # capacity, even when the model and counted boundaries stay valid.
            with path.open('a') as stream:
                stream.write(json.dumps(dict(type='event_msg', payload=dict(type='token_count',
                    info=dict(last_token_usage=dict(input_tokens=400001)))))+'\n')
            session.refresh_measurement(record, "codex", 5, now=NOW)
            self.assertEqual(admission.evidence(record, policy)['reason_code'], 'observed_window_missing')
            path.write_text("".join(json.dumps(row)+'\n' for row in rows))
            record["scan"] = None
            record['owner_pid'] = '456'
            session.refresh_measurement(record, "codex", 5, now=NOW)
            self.assertTrue(admission.evidence(record, policy)['admitted'])
            record['session_id'] = 'other-owner'
            self.assertEqual(admission.evidence(record, policy)['reason_code'], 'observed_window_stale')

if __name__ == "__main__":
    unittest.main()

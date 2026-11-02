"""Confirmed per-harness policy: boundaries, uncertainty and migration."""
import datetime
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from fm_context_relay import config, measure, session

NOW = datetime.datetime(2026, 10, 30, 21, tzinfo=datetime.timezone.utc)
POLICY = {"codex": {"compactions": 4}, "claude": {"compactions": 2, "min_context_percent": 80}}


class NativeCountingTests(unittest.TestCase):
    """Sanitized native TUI structure and numbers, not private summaries.

    Fixture: 2026-10-30, Codex 0.166.0 paginated rollout, four linked
    boundaries and their adjacent event_msg/token_count input figures.
    IDs are replaced; only the protocol fields needed by the reader remain.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "rollout.jsonl"
        fixture = Path(__file__).parent / "fixtures" / "codex-tui-paginated.jsonl"
        self.rows = [json.loads(line) for line in fixture.read_text().splitlines()]
        self.write(self.rows)

    def write(self, rows):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def read(self, state=None, budget=10):
        state = measure.scan(state, str(self.path), "codex", budget)
        return state, measure.summarize("codex", state)

    def test_native_chain_accredits_both_observed_sources(self):
        for source in ("cli", "vscode"):
            self.rows[0]["payload"]["source"] = source
            self.write(self.rows)
            state, summary = self.read()
            self.assertEqual(summary["compactions"]["count"], 4)
            self.assertEqual(summary["compactions"]["counting"], "verified")
            self.assertIn("paginated", summary["compactions"]["signal"])
            self.assertIn(source, summary["compactions"]["signal"])
            self.assertEqual(summary["resident"]["tokens"], 38220)
            self.assertEqual(self.read(state)[1], summary)

    def test_zero_boundaries_requires_the_full_accredited_header(self):
        self.write(self.rows[:2])
        self.assertEqual(self.read()[1]["compactions"]["counting"], "verified")
        for key, value in (("originator", "other"), ("source", "app-server"),
                           ("cli_version", "0.167.0"),
                           ("context_window", {}), ("history_mode", "other"),
                           ("thread_source", "other"), ("session_id", "other")):
            rows = copy.deepcopy(self.rows[:2])
            rows[0]["payload"][key] = value
            self.write(rows)
            with self.subTest(key=key, value=value):
                self.assertEqual(self.read()[1]["compactions"]["counting"], "unverified")
        self.write(self.rows[1:])
        self.assertEqual(self.read()[1]["compactions"]["counting"], "unverified")

    def test_wrappers_nested_history_text_and_replay_do_not_count(self):
        boundary = self.rows[3]
        extras = [boundary, self.rows[7], self.rows[15],
                  {"type": "event_msg", "payload": {"type": "context_compacted", "window_id": "w4"}},
                  {"type": "response_item", "payload": {"type": "message", "role": "user", "content": json.dumps(boundary)}},
                  {"type": "response_item", "payload": {"type": "function_call_output", "output": json.dumps(boundary)}},
                  {"type": "response_item", "payload": {"replacement_history": [boundary]}}]
        self.write(self.rows + extras)
        state, summary = self.read()
        self.assertEqual(summary["compactions"]["count"], 4)
        self.assertEqual(summary["compactions"]["counting"], "verified")
        self.assertEqual(summary["resident"]["tokens"], 38220)
        self.assertEqual(self.read(state)[1], summary)

    def test_broken_or_unidentified_boundaries_veto_verification(self):
        for key, value in (("previous_window_id", "wrong"), ("first_window_id", "wrong"),
                           ("window_number", 3), ("window_number", True),
                           ("window_id", ""), ("window_id", "w0")):
            rows = copy.deepcopy(self.rows)
            rows[7]["payload"][key] = value
            self.write(rows)
            with self.subTest(key=key, value=value):
                self.assertEqual(self.read()[1]["compactions"]["counting"], "unverified")
        self.write(self.rows + [{"type": "compacted", "payload": {"window_id": "w4", "window_number": 99}}])
        self.assertEqual(self.read()[1]["compactions"]["counting"], "unverified")

    def test_partial_final_line_and_budget_exhaustion_remain_uncertain(self):
        with self.path.open("a") as handle:
            handle.write('{"type":')
        state, summary = self.read()
        self.assertFalse(summary["caught_up"])
        self.assertEqual(summary["compactions"]["counting"], "unverified")
        with self.path.open("a") as handle:
            handle.write('"event_msg","payload":{}}\n')
        self.assertEqual(self.read(state)[1]["compactions"]["counting"], "verified")
        self.write(self.rows + [{"type": "response_item", "payload": {}}] * 4000)
        state, summary = self.read(budget=-1)
        self.assertFalse(summary["caught_up"])
        self.assertEqual(summary["compactions"]["counting"], "unverified")
        self.assertEqual(self.read(state)[1]["compactions"]["counting"], "verified")

    def test_invalid_complete_json_and_late_header_remain_uncertain(self):
        with self.path.open("a") as handle:
            handle.write('bad json\n')
        self.assertEqual(self.read()[1]["compactions"]["counting"], "unverified")
        self.write([{"type": "response_item", "payload": {}}] + self.rows)
        self.assertEqual(self.read()[1]["compactions"]["counting"], "unverified")

    def test_rotation_truncation_and_missing_file_preserve_count_without_false_trust(self):
        for action in ("rotate", "truncate", "missing"):
            self.write(self.rows)
            state, _ = self.read()
            if action in ("rotate", "missing"):
                self.path.rename(self.path.with_suffix(".old"))
            if action != "missing":
                self.write(self.rows[:2])
            with self.subTest(action=action):
                state, summary = self.read(state)
                self.assertEqual(summary["compactions"]["count"], 4)
                self.assertEqual(summary["compactions"]["counting"], "unverified")
                self.write(self.rows)
                self.assertEqual(self.read(state)[1]["compactions"]["counting"], "verified")

    def test_existing_cursor_is_rescanned_once_without_duplicate_counts(self):
        state, _ = self.read()
        state.pop("codex_tui", None)  # A durable record written by the previous reader.
        state, summary = self.read(state)
        self.assertEqual(summary["compactions"]["counting"], "verified")
        self.assertEqual(summary["compactions"]["count"], 4)
        self.assertEqual(self.read(state)[1], summary)

    def test_rescan_boundary_invalidates_usage_even_with_durable_ids(self):
        state, _ = self.read()
        self.write(self.rows[:-2])  # Fourth boundary, before its zero/next call.
        _, summary = self.read(state)
        self.assertEqual(summary["compactions"]["count"], 4)
        self.assertEqual(summary["compactions"]["counting"], "verified")
        self.assertEqual(summary["resident"]["reason"], "after_compaction")

    def test_zero_and_model_change_do_not_invent_resident_or_model_based_trust(self):
        self.write(self.rows[:-1])
        self.assertEqual(self.read()[1]["resident"]["status"], "no_data")
        self.write(self.rows + [{"type": "turn_context", "payload": {"model": "different-model"}}])
        _, summary = self.read()
        self.assertEqual(summary["resident"]["reason"], "model_changed")
        self.assertEqual(summary["compactions"]["counting"], "verified")

    def test_real_reader_drives_four_only_and_safe_point_policy_once(self):
        self.write(self.rows[:-4])  # Three native boundaries.
        record = session.new_record("codex", "fixture-session", NOW)
        record["transcript_path"] = str(self.path)
        session.refresh_measurement(record, "codex", 10)
        evaluate = PolicyTests().evaluate
        text = evaluate(record)
        self.assertEqual(record["relay"]["status"], "waiting_compactions")
        self.assertFalse(record["relay"]["pending"])
        self.assertNotIn("Do not admit new substantive work", text)
        with self.path.open("a") as handle:
            for row in self.rows[-4:]:
                handle.write(json.dumps(row) + "\n")
        session.refresh_measurement(record, "codex", 10)
        text = evaluate(record)
        self.assertTrue(record["relay"]["pending"])
        self.assertIn("pause safely", text)
        self.assertEqual(evaluate(record), "")


class PolicyTests(unittest.TestCase):
    def test_assisted_notice_allows_preparation_but_keeps_explicit_confirmation(self):
        record = self.record(count=4)
        settings = config.Settings({"relay_policy": POLICY, "relay_mode": "assisted"}, "file")
        loaded = SimpleNamespace(settings=settings, using="file", status="ok", sha=None)
        session.evaluate(record, loaded, "codex", NOW, "fixture-cli")
        text = session.take_pending(record, "UserPromptSubmit", NOW)
        self.assertIn("Assisted mode", text)
        self.assertIn("relay start prepares the safe point and shows the package", text)
        self.assertIn("After the summary require explicit operator confirmation", text)
        self.assertNotIn("mode warn", text)
        self.assertNotIn("Warn mode", text)
        session.evaluate(record, loaded, "codex", NOW, "fixture-cli")
        self.assertFalse(session.take_pending(record, "UserPromptSubmit", NOW))

    def record(self, harness="codex", count=0, tokens=313489, counting="verified"):
        record = session.new_record(harness, "policy-fixture", NOW)
        record.update(model="gpt-6.1-sol" if harness == "codex" else "claude-opus-5-5",
                      resident={"status": "ok", "tokens": tokens},
                      compactions={"count": count, "counting": counting, "signal": "fixture"})
        record["scan"]["caught_up"] = True
        record["window_tokens_observed"] = 475000 if harness == "codex" else 1000000
        return record

    def evaluate(self, record, policy=POLICY):
        settings = config.Settings({"relay_policy": policy}, "file")
        loaded = SimpleNamespace(settings=settings, using="file", status="ok", sha=None)
        session.evaluate(record, loaded, record["harness"], NOW, "fixture-cli")
        return session.take_pending(record, "PostToolUse", NOW) or ""

    def pending(self, record):
        return (record.get("relay") or {}).get("pending", False)

    def test_codex_three_then_four_and_one_safe_point_notice(self):
        record = self.record(count=3)
        self.evaluate(record)
        self.assertFalse(self.pending(record))
        record["compactions"]["count"] = 4
        text = self.evaluate(record)
        self.assertTrue(self.pending(record))
        self.assertIn("Relay awaiting safe point", text)
        self.assertIn("pause safely", text)
        self.assertIn("do not run automatic relay", text)
        self.assertEqual(self.evaluate(record), "")

    def test_claude_count_and_current_occupancy_are_conjunctive(self):
        for count, tokens, expected in ((1, 800000, False), (2, 790000, False),
                                        (2, 799999, False), (2, 800000, True)):
            with self.subTest(count=count, tokens=tokens):
                record = self.record("claude", count, tokens)
                self.evaluate(record)
                self.assertEqual(self.pending(record), expected)

    def test_actual_old_level_two_is_not_eligibility_or_admission_block(self):
        record = self.record(count=0, counting="unverified")
        # First evaluate the old settings: real incident's durable level 2.
        self.evaluate(record, {})
        self.assertEqual(record["request"]["level"], 2)
        self.assertIn("level:2", record["delivered"])
        text = self.evaluate(record)
        self.assertFalse(self.pending(record))
        self.assertEqual(record["relay"]["status"], "unverified_compactions")
        self.assertNotIn("Relevo pendiente", text)
        self.assertIn("Current relay policy", text)
        self.assertIn("0/4 (unverified)", text)
        self.assertIn("66.0%", text)
        self.assertIn("do not block admission", text)
        self.assertEqual(self.evaluate(record), "")
        # An old, still-undelivered level 2 must be rewritten on upgrade.
        record["delivered"].remove("level:2")
        record["pending"] = [{"key": "level:2", "text": "Do not admit new substantive work"}]
        text = self.evaluate(record)
        self.assertIn("continue useful work", text)
        self.assertNotIn("Do not admit new substantive work", text)
        self.assertIn("do not block admission", text)

    def test_unmet_verified_policy_also_replaces_old_admission_instruction_once(self):
        record = self.record(count=0)
        self.evaluate(record, {})
        text = self.evaluate(record)
        self.assertIn("Current relay policy", text)
        self.assertIn("do not block admission", text)
        self.assertFalse(self.pending(record))
        self.assertEqual(self.evaluate(record), "")

    def test_unverified_four_is_uncertain_never_fallback_eligible(self):
        record = self.record(count=4, tokens=450000, counting="unverified")
        self.evaluate(record)
        self.assertFalse(self.pending(record))
        self.assertEqual(record["relay"]["status"], "unverified_compactions")

    def test_catching_up_cannot_create_eligibility(self):
        record = self.record(count=4)
        record["scan"]["caught_up"] = False
        self.evaluate(record)
        self.assertFalse(self.pending(record))

    def test_after_second_compaction_requires_new_valid_usage(self):
        record = self.record("claude", count=2, tokens=900000)
        record["resident"] = {"status": "no_data", "reason": "after_compaction", "tokens": None}
        self.evaluate(record)
        self.assertFalse(self.pending(record))
        self.assertEqual(record["relay"]["status"], "waiting_resident")

    def test_window_provenance_and_missing_capacity(self):
        record = self.record("claude", count=2, tokens=400000)
        record["window_tokens_observed"] = 500000
        self.evaluate(record)
        self.assertTrue(self.pending(record))
        self.assertEqual(record["relay"]["matched_evidence"]["window_source"], "observed")
        configured = self.record("claude", count=2, tokens=800000)
        configured.pop("window_tokens_observed")
        self.evaluate(configured)
        self.assertTrue(self.pending(configured))
        self.assertEqual(configured["relay"]["window_source"], "configured")
        unknown = self.record("claude", count=2, tokens=800000)
        unknown.pop("window_tokens_observed")
        unknown["model"] = "claude-unknown"
        self.evaluate(unknown)
        self.assertFalse(self.pending(unknown))
        self.assertEqual(unknown["relay"]["status"], "window_unknown")

    def test_safe_pending_survives_compaction_but_changed_policy_does_not(self):
        record = self.record("claude", count=2, tokens=800000)
        self.evaluate(record)
        evidence = record["relay"]["matched_evidence"]
        record["resident"] = {"status": "ok", "tokens": 42000}
        self.evaluate(record)
        self.assertTrue(self.pending(record))
        self.assertFalse(record["relay"]["current_match"])
        self.assertEqual(record["relay"]["matched_evidence"], evidence)
        self.evaluate(record, {"claude": {"compactions": 3, "min_context_percent": 80}})
        self.assertFalse(self.pending(record))

    def test_policy_schema_rejects_malformed_or_non_finite_constraints(self):
        for value in (None, [], {"unknown": {"compactions": 4}}, {"codex": {"compactions": True}},
                      {"codex": {"compactions": 0}}, {"claude": {"compactions": 2, "min_context_percent": 0}},
                      {"claude": {"compactions": 2, "min_context_percent": 101}},
                      {"claude": {"compactions": 2, "min_context_percent": float("nan")}},
                      {"claude": {"compactions": 2, "min_context_percent": True}}):
            self.assertTrue(config.validate({"relay_policy": value})[0])
        self.assertFalse(config.validate({"relay_policy": POLICY})[0])


class ClaudeHaikuNativeTests(unittest.TestCase):
    """Sanitized 2.1.316 shapes/usage; IDs/times replaced, replays appended.

    The fixture is a reader/policy regression, not a native hook invocation.
    Child cost belongs in accounting, but sidechain use cannot become resident.
    """

    def test_second_boundary_then_81_percent_and_one_durable_delivery(self):
        fixture = Path(__file__).parent / 'fixtures/claude-haiku-boundaries.jsonl'
        native = [json.loads(line) for line in fixture.read_text().splitlines()]
        settings = config.Settings({'models': {'claude-haiku-5-5': {'window_tokens': 200000}},
                                    'relay_policy': POLICY}, 'file')
        loaded = SimpleNamespace(settings=settings, using='file', status='ok', sha=None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'native.jsonl'
            record = session.new_record('claude', 'haiku-fixture', NOW)
            record['transcript_path'] = str(path)

            def evaluate(part):
                path.write_text(''.join(json.dumps(r) + '\n' for r in part))
                session.refresh_measurement(record, 'claude', 10)
                session.evaluate(record, loaded, 'claude', NOW, 'fixture-cli')

            evaluate(native[:4])
            self.assertEqual(record['compactions']['count'], 2)
            self.assertEqual(record['relay']['status'], 'waiting_resident')
            evaluate(native[:5])
            self.assertEqual(record['relay']['status'], 'waiting_occupancy')
            self.assertEqual(record['resident']['tokens'], 20491)
            evaluate(native)
            self.assertTrue(record['relay']['pending'])
            self.assertEqual(record['resident']['tokens'], 161970)
            self.assertEqual(record['compactions']['count'], 2)
            evidence = record['relay']['matched_evidence']
            self.assertEqual(evidence['window_tokens'], 200000)
            self.assertEqual(evidence['window_source'], 'configured')
            text = session.take_pending(record, 'UserPromptSubmit', NOW)
            self.assertIn('Relay policy reached', text)
            self.assertFalse(session.take_pending(record, 'UserPromptSubmit', NOW))
            # A subsequent low context cannot clear a durable safe-point request.
            low = copy.deepcopy(native[4])
            low['message']['id'] = 'after-auto'
            evaluate(native + [low])
            self.assertTrue(record['relay']['pending'])
            self.assertFalse(record['relay']['current_match'])
            self.assertFalse(session.take_pending(record, 'UserPromptSubmit', NOW))


if __name__ == "__main__":
    unittest.main()

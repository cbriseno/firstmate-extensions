import importlib.util
import json
import pathlib
import tempfile
import time
import unittest
import zipfile

MODULE = pathlib.Path(__file__).parents[1] / "fm_diagnostics.py"
spec = importlib.util.spec_from_file_location("fm_diagnostics", MODULE)
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

class DiagnosticsTest(unittest.TestCase):
    def test_canaries_and_export_scope(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td); directory = base / "logs"; out = base / "out"
            logger = d.Logger("extensiones", "1.2", "abcdef0123456", directory)
            canaries = []
            for event, schema in d.EVENTS.items():
                fields = {}
                for name, kind in schema.items():
                    secret = "canary-%s-%s-private" % (event.replace(".", "-"), name)
                    canaries.append(secret)
                    fields[name] = [secret] if kind == "names" else secret
                logger.emit(event, **fields, title="private-title-canary", path="/private/path/canary",
                            error="exception-content-canary")
            canaries.extend(["private-title-canary", "/private/path/canary", "exception-content-canary"])
            logger.flush()
            tampered = {"t": "2026-11-03T00:00:00Z", "c": "extensiones", "e": "relay.preflight",
                        "v": "1.2", "r": "abcdef0123456", "check": "model", "result": "fail",
                        "reason": "tampered-reason-canary"}
            with (directory / "events.jsonl").open("a") as stream:
                stream.write(json.dumps(tampered) + "\n")
            canaries.append("tampered-reason-canary")
            # Export discovers only documented XDG paths; redirect root for a synthetic fixture.
            old = d.root
            d.root = lambda component: directory if component == "extensiones" else base / "outside"
            try:
                (base / "outside").mkdir(); (base / "outside" / "events.jsonl").write_text(canaries[2])
                archive = d.export(["extensiones", "firstmate"], out)
            finally: d.root = old
            payload = zipfile.ZipFile(archive).read("events.jsonl").decode()
            self.assertTrue(all(secret not in payload for secret in canaries))
            self.assertIn("t-", payload)
            first_id = json.loads(payload.splitlines()[0]).get("task")
            second_archive = d.export(["extensiones"], base / "out-second")
            second_payload = zipfile.ZipFile(second_archive).read("events.jsonl").decode()
            second_id = json.loads(second_payload.splitlines()[0]).get("task")
            self.assertNotEqual(first_id, second_id)
            bad = base / "bad.zip"
            with zipfile.ZipFile(bad, "w") as z: z.writestr("events.jsonl", "not json\n")
            self.assertFalse(d._verify_zip(str(bad)))

    def test_rotation_and_cost(self):
        with tempfile.TemporaryDirectory() as td:
            logger = d.Logger("extensiones", "1", "abcdef0123456", td)
            target = pathlib.Path(td) / "events.jsonl"
            target.write_bytes(b"x" * (d.LIMIT - 100))
            logger.emit("snapshot.read", tasks=2)
            logger.flush()
            self.assertTrue((pathlib.Path(td) / "events.1.jsonl").exists())
            self.assertLessEqual(target.stat().st_size + (pathlib.Path(td) / "events.1.jsonl").stat().st_size, d.LIMIT)
            start = time.monotonic()
            for _ in range(10000): logger.emit("snapshot.read", tasks=3)
            logger.flush()
            elapsed = time.monotonic() - start
            print("10000 anonymized events: %.3fs" % elapsed)
            self.assertLess(elapsed, 10)

if __name__ == "__main__": unittest.main()

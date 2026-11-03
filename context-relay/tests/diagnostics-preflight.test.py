#!/usr/bin/env python3
"""Read-only relay preflight tests with temporary homes and synthetic Herdr."""
import json, os, pathlib, subprocess, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0,str(ROOT/'context-relay/lib'))
from fm_context_relay import install
class PreflightTest(unittest.TestCase):
    def test_preflight_reports_checks_and_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as temp:
            home=pathlib.Path(temp)/'home'; fakebin=pathlib.Path(temp)/'bin'; fakebin.mkdir()
            (home/'config').mkdir(parents=True); (home/'state/context-relay/sessions').mkdir(parents=True)
            environment_fixture=json.loads((ROOT/'context-relay/tests/fixtures/diagnostics-preflight-environment.json').read_text())
            fixture=ROOT/'context-relay/tests/fixtures'/environment_fixture['herdr_status_fixture']
            herdr=fakebin/'herdr'; herdr.write_text('#!/bin/sh\n[ "$1 $2" = "status --json" ] || exit 2\ncat '+repr(str(fixture))+'\n'); herdr.chmod(0o755)
            record={'schema':'fm-context-relay-session.v1','harness':'claude','model':'claude-opus-5-5','window_tokens_observed':1000000,'updated_at':'2026-11-03T00:00:00Z'}
            (home/'state/context-relay/sessions/claude-synthetic.json').write_text(json.dumps(record))
            hooks={event:[{'hooks':[{'type':'command','command':install.hook_command(str(home),'claude',event),'timeout':timeout}]}]
                   for event,timeout in install.CLAUDE_EVENTS}
            settings=home/'.claude/settings.local.json'; settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({'hooks':hooks}))
            env=dict(os.environ,PATH=str(fakebin)+os.pathsep+os.environ.get('PATH',''),FM_DIAGNOSTICS_HOME=str(pathlib.Path(temp)/'logs'))
            cli=ROOT/'context-relay/bin/fm-context-relay'
            result=subprocess.run([str(cli),'--home',str(home),'preflight','--harness','claude'],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            checks={x['check'] for x in json.loads(result.stdout)['checks']}
            self.assertEqual(checks,{'config','authorization','model','window','code','herdr.session','processes','tasks.paused'})
            self.assertFalse((home/'state/context-relay/automatic-authority.json').exists())
if __name__=='__main__': unittest.main()

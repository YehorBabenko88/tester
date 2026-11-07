import unittest
from uuid import uuid4
from src.control.commands import parse_command,authorize

def command(**kwargs):
    base={"schema_version":"1.0","request_id":str(uuid4()),"actor":"codex:research",
          "action":"history.coverage","category":"linear","symbol":"BTCUSDT",
          "start_ms":1700000000000,"end_ms":1700000060000,"params":{}}
    base.update(kwargs)
    return base

class CommandTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_command(command()).action,"history.coverage")
    def test_unknown_action(self):
        with self.assertRaises(ValueError): parse_command(command(action="shell.exec"))
    def test_wrong_version(self):
        with self.assertRaises(ValueError): parse_command(command(schema_version="2"))
    def test_bad_range(self):
        with self.assertRaises(ValueError): parse_command(command(end_ms=1700000000000))
    def test_float_time(self):
        with self.assertRaises(ValueError): parse_command(command(start_ms=1.0))
    def test_extra_fields(self):
        with self.assertRaises(ValueError): parse_command(command(sql="DROP TABLE"))
    def test_no_capability(self):
        with self.assertRaises(PermissionError): authorize(parse_command(command()),set())
    def test_approval_required(self):
        c=parse_command(command(action="system.config.apply"))
        with self.assertRaises(PermissionError): authorize(c,{"approval"})
        authorize(c,{"approval"},approved=True)
    def test_job_capability(self):
        c=parse_command(command(action="backtest.run"))
        with self.assertRaises(PermissionError): authorize(c,{"read"})
        authorize(c,{"job"})
if __name__=="__main__":unittest.main()

"""Startup diagnostics and Windows reader-lock recovery; no system changes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capture_service as service
from workflow_controller import friendly


class ServiceTests(unittest.TestCase):
    def test_save_retries_reader_lock_without_losing_previous_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'lease.json';target.write_text('{"running":true}')
            real=Path.replace;calls=[]
            def flaky(path,dest):
                calls.append(path)
                if len(calls)<4:
                    self.assertTrue(json.loads(target.read_text())['running'])
                    raise PermissionError('reader temporarily holds file')
                return real(path,dest)
            with patch.object(Path,'replace',flaky),patch.object(service.time,'sleep'):
                service.save(target,{'running':False,'startup_error':'certificate not trusted'})
            self.assertEqual(len(calls),4)
            self.assertFalse(json.loads(target.read_text())['running'])
            self.assertFalse(list(Path(temp).glob('*.tmp')))

    def test_actual_startup_error_is_bound_to_requested_instance(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'OUT',Path(temp)),patch.object(service,'LEASE',Path(temp)/'lease.json'):
            service.save(Path(temp)/'service_start_error.json',{'instance':'old','message':'outdated error'})
            service.save(Path(temp)/'lease.json',{'instance':'new','startup_error':'证书未能确认','message':'配置已恢复'})
            self.assertEqual(service.startup_error('new'),'证书未能确认')
            service.save(Path(temp)/'service_start_error.json',{'instance':'new','message':'6666 端口被占用'})
            self.assertEqual(service.startup_error('new'),'6666 端口被占用')

    def test_friendly_error_shows_cause_without_traceback(self):
        text='Traceback (most recent call last):\n  File "capture_service.py", line 332\nRuntimeError: 临时证书未能确认'
        self.assertEqual(friendly(text),'临时证书未能确认')

    def test_upgrade_preservation_is_checked_before_desktop_handoff(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'LEASE',Path(temp)/'lease.json'):
            service.save(service.LEASE,{'instance':'live','running':True})
            with patch.object(service,'preserve_upgrade',return_value=True),patch.object(service.subprocess,'run') as launch:
                service.desktop_dispatch(SimpleNamespace(action='stop'))
                launch.assert_not_called()

    def test_upgrade_controller_identity_survives_a_different_ui_parent(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'ROOT',Path(temp)):
            marker=Path(temp)/'output/app_upgrade.json'
            service.save(marker,{'old_pid':42,'controller_pid':77,'instance':'live','expires_at':100})
            self.assertTrue(service.preserve_upgrade({'instance':'live'},[12,77],99))
            self.assertFalse(service.preserve_upgrade({'instance':'other'},[77],99))
            self.assertFalse(service.preserve_upgrade({'instance':'live'},[12],99))
            self.assertFalse(service.preserve_upgrade({'instance':'live'},[77],101))


if __name__=='__main__':unittest.main()

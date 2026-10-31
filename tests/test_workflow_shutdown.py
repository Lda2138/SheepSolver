import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import capture_service
import workflow_controller as workflow
from solve_verified_map import atomic_json


class ShutdownTests(unittest.TestCase):
    def make(self,directory):
        root=Path(directory);out=root/'workflow';capture=root/'capture'
        capture.mkdir();(root/'output').mkdir()
        self.enterContext(patch.object(workflow,'OUT',out))
        self.enterContext(patch.object(workflow,'CAPTURE',capture))
        self.enterContext(patch.object(capture_service,'ROOT',root))
        controller=workflow.Controller();controller.owns_capture=True
        self.addCleanup(controller.pool.shutdown,wait=True)
        atomic_json(capture/'service_lease.json',{'instance':'live','running':True})
        return controller,root

    def test_upgrade_keeps_capture_even_when_ui_is_not_the_controller_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            controller,root=self.make(directory)
            atomic_json(root/'output/app_upgrade.json',{'old_pid':-1,'controller_pid':os.getpid(),
                        'instance':'live','expires_at':time.time()+60})
            with patch.object(controller,'run_command') as command:
                controller.close();command.assert_not_called()
            self.assertFalse(json.loads((workflow.OUT/'lease.json').read_text(encoding='utf-8'))['running'])

    def test_failed_capture_stop_still_marks_controller_as_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            controller,_=self.make(directory)
            with patch.object(controller,'run_command',side_effect=ValueError('stop timeout')):
                with self.assertRaisesRegex(ValueError,'stop timeout'):controller.close()
            self.assertFalse(json.loads((workflow.OUT/'lease.json').read_text(encoding='utf-8'))['running'])


if __name__=='__main__':unittest.main()

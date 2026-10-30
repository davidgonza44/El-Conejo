"""Temporary folders only: read-only landing contract, failures and restart recovery."""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
import ctypes
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask
from app.services import reconciliation_intake_service as intake, reconciliation_file_service as files
import reconciliation_file_bridge as producer


class IntakeTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='synthetic-landing-')
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name).resolve()
        self.landing=self.root/'landing'
        self.inbox=self.root/'inbox'
        self.archive=self.root/'archive'
        self.app=Flask('intake-test',instance_path=str(self.root))
        self.app.config.update(RECONCILIATION_LANDING_PATH=str(self.landing),
            RECONCILIATION_INBOX_PATH=str(self.inbox),RECONCILIATION_ARCHIVE_PATH=str(self.archive),
            RECONCILIATION_SETTLE_SECONDS=0)
        context=self.app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.source=Path(producer.produce(self.landing,'2030-01-01','0800',3)['path'])
        self.original=self.source.read_bytes()
        self.initial_mtime=self.source.stat().st_mtime_ns

    def unchanged(self):
        self.assertEqual(self.source.read_bytes(),self.original)
        self.assertEqual(self.source.stat().st_mtime_ns,self.initial_mtime)
        self.assertEqual(list(self.landing.iterdir()),[self.source])

    def test_valid_and_repeated_after_archive(self):
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.assertEqual(files.digest(self.source),files.digest(self.inbox/self.source.name))
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.archive.mkdir()
        (self.inbox/self.source.name).rename(self.archive/self.source.name)
        self.assertEqual(files.transport(self.source,self.inbox)['status'],'duplicate')
        self.unchanged()

    def test_tmp_ignored_and_missing_landing_not_created(self):
        with self.assertRaises(ValueError): intake.prepare(self.source.name+'.tmp')
        self.source.unlink()  # Simulates source owned by the producer, before calling intake.
        self.landing.rmdir()
        self.assertEqual(intake.prepare(self.source.name)['status'],'waiting')
        self.assertFalse(self.landing.exists())

    def test_stability_two_observations_and_change(self):
        self.app.config['RECONCILIATION_SETTLE_SECONDS']=2
        with patch.object(intake.time,'time',return_value=100):
            self.assertEqual(intake.prepare(self.source.name)['status'],'waiting')
        with patch.object(intake.time,'time',return_value=101):
            self.assertEqual(intake.prepare(self.source.name)['status'],'waiting')
        with self.source.open('ab') as out: out.write(b'SYN-BENCH-00000004;14\n')
        self.original=self.source.read_bytes()
        self.initial_mtime=self.source.stat().st_mtime_ns
        with patch.object(intake.time,'time',return_value=102):
            self.assertEqual(intake.prepare(self.source.name)['status'],'waiting')
        with patch.object(intake.time,'time',return_value=104):
            self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.unchanged()

    @unittest.skipUnless(os.name == 'nt', 'Bloqueo kernel32 exclusivo de Windows')
    def test_temporarily_locked_windows_file(self):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,
                                    ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
        kernel.CreateFileW.restype=ctypes.c_void_p
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.CreateFileW(str(self.source),0x80000000,0,None,3,0x80,None)
        self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
        try:
            self.assertEqual(intake.prepare(self.source.name)['status'],'waiting')
            self.assertFalse((self.inbox/self.source.name).exists())
        finally: kernel.CloseHandle(handle)
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.unchanged()

    def test_interrupted_copy_and_retry(self):
        original=files.transport
        def fail(_path,_size): raise OSError('Synthetic interrupted copy')
        with patch.object(files,'transport',side_effect=lambda *a,**k:original(*a,progress=fail,**k)):
            self.assertEqual(intake.prepare(self.source.name)['status'],'waiting')
        self.assertFalse((self.inbox/self.source.name).exists())
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.unchanged()

    def test_hash_mismatch_before_publication(self):
        original=files.digest
        def mismatch(path):
            result=original(path)
            if path.parent==self.inbox and path.suffix=='.tmp': result['sha256']='0'*64
            return result
        with patch.object(files,'digest',side_effect=mismatch):
            self.assertEqual(intake.prepare(self.source.name)['status'],'waiting')
        self.assertFalse((self.inbox/self.source.name).exists())
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.unchanged()

    def test_inbox_tamper_blocked_even_if_landing_disappears(self):
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        (self.inbox/self.source.name).write_bytes(b'product_code;quantity\nX;99\n')
        self.assertEqual(intake.prepare(self.source.name)['status'],'conflict')
        self.unchanged()
        self.source.unlink()  # Remote owner removal, not an application operation.
        self.assertEqual(intake.prepare(self.source.name)['status'],'conflict')

    def test_same_cut_changed_content_rejected(self):
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.source.write_bytes(b'product_code;quantity\nX;99\n')
        changed=self.source.read_bytes()
        self.assertEqual(intake.prepare(self.source.name)['status'],'conflict')
        self.assertEqual(self.source.read_bytes(),changed)
        self.assertEqual((self.inbox/self.source.name).read_bytes(),self.original)

    def test_restart_after_receipt_before_publication(self):
        original=files.rename_new
        def crash(source, target):
            if target.name==self.source.name: raise OSError('Synthetic crash before publication')
            return original(source,target)
        with patch.object(files,'rename_new',side_effect=crash):
            self.assertEqual(intake.prepare(self.source.name)['status'],'waiting')
        self.assertTrue((self.inbox/'.transport_receipts'/(self.source.name+'.json')).exists())
        self.assertFalse((self.inbox/self.source.name).exists())
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.unchanged()

    def test_process_restart_uses_durable_receipt(self):
        self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        import subprocess
        code=('import os;os.environ["PYTHON_DOTENV_DISABLED"]="1";'
              'from flask import Flask;from app.services import reconciliation_intake_service as s;'
              f'a=Flask("restart",instance_path={str(self.root)!r});'
              f'a.config.update(RECONCILIATION_LANDING_PATH={str(self.landing)!r},'
              f'RECONCILIATION_INBOX_PATH={str(self.inbox)!r},RECONCILIATION_ARCHIVE_PATH={str(self.archive)!r});'
              'ctx=a.app_context();ctx.push();'
              f'assert s.verify_private({self.source.name!r});print("RESTART PASS")')
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,check=False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.unchanged()

    def test_disjoint_paths_fail_closed(self):
        for key,value in [('RECONCILIATION_INBOX_PATH',self.landing),
                          ('RECONCILIATION_ARCHIVE_PATH',self.landing/'archive'),
                          ('RECONCILIATION_INBOX_PATH',self.root)]:
            with patch.dict(self.app.config,{key:str(value)}):
                with self.assertRaises(ValueError): intake.prepare(self.source.name)
        self.unchanged()

    def test_no_write_operation_allowed_in_landing(self):
        original_open=Path.open
        original_unlink=Path.unlink
        original_rename=os.rename
        original_replace=os.replace
        def guard_path(path):
            self.assertFalse(Path(path).resolve().is_relative_to(self.landing),'Application mutated landing')
        def opening(path,mode='r',*a,**k):
            if any(flag in mode for flag in 'wax+'): guard_path(path)
            return original_open(path,mode,*a,**k)
        def unlinking(path,*a,**k):
            guard_path(path)
            return original_unlink(path,*a,**k)
        def moving(fn):
            def move(a,b):
                guard_path(a);guard_path(b)
                return fn(a,b)
            return move
        with patch.object(Path,'open',opening),patch.object(Path,'unlink',unlinking),\
             patch.object(os,'rename',moving(original_rename)),patch.object(os,'replace',moving(original_replace)):
            self.assertEqual(intake.prepare(self.source.name)['status'],'ready')
        self.unchanged()


if __name__=='__main__': unittest.main(verbosity=2)

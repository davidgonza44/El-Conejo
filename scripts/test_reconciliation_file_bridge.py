"""Filesystem-only tests; temporary folders and synthetic bytes, no databases."""
import tempfile
import unittest
from pathlib import Path
import reconciliation_file_bridge as bridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='synthetic-file-bridge-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_publish_after_export_and_no_overwrite(self):
        stage = self.root/'export.csv.tmp'
        stage.write_bytes(b'product_code;quantity\nSYN-001;1\n')
        final = 'inventario_2030-01-01_0800.csv'
        result = bridge.publish_export(stage,final)
        self.assertFalse(stage.exists())
        self.assertEqual(result['sha256'],bridge.digest(self.root/final)['sha256'])
        stage.write_bytes(b'different')
        with self.assertRaises(FileExistsError): bridge.publish_export(stage,final)
        self.assertTrue(stage.exists())

    def test_changed_source_never_published(self):
        result = bridge.produce(self.root/'exports','2030-01-01','1200',20000)
        source = Path(result['path'])
        def change(_temporary,_copied):
            with source.open('ab') as stream: stream.write(b'X')
        with self.assertRaises(RuntimeError):
            bridge.transport(source,self.root/'inbox',progress=change)
        self.assertFalse((self.root/'inbox'/source.name).exists())

    def test_names_and_calendar_dates(self):
        for name in ('../inventario_2030-01-01_0800.csv','inventario_2030-02-30_0800.csv',
                     'inventario_2030-01-01_0900.csv','inventario_2030-01-01_0800.csv.tmp'):
            with self.assertRaises(ValueError): bridge.validate_name(name)

    def test_existing_destination_conflict_preserved(self):
        result = bridge.produce(self.root/'exports','2030-01-01','1600',3)
        inbox = self.root/'inbox'
        inbox.mkdir()
        target = inbox/Path(result['path']).name
        target.write_bytes(b'existing synthetic evidence')
        with self.assertRaises(ValueError): bridge.transport(result['path'],inbox)
        self.assertEqual(target.read_bytes(),b'existing synthetic evidence')


if __name__ == '__main__': unittest.main(verbosity=2)

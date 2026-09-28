"""CLI and path regression tests; synthetic inputs only, no database or model needed."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'backend/scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseScriptsTest(unittest.TestCase):
    def test_cli_requires_explicit_inputs_without_loading_runtime(self):
        scripts = {
            'backend/scripts/ingest_test_batch.py': ['--xlsx', '--raw-root'],
            'backend/scripts/import_clinical_csv.py': ['--csv'],
            'backend/scripts/ingest_cohort_batch.py': ['--manifest-dir', '--raw-root'],
            'deploy/remote/run_remote_batch.py': ['--input-root', '--out', '--code-dir', '--deeplung-image', '--ckpt'],
        }
        for script, required in scripts.items():
            with self.subTest(script=script):
                # -S excludes installed packages: help/validation must use only stdlib.
                cmd = [sys.executable, '-S', str(ROOT / script)]
                help_result = subprocess.run(cmd + ['--help'], capture_output=True, text=True)
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                for missing in required:
                    args = [v for flag in required if flag != missing for v in (flag, 'synthetic-input')]
                    result = subprocess.run(cmd + args, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(missing, result.stderr)
                    self.assertNotIn('Traceback', result.stderr)

    def test_path_mapping_respects_directory_boundaries(self):
        m = load_script('ingest_cohort_batch')
        cases = [
            ('/legacy/dicom/case1', '/synthetic/raw/case1'),
            ('/legacy/dicom', '/synthetic/raw'),
            ('/legacy/dicom-other/case1', '/legacy/dicom-other/case1'),
            ('/other/legacy/dicom/case1', '/other/legacy/dicom/case1'),
            ('case1', '/synthetic/raw/case1'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(m._map_ct_dir(source, '/synthetic/raw', '/legacy/dicom/'), expected)
        self.assertEqual(m._map_ct_dir('/absolute/case1', '/synthetic/raw'), '/absolute/case1')

    def test_synthetic_manifest_and_patient_directory(self):
        m = load_script('ingest_cohort_batch')
        names = load_script('ingest_test_batch')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / 'episode_status.csv'
            manifest.write_text('episode_id,inventory_status,selected_series_uid\nDEMO_001,done,1.2.3\n', encoding='utf-8-sig')
            self.assertEqual(m._read_csv(str(manifest))[0]['episode_id'], 'DEMO_001')
            patient = root / 'group' / 'DEMO_PATIENT_001'
            patient.mkdir(parents=True)
            self.assertEqual(names._find_patient_folders(tmp, 'DEMO_PATIENT_001'), [str(patient)])


if __name__ == '__main__':
    unittest.main()

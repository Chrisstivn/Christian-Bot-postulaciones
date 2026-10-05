import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pdf_generator


class WordConfigurationTests(unittest.TestCase):
    def test_bundled_script_is_resolved_via_wsl_without_windows_install_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'result.pdf'
            out.write_bytes(b'%PDF-test')
            with patch.object(pdf_generator, 'WORD_TO_PDF_SCRIPT', ''), \
                 patch.object(pdf_generator, '_wsl_to_windows_path', side_effect=['doc.docx', 'out.pdf', 'bundled.ps1']) as convert, \
                 patch.object(pdf_generator.subprocess, 'run') as run:
                pdf_generator.docx_to_pdf_via_word('doc.docx', str(out))
            self.assertEqual(convert.call_args_list[-1].args[0], str(pdf_generator.BUNDLED_WORD_SCRIPT))
            self.assertIn('bundled.ps1', run.call_args.args[0])
            self.assertTrue(pdf_generator.BUNDLED_WORD_SCRIPT.is_file())

    def test_explicit_windows_script_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'result.pdf'
            out.write_bytes(b'%PDF-test')
            with patch.object(pdf_generator, 'WORD_TO_PDF_SCRIPT', r'C:\Custom\convert.ps1'), \
                 patch.object(pdf_generator, '_wsl_to_windows_path', side_effect=['doc.docx', 'out.pdf']), \
                 patch.object(pdf_generator.subprocess, 'run') as run:
                pdf_generator.docx_to_pdf_via_word('doc.docx', str(out))
            self.assertIn(r'C:\Custom\convert.ps1', run.call_args.args[0])

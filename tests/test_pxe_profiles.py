"""Regression tests for generated BIOS/UEFI PXE menu ordering."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class MergedGrubTemplateTests(unittest.TestCase):
    def test_uefi_uses_http_first_with_matching_tftp_fallback(self):
        source = (REPO / "build" / "merge-pxe-profiles.sh").read_text(
            encoding="utf-8"
        )
        http_entry = source.index(
            'menuentry "Lite Auto Cache - HTTP fast path (recommended)"'
        )
        tftp_entry = source.index('menuentry "Lite Auto Cache - TFTP recovery"')
        self.assertLess(http_entry, tftp_entry)
        self.assertIn("set default=0", source)
        self.assertIn("set fallback=3", source)


@unittest.skipIf(os.name == "nt", "requires a POSIX shell and sed")
class RenderConfigsTests(unittest.TestCase):
    def _render(self, dual_profile):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pxelinux.cfg").mkdir()
            (root / "grub").mkdir()
            shutil.copy2(REPO / "pxe" / "render-configs.sh", root)
            (root / "pxelinux.cfg" / "default").write_text(
                "fetch=http://{{HTTP}}/thinclient/filesystem.squashfs\n",
                encoding="utf-8",
            )
            profile_path = "/thinclient/lite/vmlinuz" if dual_profile else "/thinclient/vmlinuz"
            (root / "grub" / "grub.cfg").write_text(
                "set default=0\n"
                "set fallback=3\n"
                "set timeout=5\n"
                f"linux {profile_path} fetch=http://{{{{HTTP}}}}/thinclient/filesystem.squashfs\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["bash", str(root / "render-configs.sh"), "192.0.2.15:8080", "--tftp-first"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return (root / "grub" / "grub.cfg").read_text(encoding="utf-8")

    def test_dual_profile_tftp_first_selects_entry_three(self):
        rendered = self._render(dual_profile=True)
        self.assertIn("set default=3", rendered)
        self.assertIn("set fallback=0", rendered)
        self.assertNotIn("{{HTTP}}", rendered)

    def test_single_profile_tftp_first_selects_entry_one(self):
        rendered = self._render(dual_profile=False)
        self.assertIn("set default=1", rendered)
        self.assertIn("set fallback=0", rendered)
        self.assertNotIn("{{HTTP}}", rendered)


if __name__ == "__main__":
    unittest.main()

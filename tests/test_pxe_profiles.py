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

    def test_single_profile_template_labels_http_as_recommended(self):
        source = (REPO / "build" / "build.sh").read_text(encoding="utf-8")
        self.assertIn(
            'menuentry "Start $DISTRO_NAME - HTTP fast path (recommended)"',
            source,
        )
        self.assertIn(
            'menuentry "Start $DISTRO_NAME - TFTP recovery"', source
        )


@unittest.skipIf(os.name == "nt", "requires a POSIX shell and sed")
class RenderConfigsTests(unittest.TestCase):
    def _simple_tree(self, directory, content):
        root = Path(directory)
        (root / "pxelinux.cfg").mkdir()
        shutil.copy2(REPO / "pxe" / "render-configs.sh", root)
        config = root / "pxelinux.cfg" / "default"
        config.write_text(content, encoding="utf-8")
        return root, config

    def test_invalid_server_is_rejected_before_modifying_boot_menus(self):
        for server in ("http://192.0.2.1", "host:0", "host:65536", "host:no",
                       "host&unexpected", "host|unexpected", "host name", "host:80:90",
                       "pxe..example", "pxe..", "pxe-.example", "pxe_1.example",
                       "a" * 64 + ".example", "192.0.2.999"):
            with self.subTest(server=server), tempfile.TemporaryDirectory() as directory:
                original = "fetch=http://{{HTTP}}/thinclient/filesystem.squashfs\n"
                root, config = self._simple_tree(directory, original)
                result = subprocess.run(["bash", str(root / "render-configs.sh"), server],
                                        capture_output=True, text=True)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(original, config.read_text(encoding="utf-8"))

    def test_valid_hostname_and_decimal_port_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self._simple_tree(directory, "fetch=http://{{HTTP}}/root.img\n")
            subprocess.run(["bash", str(root / "render-configs.sh"), "pxe-1.example.:08080"],
                           check=True, capture_output=True)
            self.assertEqual("fetch=http://pxe-1.example.:08080/root.img\n",
                             config.read_text(encoding="utf-8"))

    def test_partial_template_is_fully_retargeted(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self._simple_tree(directory,
                "fetch=http://{{HTTP}}/thinclient/filesystem.squashfs\n"
                "tc.config=http://192.0.2.5:8087/config.json\n"
                "nfsroot=192.0.2.5:/srv/thinclient\n")
            subprocess.run(["bash", str(root / "render-configs.sh"), "192.0.2.15:8080"],
                           check=True, capture_output=True)
            self.assertEqual(
                "fetch=http://192.0.2.15:8080/thinclient/filesystem.squashfs\n"
                "tc.config=http://192.0.2.15:8080/config.json\n"
                "nfsroot=192.0.2.15:/srv/thinclient\n",
                config.read_text(encoding="utf-8"))

    def test_failed_retarget_is_reported_to_the_deployment_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _config = self._simple_tree(directory,
                "fetch=http://192.0.2.5:8087/thinclient/filesystem.squashfs\n")
            binaries = root / "bin"
            binaries.mkdir()
            sed = binaries / "sed"
            sed.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
            sed.chmod(0o755)
            result = subprocess.run(["bash", str(root / "render-configs.sh"), "192.0.2.15"],
                                    env={**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"]},
                                    capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)

    def _render(self, dual_profile, repetitions=1, mode="--tftp-first"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pxelinux.cfg").mkdir()
            (root / "grub").mkdir()
            shutil.copy2(REPO / "pxe" / "render-configs.sh", root)
            (root / "pxelinux.cfg" / "default").write_text(
                "fetch=http://{{HTTP}}/thinclient/filesystem.squashfs\n",
                encoding="utf-8",
            )
            if dual_profile:
                grub = (
                    "set default=0\n"
                    "set fallback=3\n"
                    "set timeout=5\n"
                    'menuentry "Lite Auto Cache - HTTP fast path (recommended)" {\n'
                    "  linux (http,{{HTTP}})/thinclient/lite/vmlinuz\n"
                    "}\n"
                    'menuentry "Lite Network Only - TFTP recovery" {\n}\n'
                    'menuentry "Full Drivers Auto Cache - TFTP recovery" {\n}\n'
                    'menuentry "Lite Auto Cache - TFTP recovery" {\n'
                    "  linux /thinclient/lite/vmlinuz\n"
                    "}\n"
                )
            else:
                grub = (
                    "set default=0\n"
                    "set fallback=1\n"
                    "set timeout=5\n"
                    'menuentry "Start ThinClient - HTTP fast path (recommended)" {\n'
                    "  linux (http,{{HTTP}})/thinclient/vmlinuz\n"
                    "}\n"
                    'menuentry "Start ThinClient - TFTP recovery" {\n'
                    "  linux /thinclient/vmlinuz\n"
                    "}\n"
                )
            (root / "grub" / "grub.cfg").write_text(grub, encoding="utf-8")
            for _ in range(repetitions):
                command = [
                    "bash",
                    str(root / "render-configs.sh"),
                    "192.0.2.15:8080",
                ]
                if mode:
                    command.append(mode)
                subprocess.run(
                    command,
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
        self.assertIn(
            'menuentry "Lite Auto Cache - restart-safe (recommended)"', rendered
        )
        self.assertIn('menuentry "Lite Auto Cache - HTTP fast path"', rendered)
        self.assertNotIn(
            'menuentry "Lite Auto Cache - HTTP fast path (recommended)"', rendered
        )
        self.assertNotIn('menuentry "Lite Auto Cache - TFTP recovery"', rendered)
        self.assertIn('menuentry "Lite Network Only - TFTP recovery"', rendered)
        self.assertIn('menuentry "Full Drivers Auto Cache - TFTP recovery"', rendered)
        self.assertEqual(rendered.count("(recommended)"), 1)
        self.assertNotIn("{{HTTP}}", rendered)

    def test_single_profile_tftp_first_selects_entry_one(self):
        rendered = self._render(dual_profile=False)
        self.assertIn("set default=1", rendered)
        self.assertIn("set fallback=0", rendered)
        self.assertIn(
            'menuentry "Start ThinClient - restart-safe (recommended)"', rendered
        )
        self.assertIn('menuentry "Start ThinClient - HTTP fast path"', rendered)
        self.assertNotIn("TFTP recovery", rendered)
        self.assertNotIn("{{HTTP}}", rendered)

    def test_tftp_first_rewrite_is_idempotent(self):
        rendered = self._render(dual_profile=True, repetitions=2)
        self.assertEqual(rendered.count("restart-safe (recommended)"), 1)
        self.assertEqual(rendered.count("HTTP fast path"), 1)

    def test_default_render_keeps_http_recommended(self):
        rendered = self._render(dual_profile=True, mode=None)
        self.assertIn("set default=0", rendered)
        self.assertIn("set fallback=3", rendered)
        self.assertIn(
            'menuentry "Lite Auto Cache - HTTP fast path (recommended)"', rendered
        )
        self.assertIn('menuentry "Lite Auto Cache - TFTP recovery"', rendered)
        self.assertNotIn("restart-safe", rendered)
        self.assertEqual(rendered.count("(recommended)"), 1)
        self.assertNotIn("{{HTTP}}", rendered)


if __name__ == "__main__":
    unittest.main()

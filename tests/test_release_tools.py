"""Release signing, targeted canaries, and privacy of local measurements."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
LIBRARY = REPO / "overlay/usr/local/lib/thinclient"
sys.path.insert(0, str(LIBRARY if LIBRARY.is_dir() else Path("/usr/local/lib/thinclient")))
import metrics


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


canary = module("canary", "prepare-canary.py")


class ReleaseTools(unittest.TestCase):
    def test_rebuild_drops_old_signature_and_approval(self):
        for name in ("vmlinuz", "initrd.img", "filesystem.squashfs", "manifest.sha256.sig", "verification.json"):
            (self.root / name).write_text("old")
        subprocess.run([sys.executable, str(REPO / "tools/release-manifest.py"), "create", str(self.root)],
                       check=True, capture_output=True)
        self.assertFalse((self.root / "manifest.sha256.sig").exists())
        self.assertFalse((self.root / "verification.json").exists())

    @unittest.skipUnless(shutil.which("bash"), "requires bash")
    def test_reused_build_drops_derived_keys_but_preserves_config(self):
        rootfs = self.root / "rootfs"
        settings = rootfs / "etc/thinclient"
        settings.mkdir(parents=True)
        for name in ("config.pub", "release.pub", "config.json", "policy.json"):
            (settings / name).write_text("previous")
        subprocess.run(["bash", str(REPO / "build/reset-trust.sh"), str(rootfs)], check=True)
        self.assertEqual(["config.json", "policy.json"], sorted(item.name for item in settings.iterdir()))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def tree(self, name):
        root = self.root / name
        for directory in ("grub", "pxelinux.cfg"):
            (root / directory).mkdir(parents=True)
        (root / "grub/grub.cfg").write_text('set default=0\nmenuentry "' + name + '" {\nlinux /thinclient/vmlinuz\n}\n')
        (root / "pxelinux.cfg/default").write_text('KERNEL thinclient/vmlinuz\nAPPEND initrd=thinclient/initrd.img fetch=http://example/thinclient/filesystem.squashfs\n')
        (root / "config.json").write_text('{}')
        return root

    def test_canary_keeps_default_and_changes_only_selected_macs(self):
        stable, candidate = self.tree("stable"), self.tree("candidate")
        original = (stable / "grub/grub.cfg").read_bytes()
        output = self.root / "output"
        canary.prepare(stable, candidate, output, ["AA:BB:CC:DD:EE:FF"])
        self.assertEqual(original, (stable / "grub/grub.cfg").read_bytes())
        self.assertEqual((stable / "pxelinux.cfg/default").read_bytes(),
                         (output / "pxelinux.cfg/default").read_bytes())
        selected = (output / "pxelinux.cfg/01-aa-bb-cc-dd-ee-ff").read_text()
        self.assertIn("releases/canary/thinclient/vmlinuz", selected)
        self.assertIn('"$net_default_mac" = "aa:bb:cc:dd:ee:ff"',
                      (output / "grub/grub.cfg").read_text())
        self.assertTrue((output / "grub/grub.cfg").read_bytes().endswith(original))

    def test_canary_refuses_existing_output_and_malformed_identity(self):
        stable, candidate = self.tree("stable"), self.tree("candidate")
        for output, mac in ((stable, "aa:bb:cc:dd:ee:ff"), (self.root / "new", "../bad")):
            with self.assertRaises(ValueError):
                canary.prepare(stable, candidate, output, [mac])

    def test_metrics_drop_credentials_and_bound_history(self):
        event = metrics.sample("ui_ready", {"password": "secret", "username": "private",
                                            "host": "hidden", "profile": "lite"})
        self.assertNotIn("secret", json.dumps(event))
        self.assertNotIn("hidden", json.dumps(event))
        path = self.root / "events.jsonl"
        for _ in range(140):
            metrics.record("session_ended", {"exit_code": 0}, path)
        self.assertEqual(128, len(path.read_text().splitlines()))

    @unittest.skipUnless(shutil.which("openssl"), "requires openssl")
    def test_signed_release_verifies_and_tampering_fails(self):
        key, public = self.root / "private.pem", self.root / "public.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", str(key)], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)],
                       check=True, capture_output=True)
        for name in ("vmlinuz", "initrd.img", "filesystem.squashfs"):
            (self.root / name).write_bytes(name.encode())
        script = str(REPO / "tools/release-manifest.py")
        subprocess.run([sys.executable, script, "create", str(self.root), "--key", str(key)],
                       check=True, capture_output=True)
        check = [sys.executable, script, "verify", str(self.root), "--key", str(public)]
        self.assertEqual(0, subprocess.run(check, capture_output=True).returncode)
        (self.root / "filesystem.squashfs").write_bytes(b"tampered")
        self.assertNotEqual(0, subprocess.run(check, capture_output=True).returncode)
        # Rewriting the checksums cannot repair the detached signature.
        subprocess.run([sys.executable, script, "create", str(self.root)], check=True, capture_output=True)
        self.assertNotEqual(0, subprocess.run(check, capture_output=True).returncode)

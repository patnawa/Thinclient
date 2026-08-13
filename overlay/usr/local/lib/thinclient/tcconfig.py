"""Configuration handling and FreeRDP command construction for ThinClient.

Configuration is layered; later layers override earlier ones:

  1. /etc/thinclient/config.json          factory defaults baked into the image
  2. /run/thinclient/media-config.json    a TCCONF-labelled partition on the boot media
  3. /run/thinclient/remote-config.json   tc.config=<url> on the kernel cmdline, or DHCP option 224
  4. /run/thinclient/local-config.json    edits made in the UI this boot

Layer 4 is written back to the TCCONF partition when one is present, which is
what makes settings survive a reboot on a USB-booted client. A PXE client
normally has no writable media, so it is managed centrally through layer 3.
"""

import collections
import hashlib
import json
import os
import secrets
import shlex
import shutil
import subprocess

FACTORY = "/etc/thinclient/config.json"
RUNDIR = "/run/thinclient"
MEDIA_CONFIG = os.path.join(RUNDIR, "media-config.json")
REMOTE_CONFIG = os.path.join(RUNDIR, "remote-config.json")
LOCAL_CONFIG = os.path.join(RUNDIR, "local-config.json")
MEDIA_MOUNT = os.path.join(RUNDIR, "media")
USB_SHARE = "/media/tc"

LAYERS = (FACTORY, MEDIA_CONFIG, REMOTE_CONFIG, LOCAL_CONFIG)

DEVICE_DEFAULTS = {
    "hostname_prefix": "thin",
    "keyboard_layout": "us",
    "keyboard_variant": "",
    "timezone": "UTC",
    "ntp_server": "pool.ntp.org",
    "screen_blank_minutes": 0,
    "resolution": "auto",
    "admin_password": "",
    "auto_connect": "",
    "allow_settings": True,
    "allow_console": False,
    "allow_terminal": True,
    "session_bar": True,
    "show_ip": True,
}

# X11 layout codes (what setxkbmap takes) mapped to the Windows keyboard layout
# identifiers FreeRDP expects. FreeRDP rejects "/kbd:layout:us" outright, and an
# unmapped layout is better left out entirely - FreeRDP then reads the layout
# from the X server, which tc-session has already configured.
KEYBOARD_LAYOUT_IDS = {
    "us": "0x00000409",     # United States - English
    "gb": "0x00000809",     # United Kingdom
    "uk": "0x00000809",
    "th": "0x0000041E",     # Thai Kedmanee
    "de": "0x00000407",     # German
    "fr": "0x0000040C",     # French
    "es": "0x0000040A",     # Spanish
    "latam": "0x0000080A",  # Latin American
    "it": "0x00000410",     # Italian
    "jp": "0x00000411",     # Japanese
    "kr": "0x00000412",     # Korean
    "cn": "0x00000804",     # Chinese (Simplified) - US keyboard
    "ru": "0x00000419",     # Russian
    "br": "0x00000416",     # Portuguese (Brazilian ABNT)
}

CONNECTION_DEFAULTS = {
    "id": "",
    "name": "",
    "protocol": "rdp",          # rdp | vnc
    "host": "",
    "port": 3389,
    "username": "",
    "domain": "",
    "password": "",
    "prompt_credentials": True,
    "gateway": "",
    "gateway_username": "",
    "gateway_domain": "",
    "app": "",
    "display": "fullscreen",     # fullscreen | multimon | window | <W>x<H>
    "cert_policy": "ignore",     # ignore | tofu | strict
    "security": "auto",          # auto | nla | tls | rdp
    "gfx": "auto",               # auto | avc444 | avc420 | rfx | none
    "network": "auto",           # auto | lan | broadband | modem
    "audio_out": True,
    "audio_in": True,
    "redirect_clipboard": True,
    "redirect_usb_storage": True,
    "redirect_usb_devices": False,
    "redirect_smartcard": True,
    "redirect_printers": True,
    "auto_reconnect": True,
    "reconnect_delay": 5,
    "extra_args": [],
}


# --------------------------------------------------------------------- load --
def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load(layers=None):
    """Merge every configuration layer that exists into one dict.

    layers defaults to the four real paths above. It is a parameter so the
    merge rules can be exercised against known inputs without a client's
    /etc and /run underneath them.
    """
    cfg = {"schema": 1, "device": dict(DEVICE_DEFAULTS), "connections": []}
    for path in (LAYERS if layers is None else layers):
        layer = _read(path)
        if not layer:
            continue
        if isinstance(layer.get("device"), dict):
            cfg["device"].update(layer["device"])
        if isinstance(layer.get("connections"), list):
            cfg["connections"] = layer["connections"]

    normalised = []
    for index, raw in enumerate(cfg["connections"]):
        if not isinstance(raw, dict):
            continue
        conn = dict(CONNECTION_DEFAULTS)
        # dict() is a shallow copy, so every connection that omits extra_args
        # would otherwise share the one list object living in the defaults.
        conn["extra_args"] = list(CONNECTION_DEFAULTS["extra_args"])
        conn.update(raw)
        if not isinstance(conn.get("extra_args"), list):
            conn["extra_args"] = []
        if not conn["id"]:
            conn["id"] = "conn%d" % (index + 1)
        if not conn["name"]:
            conn["name"] = conn["host"] or conn["id"]
        try:
            conn["port"] = int(conn["port"]) or 3389
        except (TypeError, ValueError):
            conn["port"] = 3389
        normalised.append(conn)
    cfg["connections"] = normalised
    return cfg


def parse_extra_args(text):
    """Split a line of extra FreeRDP arguments the way a shell would.

    Values such as a Windows printer driver name contain spaces and are quoted;
    a plain split() would break them into fragments. The arguments are passed
    to FreeRDP as an argv list with no shell in between, so the quotes are
    consumed here and each value arrives as one argument.
    """
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        # Unbalanced quotes while the admin is still typing: fall back rather
        # than lose what they entered.
        return text.split()


def find(cfg, conn_id):
    for conn in cfg["connections"]:
        if conn["id"] == conn_id:
            return conn
    return None


# --------------------------------------------------------------------- save --
def media_is_writable():
    return os.path.isdir(MEDIA_MOUNT) and os.access(MEDIA_MOUNT, os.W_OK)


def save(cfg):
    """Persist configuration. Returns (ok, human_readable_message).

    This must never raise: the caller has already accepted the user's edits, and
    a storage problem should be reported on screen, not thrown through the UI.
    """
    payload = json.dumps(
        {"schema": 1, "device": cfg["device"], "connections": cfg["connections"]},
        indent=2,
    )

    # Layer 4: this boot's working copy. Lives on tmpfs, owned by the kiosk user.
    session_ok = True
    session_error = ""
    try:
        os.makedirs(RUNDIR, exist_ok=True)
        tmp = LOCAL_CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, LOCAL_CONFIG)
    except OSError as exc:
        session_ok = False
        session_error = str(exc)

    # Layer 2: the TCCONF partition, which is what survives a reboot.
    helper = "/usr/local/sbin/tc-save-config"
    persisted = False
    hint = "no configuration partition on this device"
    if os.path.exists(helper):
        try:
            result = subprocess.run(
                ["sudo", "-n", helper],
                input=payload, text=True, capture_output=True, timeout=30,
            )
            if result.returncode == 0:
                persisted = True
            else:
                detail = (result.stderr or "").strip().splitlines()
                if detail:
                    hint = detail[-1]
        except FileNotFoundError:
            hint = "sudo is not available"
        except (OSError, subprocess.SubprocessError) as exc:
            hint = str(exc)

    if persisted:
        return True, "Settings saved and will survive a reboot."
    if session_ok:
        return True, ("Settings applied for this session only — %s. "
                      "Add a partition labelled TCCONF to keep them." % hint)
    return False, ("Settings could not be stored: %s. They are active now but "
                   "will be lost when the session restarts." % session_error)


# --------------------------------------------------------------- passwords --
def hash_password(plain):
    """Salted SHA-256. Gates the settings UI; not a boundary against physical access."""
    salt = secrets.token_hex(8)
    return "sha256$%s$%s" % (salt, hashlib.sha256((salt + plain).encode()).hexdigest())


def verify_password(stored, plain):
    if not stored:
        return True
    if stored.startswith("sha256$"):
        try:
            _, salt, digest = stored.split("$", 2)
        except ValueError:
            return False
        return secrets.compare_digest(
            hashlib.sha256((salt + plain).encode()).hexdigest(), digest
        )
    # A plain-text password dropped straight into config.json by an admin.
    return secrets.compare_digest(stored, plain)


# ------------------------------------------------------------------ freerdp --
_CLIENT_CACHE = []


def freerdp_binary():
    if _CLIENT_CACHE:
        return _CLIENT_CACHE[0]
    for candidate in ("xfreerdp3", "xfreerdp", "/usr/bin/xfreerdp3", "/usr/bin/xfreerdp"):
        path = shutil.which(candidate) if not candidate.startswith("/") else (
            candidate if os.path.exists(candidate) else None
        )
        if path:
            _CLIENT_CACHE.append(path)
            return path
    return None


_STDIN_CACHE = []


def supports_stdin_credentials():
    """True when the installed FreeRDP can read the password from stdin.

    Preferred over /p: because a command line is visible in the process list.
    Cached: this spawns a process, and it must not sit on the connect path.
    """
    if _STDIN_CACHE:
        return _STDIN_CACHE[0]
    binary = freerdp_binary()
    if not binary:
        return False
    try:
        helptext = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, timeout=15
        )
        answer = "from-stdin" in (helptext.stdout + helptext.stderr)
    except (OSError, subprocess.SubprocessError):
        answer = False
    _STDIN_CACHE.append(answer)
    return answer


# ----------------------------------------------------------------- kerberos --
def kerberos_realm(conn):
    """Best guess at the Kerberos realm for a connection, or "".

    A NetBIOS domain such as CORP is not a realm, so fall back to the DNS
    domain of the server itself when the configured domain has no dots.
    """
    domain = (conn.get("domain") or "").strip().strip(".")
    if "." in domain:
        return domain.upper()
    host = (conn.get("host") or "").strip().strip(".")
    if "." in host and not host.replace(".", "").isdigit():
        return host.split(".", 1)[1].upper()
    return ""


def prepare_environment(conn):
    """Environment for the FreeRDP process, including Kerberos configuration.

    Without a krb5.conf FreeRDP logs "Configuration file does not specify
    default realm" and drops to NTLM. That still works today, but Windows is
    actively deprecating NTLM, so give Kerberos what it needs whenever we can
    work out the realm. Written under /run so no root privilege is required.
    """
    env = dict(os.environ)
    realm = kerberos_realm(conn)
    if not realm:
        return env

    path = os.path.join(RUNDIR, "krb5.conf")
    contents = (
        "[libdefaults]\n"
        "    default_realm = %s\n"
        "    dns_lookup_kdc = true\n"
        "    dns_lookup_realm = true\n"
        "    rdns = false\n"
        "    udp_preference_limit = 0\n" % realm
    )
    try:
        os.makedirs(RUNDIR, exist_ok=True)
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                existing = fh.read()
        if existing != contents:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(contents)
        env["KRB5_CONFIG"] = path
    except OSError:
        pass            # NTLM still works; never block a connection over this
    return env


Failure = collections.namedtuple("Failure", "message retryable")

# Ordered most-specific first: the first token found in the log wins.
#
# The third field says whether reconnecting could plausibly succeed. Anything
# the user or an administrator has to change - a password, a licence, a
# disabled account - is not retryable, because reconnecting in five seconds
# would only repeat the rejection and walk a domain account towards a lockout.
FAILURE_HINTS = (
    ("ERRCONNECT_ACCOUNT_LOCKED_OUT",
     "That account is locked out. An administrator has to unlock it.", False),
    ("ERRCONNECT_ACCOUNT_DISABLED", "That account is disabled.", False),
    ("ERRCONNECT_ACCOUNT_EXPIRED", "That account has expired.", False),
    ("ERRCONNECT_PASSWORD_MUST_CHANGE",
     "The password must be changed before signing in. Change it on a full PC first.",
     False),
    ("ERRCONNECT_PASSWORD_EXPIRED", "The password has expired.", False),
    ("ERRCONNECT_PASSWORD_CERTAINLY_EXPIRED",
     "The server rejected the credentials. Check the username, password and domain "
     "- and that this client's clock matches the server.", False),
    ("ERRCONNECT_LOGON_FAILURE",
     "Wrong username or password (or the wrong domain).", False),
    ("ERRCONNECT_INSUFFICIENT_PRIVILEGES",
     "That account is not allowed to sign in remotely. Add it to Remote Desktop Users.",
     False),
    ("ERRCONNECT_AUTHENTICATION_FAILED",
     "Authentication failed. With NLA this is usually the password, the domain, "
     "or a client clock more than five minutes out.", False),
    ("ERRCONNECT_DNS_NAME_NOT_FOUND",
     "The server name could not be resolved. Check DNS, or use an IP address.", True),
    ("ERRCONNECT_CONNECT_TRANSPORT_FAILED",
     "Could not reach the server. Check the address, the network and the firewall.",
     True),
    # FreeRDP reports this when it needs credentials and has no terminal to ask
    # on, which on a kiosk means the username or password was left empty.
    ("ERRCONNECT_CONNECT_CANCELLED",
     "No username or password was supplied, and this server requires them "
     "before it will connect.", False),
    ("ERRINFO_LOGOFF_BY_USER", "You signed out of the remote session.", False),
    ("ERRINFO_IDLE_TIMEOUT", "The remote session timed out.", False),
    ("ERRINFO_DISCONNECTED_BY_OTHER_CONNECTION",
     "Someone else signed in and took over this session.", False),
    ("ERRINFO_RPC_INITIATED_DISCONNECT",
     "An administrator disconnected the session.", False),
    ("ERRINFO_LICENSE",
     "The server refused a licence. Check RDS licensing and CALs.", False),
    ("SEC_E_NO_CREDENTIALS", "No credentials were supplied.", False),
    ("tlsv1 alert",
     "The secure channel was rejected by the server during the handshake.", False),
    ("certificate", "The server certificate was not accepted.", False),
    ("failed to open display", "The local display is not available.", False),
)


def explain_failure(log_path, code):
    """Turn a FreeRDP failure into a message and a retry decision.

    Returns a Failure(message, retryable). Callers must not infer retryability
    by reading the message: rewording a sentence would silently change whether
    clients reconnect.
    """
    text = ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        pass

    for token, message, retryable in FAILURE_HINTS:
        if token in text:
            return Failure(message, retryable)

    # Nothing recognised: fall back to the last error line, trimmed of FreeRDP's
    # timestamp/thread/module prefixes. An unexplained drop is the case
    # auto-reconnect exists for - a network blip or a server reboot - so it
    # stays retryable.
    for line in reversed(text.splitlines()):
        if "[ERROR]" in line:
            message = line.rsplit("]:", 1)[-1] if "]:" in line else line
            message = message.strip(" -\t")
            if message:
                return Failure(message, True)
    return Failure("exit code %d" % code, True)


_VNC_CACHE = []
VNC_DEFAULT_PORT = 5900


def vnc_binary():
    if _VNC_CACHE:
        return _VNC_CACHE[0]
    for candidate in ("xtigervncviewer", "vncviewer", "xvnc4viewer"):
        path = shutil.which(candidate)
        if path:
            _VNC_CACHE.append(path)
            return path
    return None


def build_vnc_command(conn, device, debug=False):
    """Command line for a VNC connection.

    VNC carries no device redirection, no audio and no domain login, so most of
    a connection's settings simply do not apply. Authentication is left to the
    viewer: classic VNC auth has a password and no username, and TigerVNC has
    its own prompt for it, so there is nothing useful for the manager's
    credential dialog to collect.
    """
    binary = vnc_binary()
    if not binary:
        raise RuntimeError("no VNC client installed (build with INCLUDE_VNC=1)")

    port = int(conn.get("port") or VNC_DEFAULT_PORT)
    if port == 3389:                    # an RDP default left behind by a protocol switch
        port = VNC_DEFAULT_PORT

    host = (conn.get("host") or "").strip()
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host
    # TigerVNC reads "host:N" as display N and "host::N" as a TCP port. Ports
    # are what people configure, so always use the double colon.
    argv = [binary, "%s::%d" % (host, port)]

    display = (conn.get("display") or "fullscreen").lower()
    if display in ("fullscreen", "multimon"):
        argv.append("-FullScreen")
    elif "x" in display:
        argv += ["-geometry", display]

    # Trade picture quality against bandwidth the same way the RDP side does.
    quality = {
        "lan": ("9", "0"),
        "broadband": ("6", "2"),
        "modem": ("2", "6"),
    }.get((conn.get("network") or "auto").lower())
    if quality:
        argv += ["-QualityLevel", quality[0], "-CompressLevel", quality[1]]

    # Do not disconnect other viewers, and never sit on a modal error dialog
    # that nobody is present to dismiss.
    argv += ["-Shared", "-AlertOnFatalError=0", "-ReconnectOnError=0"]
    if debug:
        argv.append("-Log=*:stderr:100")

    for extra in conn.get("extra_args") or []:
        if isinstance(extra, str) and extra.strip():
            argv.append(extra.strip())

    return argv, None


def build_command(conn, device, password=None, debug=False):
    """Return (argv, stdin_text). stdin_text is None when nothing is piped."""
    if (conn.get("protocol") or "rdp").lower() == "vnc":
        return build_vnc_command(conn, device, debug=debug)

    binary = freerdp_binary()
    if not binary:
        raise RuntimeError("no FreeRDP client installed")

    argv = [binary]
    target = (conn["host"] or "").strip()
    # An IPv6 literal has to be bracketed or FreeRDP cannot tell the address
    # colons from the port separator and refuses the whole command line.
    if ":" in target and not target.startswith("["):
        target = "[%s]" % target
    if conn["port"] and int(conn["port"]) != 3389:
        target = "%s:%d" % (target, int(conn["port"]))
    argv.append("/v:%s" % target)

    if conn["username"]:
        argv.append("/u:%s" % conn["username"])
    if conn["domain"]:
        argv.append("/d:%s" % conn["domain"])

    stdin_text = None
    secret = password if password is not None else conn.get("password", "")
    if secret:
        if supports_stdin_credentials():
            argv.append("/from-stdin")
            # FreeRDP prompts for each credential it was not given - in the
            # order username, domain, password - and reads the answers from
            # stdin. We always pass a username, but an empty domain still
            # counts as missing: FreeRDP would take the password line as the
            # domain and then hit end of input at the password prompt, aborting
            # with ERRCONNECT_CONNECT_CANCELLED. Answer the domain prompt
            # explicitly with a blank line so the password lands where it
            # belongs.
            answers = [] if conn.get("domain") else [""]
            answers.append(secret)
            stdin_text = "\n".join(answers) + "\n"
        else:
            argv.append("/p:%s" % secret)

    # --- display -------------------------------------------------------------
    display = (conn.get("display") or "fullscreen").lower()
    if display == "multimon":
        argv += ["/f", "/multimon"]
    elif display == "fullscreen":
        # dynamic-resolution lets the session follow a monitor hot-plug, but it
        # is mutually exclusive with /multimon.
        argv += ["/f", "+dynamic-resolution"]
    elif "x" in display:
        argv.append("/size:%s" % display)
    else:
        argv += ["/size:1280x800"]

    # --- security ------------------------------------------------------------
    policy = (conn.get("cert_policy") or "ignore").lower()
    if policy == "ignore":
        argv.append("/cert:ignore")
    elif policy == "tofu":
        argv.append("/cert:tofu")
    # 'strict' adds nothing: FreeRDP verifies against the system CA store.

    security = (conn.get("security") or "auto").lower()
    if security in ("nla", "tls", "rdp"):
        argv.append("/sec:%s" % security)

    if conn.get("gateway"):
        argv.append("/g:%s" % conn["gateway"])
        if conn.get("gateway_username"):
            argv.append("/gu:%s" % conn["gateway_username"])
        if conn.get("gateway_domain"):
            argv.append("/gd:%s" % conn["gateway_domain"])

    # --- codecs and bandwidth ------------------------------------------------
    gfx = (conn.get("gfx") or "auto").lower()
    if gfx == "auto":
        argv.append("/gfx")
    elif gfx == "avc444":
        argv.append("/gfx:AVC444")
    elif gfx == "avc420":
        argv.append("/gfx:AVC420")
    elif gfx == "rfx":
        argv.append("/rfx")
    elif gfx == "none":
        argv.append("-gfx")

    network = (conn.get("network") or "auto").lower()
    if network in ("auto", "lan", "broadband", "modem"):
        argv.append("/network:%s" % network)

    # --- device redirection --------------------------------------------------
    argv.append("+clipboard" if conn.get("redirect_clipboard") else "-clipboard")

    if conn.get("audio_out"):
        argv += ["/sound:sys:pulse", "/audio-mode:0"]
    else:
        argv.append("/audio-mode:2")
    if conn.get("audio_in"):
        argv.append("/microphone:sys:pulse")

    if conn.get("redirect_usb_storage"):
        try:
            os.makedirs(USB_SHARE, exist_ok=True)
        except OSError:
            pass        # created at boot by tmpfiles; never block a connection
        argv.append("/drive:USB,%s" % USB_SHARE)
    if conn.get("redirect_usb_devices"):
        argv.append("/usb:auto")
    if conn.get("redirect_smartcard"):
        argv.append("/smartcard")
    if conn.get("redirect_printers"):
        argv.append("/printer")

    # --- resilience ----------------------------------------------------------
    if conn.get("auto_reconnect"):
        argv += ["+auto-reconnect", "/auto-reconnect-max-retries:20"]
    # Heartbeat PDUs are on by default in FreeRDP 3, but state it explicitly:
    # this is the mechanism that lets the server confirm the client is alive,
    # and it should not silently change with a future default.
    argv.append("+heartbeat")
    argv.append("/timeout:20000")

    layout = (device.get("keyboard_layout") or "").strip().lower()
    layout_id = KEYBOARD_LAYOUT_IDS.get(layout)
    if layout_id is None and layout.startswith("0x"):
        layout_id = layout          # an explicit Windows layout id
    if layout_id:
        argv.append("/kbd:layout:%s" % layout_id)

    if conn.get("app"):
        argv.append("/app:program:%s" % conn["app"])

    argv.append("/log-level:%s" % ("DEBUG" if debug else "WARN"))

    for extra in conn.get("extra_args") or []:
        if isinstance(extra, str) and extra.strip():
            argv.append(extra.strip())

    return argv, stdin_text

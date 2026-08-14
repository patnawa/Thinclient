# ThinClient UI/UX baseline

This document records the 1.3 fleet UI decisions so later work can improve the
experience without accidentally restoring technician-oriented clutter to the
normal-user path.

## Implemented baseline

1. A connection is a full-width, single-click workspace card. The main screen
   shows a friendly name and description, never its raw host or port.
2. The header reports link state, negotiated speed, and IP. The status banner
   reports ready/offline state plus USB cache hit, network boot, save progress,
   or verified completion.
3. Connection startup has bounded DNS and TCP checks followed by visible
   Checking network, Contacting server, Authenticating, and Starting desktop
   stages. Cancel remains available until the client window opens.
4. Normal users see only workspace cards, Connect, Help, Admin, and Power.
   Settings, writable network controls, diagnostics, and Terminal are reached
   through the administrator gate.
5. Connection editing is split into Basic and Advanced. Basic covers identity,
   endpoint, sign-in, and display. Advanced covers performance, security,
   redirection, RemoteApp, reconnect, and extra arguments.
6. Network tests present short PASS/FAILED/SKIPPED rows. The complete
   credential-free report remains copyable under Technical details.
7. Help exposes version, Lite/Full profile, hostname, IP, cache state, and last
   error. It can copy a credential-free support report, run the public network
   test, and generate an offline QR support code when `qrencode` is installed.
8. Connection failures offer Choose another, Run network test, and Try again.
   Non-retryable credential failures deliberately omit Try again and forget any
   transient password.
9. Kiosk auto-connect uses a visible five-second countdown with Connect now and
   Cancel. Configuration reloads cancel stale countdowns.
10. The dark high-contrast theme uses larger text, large primary actions,
    visible keyboard focus, and no animations. The rendering gate includes
    1024×768 for older monitors.
11. `group` and `description` connection fields support sections such as
    Desktops, Applications, and Support. Missing values retain safe friendly
    defaults.
12. The four README screenshots are generated from deterministic demo data by
    `build/uitest.sh`; no workstation identity or customer endpoint is captured.

## Interaction and privacy rules

- Do not put a password, username, domain, or raw configuration dictionary in a
  report, QR code, status banner, tooltip, or worker thread snapshot.
- Main-screen copy is for an end user. Endpoint, route, driver, and protocol
  details belong in Help/Network technical details or administrator pages.
- Every long operation needs visible state and a bounded or cancellable path.
- A remembered session password is memory-only and must never enter `cfg`,
  `TCCONF`, logs, or central configuration.
- Preserve keyboard use: cards and primary buttons must be reachable by Tab and
  Enter; F1 opens Help and F2 opens Admin.
- Keep motion disabled. Do not require hover, color alone, or a mouse to
  understand or complete the main connection flow.

## Verification after UI changes

Run:

```bash
python3 -m unittest discover -s tests -v
bash build/check.sh
bash build/uitest.sh out/ui.png manager
TC_UI_SCREEN=1024x768 bash build/uitest.sh out/ui-low.png manager
for mode in settings about network-test credentials admin progress error; do
  bash build/uitest.sh "out/$mode.png" "$mode"
done
```

Visually check that text is not clipped, raw endpoints remain off the main
screen, dialogs fit 1024×768, technical details are expandable, focus is
visible, and the error path still exposes all three recovery choices.

## Next evidence-driven improvements

- Test with representative 1024×768 VGA/LCD panels and keyboard-only users,
  not only virtual rendering.
- Add automated accessibility-name inspection and contrast measurements.
- Add localization only after every fixed-width label and support report field
  is made translation-safe.
- Consider fleet-assigned friendly icons only if they remain clear on old GPUs
  and do not add a large bitmap/theme dependency to Lite.
- Measure task completion: time from boot to session, credential retry rate,
  network-test completion, and cache-state support calls. Record only aggregate
  non-secret events if telemetry is introduced.
- Add a connection search/filter only when real fleets exceed what grouped
  cards can scan comfortably; avoid adding permanent complexity for small sites.
- Validate screen-reader behavior before claiming screen-reader support. The
  current baseline provides accessible names and keyboard focus but does not
  make that untested claim.

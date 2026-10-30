#!/usr/bin/env python3
"""
pi_daily_report.py

Daily laptop-side watchdog for the lab test bench (Raspberry Pi over
SSH/Tailscale). Once a day (Windows Task Scheduler, see the bottom of this
docstring) it:

1. Copies every new or grown log file of the active battery from the Pi
   (/media/pi/LOGBATTEST/<battery_id>/) into
   C:\\Users\\TPKam\\Documents\\BatPi_Download\\LOGBATTEST\\<battery_id>\\ .
2. Checks whether the full plan is still healthy, using only files the Pi
   already writes (_checkpoint.json, _block_summary.csv, _sweep_points.csv,
   _events.csv), the systemd journal, disk space and Pi memory.
3. Mails a short status report ("OK" or "ACTIE NODIG" in the subject) via
   Gmail SMTP.

Stdlib only, so it runs on any Python 3.8+ (no pandas needed).

SSH: uses the `BatteryTestPi` host alias from ~/.ssh/config (key-based, no
password). Gmail: needs an app password stored once in Windows Credential
Manager — never in this file or in git:

    cmdkey /generic:pi_daily_report /user:<gmail address> /pass

(cmdkey asks for the password without echoing it.)

Recipients: pi_daily_report_config.json next to this script, gitignored. Copy
pi_daily_report_config.example.json and fill in the addresses.

Run by hand:
    python pi_daily_report.py              # sync + mail
    python pi_daily_report.py --no-mail    # sync + print the report
    python pi_daily_report.py --no-sync --no-mail

Scheduled task (registered once, see analytics/CLAUDE.md): "PiDailyReport",
daily 08:00, runs as soon as possible after a missed start (laptop asleep).
"""
import argparse
import csv
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import html
import io
import json
import os
import shutil
import smtplib
import subprocess
import sys
import tarfile
from email.message import EmailMessage

BATTERY_ID = "ul18_12_unit3"
SSH_HOST = "BatteryTestPi"
REMOTE_ROOT = "/media/pi/LOGBATTEST"
LOCAL_ROOT = r"C:\Users\TPKam\Documents\BatPi_Download\LOGBATTEST"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pi_daily_report_config.json")
CRED_TARGET = "pi_daily_report"   # Windows Credential Manager entry (cmdkey /generic:...)

# Thresholds
HEARTBEAT_MAX_AGE_MIN = 30        # == UNPLANNED_GAP_S on the Pi
NEWEST_FILE_MAX_AGE_MIN = 90      # longest stage without a growing log is the 1 h rest
DISK_MIN_FREE_GB = 5
PI_RSS_MAX_MB = 1500              # the July OOM kill happened at ~3.3 GB
SOH_JUMP_UP_PCT = 5               # SOH rising more than this between blocks is suspicious
SWEEP_SOC_TOL_PCT = 5             # |soc_cc_pct - target_soc_pct| tolerance
STOP_SOH_PCT = 50

SSH_EXE = (r"C:\Windows\System32\OpenSSH\ssh.exe"
           if os.path.exists(r"C:\Windows\System32\OpenSSH\ssh.exe") else "ssh")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]

# Runs on the Pi (python3 -), prints one JSON object. Read-only.
REMOTE_PROBE = r'''
import json, os, subprocess, time, datetime
d = os.path.join(%(root)r, %(bid)r)
out = {"now": datetime.datetime.now().isoformat(timespec="seconds"),
       "now_epoch": time.time(), "dir_exists": os.path.isdir(d), "files": {}}
def read(name):
    p = os.path.join(d, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    return None
for name in ("_checkpoint.json", "_soh_reference.json", "_block_summary.csv",
             "_sweep_points.csv", "_events.csv"):
    out["files"][name] = read(name)
out["listing"] = []
if out["dir_exists"]:
    for e in os.scandir(d):
        if e.is_file():
            st = e.stat()
            out["listing"].append([e.name, st.st_size, st.st_mtime])
def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=60).stdout
    except Exception as ex:
        return "ERR " + str(ex)
out["service"] = sh("systemctl is-active battery-dashboard").strip()
out["journal"] = sh("journalctl -u battery-dashboard --since '-24h' --no-pager "
                    "| grep -E 'HALTED|UNPLANNED GAP|Traceback|Error|WARNING|timeout' | tail -40")
du = os.statvfs(%(root)r)
out["disk_free_gb"] = du.f_bavail * du.f_frsize / 1e9
out["python_rss_mb"] = sh("ps -C python3 -o rss= | sort -n | tail -1").strip()
out["uptime"] = sh("uptime -p").strip()
print(json.dumps(out))
'''


# ---------------------------------------------------------------- helpers
def ssh(args, stdin=None, binary=False, timeout=600):
    return subprocess.run([SSH_EXE, *SSH_OPTS, SSH_HOST, *args], input=stdin,
                          capture_output=True, text=not binary, timeout=timeout)


def read_gmail_app_password():
    """Generic credential from Windows Credential Manager (cmdkey /generic:...)."""
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD),
                    ("TargetName", wt.LPWSTR), ("Comment", wt.LPWSTR),
                    ("LastWritten", wt.FILETIME), ("CredentialBlobSize", wt.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                    ("UserName", wt.LPWSTR)]
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    pcred = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(CRED_TARGET, 1, 0, ctypes.byref(pcred)):  # 1 = CRED_TYPE_GENERIC
        raise RuntimeError(f"Geen credential '{CRED_TARGET}' in Windows Referentiebeheer "
                           f"(zie docstring: cmdkey /generic:{CRED_TARGET} ...)")
    try:
        c = pcred.contents
        blob = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
        return c.UserName, blob.decode("utf-16-le")
    finally:
        advapi.CredFree(pcred)


def parse_csv(text):
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text)))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- sync
def sync(listing):
    """Copy files that are missing locally or whose size differs, via tar over ssh."""
    local_dir = os.path.join(LOCAL_ROOT, BATTERY_ID)
    os.makedirs(local_dir, exist_ok=True)
    todo = [name for name, size, _ in listing
            if not os.path.exists(os.path.join(local_dir, name))
            or os.path.getsize(os.path.join(local_dir, name)) != size]
    if not todo:
        return 0, 0.0, None
    # file names are generated by backend.py (no spaces/quotes); pass them on stdin
    remote = f"cd {REMOTE_ROOT}/{BATTERY_ID} && tar cf - --warning=no-file-changed -T -"
    r = ssh([remote], stdin=("\n".join(todo) + "\n").encode(), binary=True, timeout=3600)
    # GNU tar exits 1 when a file grew while being read (the active log always
    # does); the archive is still valid, the next run picks up the rest.
    if r.returncode not in (0, 1) or not r.stdout:
        return 0, 0.0, r.stderr.decode(errors="replace").strip()[-500:]
    n, nbytes = 0, 0
    with tarfile.open(fileobj=io.BytesIO(r.stdout), mode="r:") as tf:
        for m in tf.getmembers():
            if not m.isfile() or os.path.basename(m.name) != m.name:
                continue
            tmp = os.path.join(local_dir, m.name + ".part")
            with tf.extractfile(m) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(tmp, os.path.join(local_dir, m.name))
            n += 1
            nbytes += m.size
    return n, nbytes / 1e6, None


# ---------------------------------------------------------------- checks
def analyse(p):
    """Returns (problems, info_lines, tables). problems = list of strings."""
    problems, info, tables = [], [], {}
    now = dt.datetime.fromisoformat(p["now"])

    if not p["dir_exists"]:
        problems.append(f"Map {REMOTE_ROOT}/{BATTERY_ID} bestaat niet op de Pi (USB-stick niet gemount?).")
        return problems, info, tables

    if p["service"] != "active":
        problems.append(f"Service battery-dashboard is '{p['service']}', niet 'active'.")

    blocks = parse_csv(p["files"]["_block_summary.csv"])
    ref = json.loads(p["files"]["_soh_reference.json"]) if p["files"]["_soh_reference.json"] else None
    finished = bool(blocks) and (fnum(blocks[-1].get("soh_pct_of_ref")) or 999) <= STOP_SOH_PCT

    cp_txt = p["files"]["_checkpoint.json"]
    if cp_txt:
        cp = json.loads(cp_txt)
        info.append(f"Positie: blok {cp.get('block_index')}, stage <b>{cp.get('stage')}</b>"
                    + (f", cyclus {cp['cycle']}" if cp.get("cycle") else "")
                    + f" (stage gestart {cp.get('updated_at', '?')[:16]})")
        la = cp.get("last_alive")
        if la:
            age = (now - dt.datetime.fromisoformat(la)).total_seconds() / 60
            info.append(f"Laatste heartbeat: {age:.0f} min geleden")
            if age > HEARTBEAT_MAX_AGE_MIN:
                problems.append(f"Heartbeat is {age:.0f} min oud (> {HEARTBEAT_MAX_AGE_MIN}): "
                                "het full plan lijkt stil te staan.")
    elif finished:
        info.append("Geen checkpoint en laatste blok ≤ 50 % SOH: <b>campagne afgerond</b>.")
    else:
        problems.append("Geen _checkpoint.json: full plan draait niet (en is niet afgerond).")

    if p["listing"]:
        newest = max(p["listing"], key=lambda r: r[2])
        age = (p["now_epoch"] - newest[2]) / 60
        info.append(f"Nieuwste logbestand: {newest[0]} ({age:.0f} min geleden bijgewerkt)")
        if age > NEWEST_FILE_MAX_AGE_MIN and not finished:
            problems.append(f"Al {age:.0f} min geen logbestand bijgewerkt.")

    if p["disk_free_gb"] < DISK_MIN_FREE_GB:
        problems.append(f"USB-stick bijna vol: {p['disk_free_gb']:.1f} GB vrij.")
    info.append(f"Vrij op USB: {p['disk_free_gb']:.0f} GB · Pi {p['uptime']}")

    rss = fnum(p["python_rss_mb"])
    if rss is not None:
        rss_mb = rss / 1024
        info.append(f"Geheugen dashboard-proces: {rss_mb:.0f} MB")
        if rss_mb > PI_RSS_MAX_MB:
            problems.append(f"Dashboard-proces gebruikt {rss_mb:.0f} MB (geheugenlek? zie OOM-incident juli).")

    # journal
    jl = [l for l in p["journal"].splitlines() if l.strip()]
    serious = [l for l in jl if any(k in l for k in ("HALTED", "UNPLANNED GAP", "Traceback"))]
    if serious:
        problems.append(f"{len(serious)} ernstige regel(s) in de journal van de afgelopen 24 h.")
    if jl:
        tables["Journal (24 h, gefilterd, laatste 15)"] = (["regel"], [[l[-220:]] for l in jl[-15:]])

    # events (unplanned gaps)
    events = parse_csv(p["files"]["_events.csv"])
    recent = [e for e in events
              if e.get("timestamp") and (now - dt.datetime.fromisoformat(e["timestamp"][:19])).days < 1]
    if recent:
        problems.append(f"{len(recent)} onverwachte onderbreking(en) in de afgelopen 24 h (_events.csv).")
    if events:
        tables["Onderbrekingen (_events.csv)"] = (
            ["tijd", "event", "blok", "stage", "gap h"],
            [[e.get("timestamp", "")[:16], e.get("event"), e.get("block"), e.get("stage"),
              e.get("gap_h")] for e in events[-10:]])

    # SOH per block
    if ref:
        info.append(f"SOH-referentie (blok {ref.get('block')}): {fnum(ref.get('capacity_ah')):.2f} Ah")
    if blocks:
        rows, prev = [], None
        for b in blocks:
            pct = fnum(b.get("soh_pct_of_ref"))
            to = int(fnum(b.get("degr_charge_timeouts")) or 0)
            if prev is not None and pct is not None and pct - prev > SOH_JUMP_UP_PCT:
                problems.append(f"SOH stijgt van {prev:.1f} naar {pct:.1f} % in blok {b['block']} "
                                "(rust/onderbreking? zie non-monotonie unit2).")
            if to > 0 and b is blocks[-1]:
                problems.append(f"Blok {b['block']}: {to} degradatielading(en) haalden de taper niet.")
            rows.append([b.get("block"), f"{fnum(b.get('soh_c5_ah')) or 0:.2f}",
                         f"{pct:.1f}" if pct is not None else "", to,
                         b.get("unplanned_gap_h") or ""])
            prev = pct if pct is not None else prev
        tables["SOH per blok (_block_summary.csv)"] = (
            ["blok", "C/5 Ah", "SOH %", "laad-timeouts", "gap h"], rows)
        # crude linear projection to the 50 % stop
        pts = [(int(b["block"]), fnum(b["soh_pct_of_ref"])) for b in blocks
               if fnum(b.get("soh_pct_of_ref")) is not None]
        if len(pts) >= 3 and not finished:
            n = len(pts)
            mx = sum(x for x, _ in pts) / n
            my = sum(y for _, y in pts) / n
            sxx = sum((x - mx) ** 2 for x, _ in pts)
            slope = sum((x - mx) * (y - my) for x, y in pts) / sxx if sxx else 0
            if slope < 0:
                stop_block = mx + (STOP_SOH_PCT - my) / slope
                info.append(f"Lineaire schatting: 50 % SOH rond blok {stop_block:.0f} "
                            f"({slope:.1f} %-punt per blok)")

    # sweep points of the most recent block
    sweep = parse_csv(p["files"]["_sweep_points.csv"])
    if sweep:
        last_block = sweep[-1].get("block")
        rows = []
        for s in [s for s in sweep if s.get("block") == last_block]:
            tgt, cc = fnum(s.get("target_soc_pct")), fnum(s.get("soc_cc_pct"))
            cut = str(s.get("step_hit_cutoff")).lower() == "true"
            flag = ""
            if tgt is not None and cc is not None and abs(cc - tgt) > SWEEP_SOC_TOL_PCT:
                flag = "⚠ afwijking"
                problems.append(f"Sweep blok {last_block}: doel {tgt:.0f} %, werkelijk {cc:.0f} %.")
            if cut:
                flag = "cutoff"
                if tgt is not None and tgt > 20:
                    problems.append(f"Sweep blok {last_block}: cutoff al bij doel {tgt:.0f} %.")
            rows.append([s.get("run"), s.get("target_soc_pct"),
                         f"{cc:.1f}" if cc is not None else "", s.get("ocv_after_rest"),
                         s.get("profile_net_ah"), flag])
        tables[f"SoC-sweep blok {last_block} (_sweep_points.csv)"] = (
            ["run", "doel %", "CC %", "OCV", "profiel Ah", ""], rows)

    return problems, info, tables


# ---------------------------------------------------------------- report
def render(problems, info, tables, sync_line):
    css = "font-family:Segoe UI,Arial,sans-serif;font-size:14px"
    td = "border:1px solid #ccc;padding:2px 6px;font-size:13px"
    parts = [f'<div style="{css}">']
    if problems:
        parts.append('<p style="color:#b42318"><b>Actie nodig:</b></p><ul>')
        parts += [f"<li>{html.escape(x)}</li>" for x in problems]
        parts.append("</ul>")
    else:
        parts.append('<p style="color:#067647"><b>Alles OK.</b></p>')
    parts.append("<ul>" + "".join(f"<li>{x}</li>" for x in info + [sync_line]) + "</ul>")
    for title, (head, rows) in tables.items():
        parts.append(f"<p><b>{html.escape(title)}</b></p>"
                     '<table style="border-collapse:collapse">'
                     + "<tr>" + "".join(f'<th style="{td}">{html.escape(str(h))}</th>' for h in head) + "</tr>"
                     + "".join("<tr>" + "".join(f'<td style="{td}">{html.escape(str(c))}</td>' for c in r)
                               + "</tr>" for r in rows)
                     + "</table>")
    parts.append(f'<p style="color:#888;font-size:12px">pi_daily_report.py · {BATTERY_ID} · '
                 f'{dt.datetime.now():%Y-%m-%d %H:%M}</p></div>')
    return "\n".join(parts)


def load_mail_to():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)["mail_to"]


def send_mail(subject, body_html, body_text):
    user, pwd = read_gmail_app_password()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(load_mail_to())
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pwd)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sync", action="store_true")
    ap.add_argument("--no-mail", action="store_true")
    args = ap.parse_args()

    probe = ssh(["python3", "-"], stdin=REMOTE_PROBE % {"root": REMOTE_ROOT, "bid": BATTERY_ID},
                timeout=120)
    if probe.returncode != 0:
        problems = [f"Pi niet bereikbaar via SSH ({SSH_HOST}): "
                    f"{probe.stderr.strip()[-300:] or 'exit ' + str(probe.returncode)}"]
        info, tables, sync_line = ["Is de Pi aan, en zit hij (en deze laptop) op Tailscale?"], {}, "Sync: overgeslagen"
    else:
        p = json.loads(probe.stdout)
        problems, info, tables = analyse(p)
        if args.no_sync:
            sync_line = "Sync: overgeslagen (--no-sync)"
        else:
            n, mb, err = sync(p["listing"])
            if err:
                problems.append(f"Sync mislukt: {err}")
                sync_line = "Sync: mislukt"
            else:
                sync_line = (f"Sync: {n} bestand(en), {mb:.1f} MB naar "
                             f"{os.path.join(LOCAL_ROOT, BATTERY_ID)}")

    status = "ACTIE NODIG" if problems else "OK"
    subject = f"[Accutest {BATTERY_ID}] {status} – {dt.date.today():%d-%m-%Y}"
    body_html = render(problems, info, tables, sync_line)
    body_text = subject + "\n\n" + "\n".join(["- " + x for x in problems]
                                             + [x.replace("<b>", "").replace("</b>", "") for x in info]
                                             + [sync_line])

    os.makedirs(LOCAL_ROOT, exist_ok=True)
    with open(os.path.join(LOCAL_ROOT, f"_daily_report_{BATTERY_ID}.html"), "w", encoding="utf-8") as f:
        f.write(body_html)

    if args.no_mail:
        print(body_text)
        return 0
    try:
        send_mail(subject, body_html, body_text)
    except Exception as ex:
        with open(os.path.join(LOCAL_ROOT, "_daily_report_errors.log"), "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat()} mail failed: {ex!r}\n")
        print(f"Mail mislukt: {ex}", file=sys.stderr)
        return 1
    print(f"Mail verstuurd: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

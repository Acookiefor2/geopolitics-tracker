# launch.py
"""
🚀 ONE-CLICK LAUNCHER for the Geopolitical War Room Dashboard

Just run: python launch.py

This script handles EVERYTHING:
  - Creates virtual environment if missing
  - Installs all dependencies
  - Verifies Ollama is running and pulls the model
  - Seeds test data so you see the map immediately
  - Starts the worker in the background
  - Launches the Streamlit dashboard
  - Opens your browser
  - Cleans up on Ctrl+C
"""

import os
import sys
import subprocess
import time
import platform
import webbrowser
import signal
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.resolve()
VENV_DIR    = PROJECT_DIR / "venv"
IS_WINDOWS  = platform.system() == "Windows"

# Venv-relative paths
if IS_WINDOWS:
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
    VENV_PIP    = VENV_DIR / "Scripts" / "pip.exe"
    VENV_STREAMLIT = VENV_DIR / "Scripts" / "streamlit.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"
    VENV_PIP    = VENV_DIR / "bin" / "pip"
    VENV_STREAMLIT = VENV_DIR / "bin" / "streamlit"

REQUIRED_PACKAGES = [
    "streamlit>=1.35.0",
    "pydeck>=0.9.1",
    "feedparser>=6.0.11",
    "ollama>=0.2.1",
    "geopy>=2.4.1",
    "requests>=2.32.3",
    "pandas>=2.2.2",
    "python-dateutil>=2.9.0",
]

DASHBOARD_URL = "http://localhost:8501"
OLLAMA_MODEL  = "mistral"   # change to "phi3" if low on RAM


# ─── Pretty printing ──────────────────────────────────────────────────────────

def banner(text: str):
    print("\n" + "═" * 60)
    print(f"  {text}")
    print("═" * 60)


def step(emoji: str, msg: str):
    print(f"{emoji}  {msg}")


def success(msg: str):
    print(f"✅  {msg}")


def warn(msg: str):
    print(f"⚠️   {msg}")


def fail(msg: str):
    print(f"❌  {msg}")


# ─── Setup steps ──────────────────────────────────────────────────────────────

def check_python_version():
    if sys.version_info < (3, 10):
        fail(f"Python 3.10+ required. You have {sys.version}")
        sys.exit(1)
    success(f"Python {sys.version_info.major}.{sys.version_info.minor} detected")


def create_venv():
    if VENV_DIR.exists() and VENV_PYTHON.exists():
        success("Virtual environment already exists")
        return
    step("🔧", "Creating virtual environment (this takes ~10 seconds)...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    success("Virtual environment created")


def install_dependencies():
    step("📦", "Checking dependencies...")

    # Quick check — try importing a key package
    check = subprocess.run(
        [str(VENV_PYTHON), "-c",
         "import streamlit, pydeck, pandas, feedparser, ollama, geopy"],
        capture_output=True,
    )
    if check.returncode == 0:
        success("All dependencies already installed")
        return

    step("📥", "Installing dependencies (this can take 1-3 minutes the first time)...")

    # Use 'python -m pip' instead of pip.exe directly — avoids Windows file-lock issues.
    # Skip the pip self-upgrade entirely; it's optional and causes more problems than it solves.
    try:
        subprocess.check_call(
            [str(VENV_PYTHON), "-m", "pip", "install",
             "--disable-pip-version-check", "--quiet", *REQUIRED_PACKAGES]
        )
    except subprocess.CalledProcessError:
        # If quiet mode failed, retry with full output so the user can see what broke
        warn("Quiet install failed — retrying with verbose output...")
        subprocess.check_call(
            [str(VENV_PYTHON), "-m", "pip", "install",
             "--disable-pip-version-check", *REQUIRED_PACKAGES]
        )

    success("All dependencies installed")


def check_ollama():
    step("🤖", "Checking Ollama...")

    # Is ollama CLI available?
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        fail("Ollama is not installed or not running!")
        print()
        print("   📥 Install Ollama from: https://ollama.com/download")
        print("   Then re-run this script.")
        print()
        input("   Press ENTER to exit...")
        sys.exit(1)

    if result.returncode != 0:
        fail("Ollama CLI returned an error. Is the Ollama app running?")
        print()
        if IS_WINDOWS:
            print("   👉 Open the Ollama app from your Start Menu, then re-run this script.")
        else:
            print("   👉 Run 'ollama serve' in another terminal, then re-run this script.")
        print()
        input("   Press ENTER to exit...")
        sys.exit(1)

    # Is the model pulled?
    if OLLAMA_MODEL not in result.stdout:
        step("⬇️", f"Pulling Ollama model '{OLLAMA_MODEL}' (one-time ~4GB download)...")
        subprocess.check_call(["ollama", "pull", OLLAMA_MODEL])
        success(f"Model '{OLLAMA_MODEL}' downloaded")
    else:
        success(f"Ollama model '{OLLAMA_MODEL}' is ready")


def seed_demo_data():
    """Insert demo events so the user sees something on the map immediately."""
    step("🌱", "Seeding demo events for instant visualization...")

    seed_script = '''
import sys
sys.path.insert(0, ".")
from db import init_db, insert_event, fetch_event_count

init_db()

# Only seed if DB is empty
if fetch_event_count() == 0:
    demo = [
        {"source_country":"Russia","source_city":"Moscow","source_lat":55.7558,"source_lon":37.6173,
         "target_country":"Ukraine","target_city":"Kyiv","target_lat":50.4501,"target_lon":30.5234,
         "event_type":"Military","urgency":9,
         "summary":"[DEMO] Missile strikes reported targeting critical infrastructure in Kyiv.",
         "raw_headline":"DEMO_SEED_1","source_url":"demo://1"},
        {"source_country":"China","source_city":"Beijing","source_lat":39.9042,"source_lon":116.4074,
         "target_country":"Taiwan","target_city":"Taipei","target_lat":25.0330,"target_lon":121.5654,
         "event_type":"Diplomatic","urgency":6,
         "summary":"[DEMO] Beijing issues formal statement regarding Taiwan Strait activities.",
         "raw_headline":"DEMO_SEED_2","source_url":"demo://2"},
        {"source_country":"North Korea","source_city":"Pyongyang","source_lat":39.0392,"source_lon":125.7625,
         "target_country":"South Korea","target_city":"Seoul","target_lat":37.5665,"target_lon":126.9780,
         "event_type":"Cyber","urgency":7,
         "summary":"[DEMO] Suspected cyber intrusion attempt on government infrastructure detected.",
         "raw_headline":"DEMO_SEED_3","source_url":"demo://3"},
        {"source_country":"Iran","source_city":"Tehran","source_lat":35.6892,"source_lon":51.3890,
         "target_country":"Israel","target_city":"Jerusalem","target_lat":31.7683,"target_lon":35.2137,
         "event_type":"Military","urgency":8,
         "summary":"[DEMO] Escalating regional tensions with reported drone movements.",
         "raw_headline":"DEMO_SEED_4","source_url":"demo://4"},
        {"source_country":"United States","source_city":"Washington","source_lat":38.8951,"source_lon":-77.0364,
         "target_country":"China","target_city":"Beijing","target_lat":39.9042,"target_lon":116.4074,
         "event_type":"Economic","urgency":5,
         "summary":"[DEMO] New trade tariff package announced targeting technology exports.",
         "raw_headline":"DEMO_SEED_5","source_url":"demo://5"},
        {"source_country":"France","source_city":"Paris","source_lat":48.8566,"source_lon":2.3522,
         "target_country":"Mali","target_city":"Bamako","target_lat":12.6392,"target_lon":-8.0029,
         "event_type":"Diplomatic","urgency":4,
         "summary":"[DEMO] Diplomatic recall amid regional security cooperation review.",
         "raw_headline":"DEMO_SEED_6","source_url":"demo://6"},
    ]
    for e in demo:
        insert_event(e)
    print(f"Seeded {len(demo)} demo events")
else:
    print(f"Database has {fetch_event_count()} events, skipping seed")
'''
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", seed_script],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_DIR),
    )
    if result.returncode == 0:
        success(result.stdout.strip())
    else:
        warn(f"Seed step skipped: {result.stderr.strip()}")


# ─── Process management ──────────────────────────────────────────────────────

processes = []


def start_worker():
    step("⚙️", "Starting background worker (RSS scraper + AI processor)...")
    log_file = open(PROJECT_DIR / "worker.log", "w")
    proc = subprocess.Popen(
        [str(VENV_PYTHON), "worker.py"],
        cwd=str(PROJECT_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0,
    )
    processes.append(("worker", proc, log_file))
    success(f"Worker started (PID {proc.pid}) — logs → worker.log")


def start_dashboard():
    step("🗺️", "Launching Streamlit dashboard...")
    log_file = open(PROJECT_DIR / "dashboard.log", "w")
    proc = subprocess.Popen(
        [
            str(VENV_STREAMLIT), "run", "app.py",
            "--server.port", "8501",
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=str(PROJECT_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0,
    )
    processes.append(("dashboard", proc, log_file))
    success(f"Dashboard started (PID {proc.pid}) — logs → dashboard.log")


def wait_for_dashboard(timeout: int = 30):
    """Poll the dashboard URL until it responds."""
    import urllib.request
    import urllib.error

    step("⏳", "Waiting for dashboard to come online...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(DASHBOARD_URL, timeout=2)
            success("Dashboard is live!")
            return True
        except (urllib.error.URLError, ConnectionResetError):
            time.sleep(1)
    warn("Dashboard didn't respond in time, opening browser anyway...")
    return False


def open_browser():
    step("🌐", f"Opening {DASHBOARD_URL} in your browser...")
    webbrowser.open(DASHBOARD_URL)


def shutdown(signum=None, frame=None):
    print("\n")
    banner("🛑  SHUTTING DOWN")
    for name, proc, log_file in processes:
        if proc.poll() is None:
            step("⏹️", f"Stopping {name} (PID {proc.pid})...")
            try:
                if IS_WINDOWS:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, Exception):
                proc.kill()
        log_file.close()
    success("All processes stopped cleanly. Bye! 👋")
    sys.exit(0)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    banner("⚡  GEOPOLITICAL WAR ROOM — ONE-CLICK LAUNCHER  ⚡")

    # Register signal handler
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Verify we're in the right folder
    required_files = ["app.py", "worker.py", "db.py", "config.py", "geocoder.py"]
    missing = [f for f in required_files if not (PROJECT_DIR / f).exists()]
    if missing:
        fail(f"Missing required files: {', '.join(missing)}")
        fail(f"Make sure launch.py is inside the same folder as app.py")
        input("\nPress ENTER to exit...")
        sys.exit(1)

    try:
        banner("STEP 1/6 — Python Environment")
        check_python_version()
        create_venv()

        banner("STEP 2/6 — Dependencies")
        install_dependencies()

        banner("STEP 3/6 — Ollama AI Backend")
        check_ollama()

        banner("STEP 4/6 — Demo Data")
        seed_demo_data()

        banner("STEP 5/6 — Background Worker")
        start_worker()

        banner("STEP 6/6 — Dashboard")
        start_dashboard()
        wait_for_dashboard()
        open_browser()

        banner("🎉  ALL SYSTEMS GO")
        print()
        print(f"   📊 Dashboard:  {DASHBOARD_URL}")
        print(f"   📋 Worker log: {PROJECT_DIR / 'worker.log'}")
        print(f"   📋 Dash log:   {PROJECT_DIR / 'dashboard.log'}")
        print()
        print("   The worker is fetching real news in the background.")
        print("   New events will appear on the map automatically.")
        print()
        print("   ⌨️  Press Ctrl+C in this window to stop everything.")
        print()

        # Block forever until Ctrl+C
        while True:
            time.sleep(1)
            # Check if any process died unexpectedly
            for name, proc, _ in processes:
                if proc.poll() is not None:
                    warn(f"{name} process exited unexpectedly! Check {name}.log")
                    shutdown()

    except KeyboardInterrupt:
        shutdown()
    except Exception as exc:
        fail(f"Setup failed: {exc}")
        import traceback
        traceback.print_exc()
        input("\nPress ENTER to exit...")
        shutdown()


if __name__ == "__main__":
    main()
"""Start the BoS Verifier and open it in the browser.

This is what the "Start BoS Verifier" shortcut runs.  It differs from running
app.py directly in two ways: it opens the browser once the server is actually
answering, and if the app is already running it simply opens that rather than
failing on a busy port.
"""
import socket
import sys
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 5000
URL = f"http://{HOST}:{PORT}"


def _is_listening(timeout=0.3):
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((HOST, PORT)) == 0


def _open_when_ready():
    """Wait for the server to answer, then open a browser at it."""
    for _ in range(160):          # up to about forty seconds
        if _is_listening():
            webbrowser.open(URL)
            return
        time.sleep(0.25)
    print(f"The server did not start in time. Try opening {URL} by hand.")


def main():
    if _is_listening():
        print(f"The BoS Verifier is already running. Opening {URL}")
        webbrowser.open(URL)
        return 0

    try:
        from app import app
        from bosverify.ocr import tesseract_available
    except ImportError as exc:
        print("Could not start: a required package is missing.")
        print(f"  {exc}")
        print()
        print("Install the dependencies first, from this folder:")
        print("  pip install -r requirements.txt")
        return 1

    if not tesseract_available():
        print("WARNING: Tesseract OCR was not found.")
        print("A BoS is a scan, so every text-based check needs it.")
        print("Install it, or set TESSERACT_CMD to its full path, then restart.")
        print()

    print(f"BoS Verifier is starting at {URL}")
    print("A browser window will open in a moment.")
    print("Leave this window open while you use the app; close it to stop.")
    print()
    threading.Thread(target=_open_when_ready, daemon=True).start()
    try:
        app.run(host=HOST, port=PORT, debug=False)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

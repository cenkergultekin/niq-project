"""User-clicked return from the system browser to the desktop window."""
import threading

focus_requested = threading.Event()
available = False

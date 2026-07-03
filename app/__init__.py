"""Streamlit UI package (dashboard + presentation helpers).

Declared as a real package (not an implicit namespace package) so imports like
``from app import ui`` resolve deterministically. Without this, Streamlit Cloud
could re-run the entry script (``dashboard.py``) after a git pull while keeping a
stale ``app.ui`` cached in ``sys.modules`` from process start — producing skew
errors such as ``module 'app.ui' has no attribute 'play_card_html'`` even though
the files on disk are consistent. A clean package boundary + a full app reboot
avoids that.
"""

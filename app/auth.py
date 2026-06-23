"""Password gate for the dashboard. Anyone who stumbles on the URL sees
only a password prompt; the comparison is constant-time and the password
lives in Streamlit secrets / env, never in code.

The gate is split into reusable pieces so a public landing page can render
*before* the prompt (see ``app/landing.py``):

  * ``is_authenticated()`` — true if the session is signed in (or carries a
    valid remember-me ``?k=`` token); pure check, renders nothing.
  * ``login_form()`` — the password box itself; ``st.stop()``s until entered.
  * ``require_password()`` — the original one-shot gate (check + form),
    kept for callers/tests that want the prompt with no landing in front.
"""

import hashlib
import hmac

import streamlit as st

from onesource.config import APP_PASSWORD


def _remember_token(expected: str) -> str:
    """Opaque token bookmarked in the URL so a sign-in survives reloads and
    home-screen launches without ever putting the password in the URL."""
    return hashlib.sha256(("osp:" + expected).encode()).hexdigest()[:32]


def is_authenticated() -> bool:
    """True if this session is already signed in, either via the in-session
    flag or a valid remember-me token. Renders nothing — safe to call before
    deciding whether to show the public landing page."""
    expected = APP_PASSWORD()
    if not expected:
        return False
    if st.session_state.get("authed"):
        return True
    if st.query_params.get("k") == _remember_token(expected):
        st.session_state["authed"] = True
        return True
    return False


def login_form() -> None:
    """Render the password box and stop the script until it's satisfied. On a
    correct password the session is marked authed and the URL is bookmarked."""
    expected = APP_PASSWORD()
    if not expected:
        st.error("APP_PASSWORD is not configured. Set it in Streamlit secrets.")
        st.stop()

    st.title("🔒")
    pw = st.text_input("Password", type="password", key="pw_input")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["authed"] = True
            st.query_params["k"] = _remember_token(expected)
            st.rerun()
        else:
            st.error("Nope.")
    st.stop()


def require_password() -> bool:
    """One-shot gate: pass straight through if authenticated, otherwise show
    the password box and stop. (Landing page is bypassed.)"""
    if is_authenticated():
        return True
    login_form()
    return False

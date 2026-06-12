"""Password gate for the dashboard. Anyone who stumbles on the URL sees
only a password prompt; the comparison is constant-time and the password
lives in Streamlit secrets / env, never in code."""

import hmac

import streamlit as st

from onesource.config import APP_PASSWORD


def require_password() -> bool:
    expected = APP_PASSWORD()
    if not expected:
        st.error("APP_PASSWORD is not configured. Set it in Streamlit secrets.")
        st.stop()

    if st.session_state.get("authed"):
        return True

    st.title("🔒")
    pw = st.text_input("Password", type="password", key="pw_input")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Nope.")
    st.stop()

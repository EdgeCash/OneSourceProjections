"""Password gate for the dashboard. Anyone who stumbles on the URL sees
only a password prompt; the comparison is constant-time and the password
lives in Streamlit secrets / env, never in code."""

import hashlib
import hmac

import streamlit as st

from onesource.config import APP_PASSWORD


def require_password() -> bool:
    expected = APP_PASSWORD()
    if not expected:
        st.error("APP_PASSWORD is not configured. Set it in Streamlit secrets.")
        st.stop()

    token = hashlib.sha256(("osp:" + expected).encode()).hexdigest()[:32]
    if st.session_state.get("authed"):
        return True
    # remembered sign-in: ?k=<token> survives reloads / home-screen launches
    if st.query_params.get("k") == token:
        st.session_state["authed"] = True
        return True

    st.title("🔒")
    pw = st.text_input("Password", type="password", key="pw_input")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state["authed"] = True
            st.query_params["k"] = token  # bookmark the URL to stay signed in
            st.rerun()
        else:
            st.error("Nope.")
    st.stop()

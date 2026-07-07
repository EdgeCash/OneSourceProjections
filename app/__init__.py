"""Presentation helpers shared by the static-site build (scripts/build_static.py).

After the Streamlit app was retired, this package holds only the streamlit-free
rendering + theme code the static site reuses:

- ``app.ui``     — the Sharp Sheet / card HTML builders and formatters.
- ``app.theme``  — palette tokens + global CSS.
- ``app.assets`` — team logos / monogram fallbacks.

Declared as a real package (not an implicit namespace package) so ``from app
import ui`` resolves deterministically.
"""

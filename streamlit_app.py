import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

st.title("LinkedIn Profile API")
st.caption("Paste a LinkedIn profile URL, get back structured JSON — pure HTTP calls to "
           "LinkedIn's own API, no browser involved.")

# --- Primary flow: cookie + URL in one shot -------------------------------------
st.header("Look up a profile")
st.caption(
    "Paste a fresh `li_at`/`JSESSIONID` (DevTools → Application → Cookies → "
    "linkedin.com, from a normal browser login) together with the profile URL, and "
    "click once. Session setup and the lookup happen back-to-back in one request — "
    "LinkedIn's session checks were found, during development, to be sensitive to any "
    "gap between those two steps, so doing them separately is less reliable than this."
)

with st.form("bootstrap_and_scrape_form"):
    li_at = st.text_input("li_at", type="password")
    jsessionid = st.text_input("JSESSIONID", type="password")
    url = st.text_input("LinkedIn profile URL", placeholder="https://www.linkedin.com/in/some-person/")
    go_submitted = st.form_submit_button("Get Profile")

if go_submitted:
    if not li_at or not jsessionid or not url:
        st.error("li_at, JSESSIONID, and the profile URL are all required.")
    else:
        with st.spinner("Logging in and scraping..."):
            try:
                resp = requests.post(
                    f"{API_URL}/session/bootstrap-and-scrape",
                    json={"li_at": li_at, "jsessionid": jsessionid, "url": url},
                    timeout=90,
                )
                if resp.ok:
                    st.session_state["profile"] = resp.json()
                else:
                    st.session_state.pop("profile", None)
                    st.error(resp.json().get("error", "Something went wrong."))
            except requests.RequestException as e:
                st.session_state.pop("profile", None)
                st.error(f"Couldn't reach the API: {e}")

profile = st.session_state.get("profile")
if profile:
    view = st.radio("View", ["Formatted", "Raw JSON"], horizontal=True)

    if view == "Raw JSON":
        st.json(profile)
    else:
        col1, col2 = st.columns([1, 3])
        with col1:
            if profile.get("profile_image_url"):
                st.image(profile["profile_image_url"], width=150)
        with col2:
            st.subheader(profile.get("name") or "—")
            if profile.get("headline"):
                st.write(profile["headline"])
            if profile.get("location"):
                st.caption(f"Country: {profile['location']}")

        if profile.get("about"):
            st.markdown("### About")
            st.write(profile["about"])

        if profile.get("experience"):
            st.markdown("### Experience")
            for job in profile["experience"]:
                title_line = " · ".join(filter(None, [job.get("title"), job.get("company")]))
                st.markdown(f"**{title_line or '—'}**")
                meta = " · ".join(filter(None, [job.get("duration"), job.get("location")]))
                if meta:
                    st.caption(meta)
                if job.get("description"):
                    st.write(job["description"])
                st.markdown("---")

        if profile.get("education"):
            st.markdown("### Education")
            for edu in profile["education"]:
                st.markdown(f"**{edu.get('school') or '—'}**")
                line = " · ".join(filter(None, [edu.get("degree"), edu.get("duration")]))
                if line:
                    st.caption(line)

        if profile.get("skills"):
            st.markdown("### Skills")
            st.write(", ".join(profile["skills"]))

        if profile.get("certifications"):
            st.markdown("### Certifications")
            for cert in profile["certifications"]:
                st.write(f"- {cert}")

        if profile.get("languages"):
            st.markdown("### Languages")
            st.write(", ".join(profile["languages"]))

st.divider()

# --- Advanced: separate session setup, for automated/credential login ----------
with st.expander("Advanced: set up a session separately (credential login, or reuse a saved session)"):
    try:
        status = requests.get(f"{API_URL}/session/status", timeout=10).json()
        session_configured = status.get("configured", False)
    except requests.RequestException:
        session_configured = None

    if session_configured:
        st.success("A session is saved and ready to use.")
    elif session_configured is False:
        st.warning("No session saved yet.")
    else:
        st.error(f"Couldn't reach the API at {API_URL}.")

    st.caption(
        "Tries the first account in .env's LINKEDIN_ACCOUNTS via LinkedIn's own HTTP API "
        "(no browser). This is expected to get checkpointed in practice — see README."
    )
    if st.button("Try Auto-Login"):
        with st.spinner("Logging in via LinkedIn's API..."):
            try:
                resp = requests.post(f"{API_URL}/session/auto-login", timeout=30)
                if resp.ok:
                    st.success(f"✅ Passed — logged in as {resp.json().get('username')}.")
                else:
                    st.error(f"❌ Failed — {resp.json().get('error', 'Login failed.')}")
            except requests.RequestException as e:
                st.error(f"❌ Couldn't reach the API: {e}")

    st.caption("Or log in with different credentials directly (doesn't touch .env):")
    with st.form("login_form"):
        username = st.text_input("Email", placeholder="you@example.com")
        password = st.text_input("Password", type="password", placeholder="••••••••")
        login_submitted = st.form_submit_button("Log In")

    if login_submitted:
        if not username or not password:
            st.error("Both email and password are required.")
        else:
            with st.spinner("Logging in via LinkedIn's API..."):
                try:
                    resp = requests.post(
                        f"{API_URL}/session/login",
                        json={"username": username, "password": password},
                        timeout=30,
                    )
                    if resp.ok:
                        st.success("Logged in — the saved session will be reused by /profile.")
                    else:
                        st.error(resp.json().get("error", "Login failed."))
                except requests.RequestException as e:
                    st.error(f"Couldn't reach the API: {e}")

    st.caption("Or save a session from a manually-obtained cookie, to reuse later without repasting it:")
    with st.form("session_form"):
        saved_li_at = st.text_input("li_at ", type="password")
        saved_jsessionid = st.text_input("JSESSIONID ", type="password")
        session_submitted = st.form_submit_button("Save Session")

    if session_submitted:
        if not saved_li_at or not saved_jsessionid:
            st.error("Both li_at and JSESSIONID are required.")
        else:
            with st.spinner("Checking cookies against LinkedIn..."):
                try:
                    resp = requests.post(
                        f"{API_URL}/session/bootstrap",
                        json={"li_at": saved_li_at, "jsessionid": saved_jsessionid},
                        timeout=30,
                    )
                    if resp.ok:
                        st.success("Session saved — the next /profile call will reuse it.")
                    else:
                        st.error(resp.json().get("error", "Those cookies didn't work."))
                except requests.RequestException as e:
                    st.error(f"Couldn't reach the API: {e}")
